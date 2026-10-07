"""
Multi-signal card recognition engine — v2 with SIFT+FLANN+RANSAC.

Designed for REAL camera photos, not just perfect reference images.
Combines multiple independent signals to identify a card:

  1. Perceptual hash matching — visual similarity (fast pre-filter)
  2. SIFT + FLANN + RANSAC — robust feature matching under perspective change
  3. OCR title matching — card title text (fuzzy, bilingual DE+EN)
  4. Metadata matching — card number extraction

The cascade approach:
  - phash pre-filter to top-100 candidates (fast, O(n) on 256-bit hashes)
  - SIFT+FLANN+RANSAC on top candidates (robust, handles rotation/perspective)
  - OCR as a supplementary signal (unreliable on camera photos, but helps)
  - Weighted combination with RANSAC inlier count as the dominant signal

This is the gold standard for object recognition under perspective change.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from rapidfuzz import fuzz

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.schema import connect
from app.vision.detection import (
    compute_perceptual_hash,
    hamming_distance,
    preprocess_card_image,
)
from app.vision.ocr import OCREngine

log = get_logger("recognition")


@dataclass
class RecognitionResult:
    """Result of a card recognition attempt."""
    card_id: str | None
    card_name: str
    confidence: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    method: str = "sift_flann_ransac"
    ocr_text: str = ""
    processing_time_ms: int = 0
    image_path: str | None = None


# FLANN parameters for SIFT (KD-tree for float descriptors)
FLANN_INDEX_KDTREE = 1
_flann_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=8)
_flann_search_params = dict(checks=64)


class RecognitionEngine:
    """Multi-signal card recognition engine with SIFT+FLANN+RANSAC.

    SIFT features are scale and rotation invariant — far more robust than
    ORB for matching real camera photos against reference images.
    FLANN provides fast approximate nearest-neighbor matching.
    RANSAC homography validates geometric consistency between matches.
    """

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.ocr = OCREngine(self.cfg)
        self.auto_accept = self.cfg.get("recognition.auto_accept_threshold", 0.70)
        self.candidate_threshold = self.cfg.get("recognition.candidate_threshold", 0.25)

        # SIFT detector — scale and rotation invariant, far more robust than ORB
        self.sift = cv2.SIFT_create(nfeatures=1000)
        # FLANN matcher — fast approximate nearest neighbor for SIFT's float descriptors
        self.flann = cv2.FlannBasedMatcher(_flann_params, _flann_search_params)

        # Reference caches
        self._ref_cache: dict[str, dict[str, Any]] = {}
        self._feat_cache: dict[str, tuple] = {}  # path -> (kp, desc) SIFT cache
        self._cache_loaded = False

    def _load_reference_cache(self, conn: sqlite3.Connection) -> None:
        """Load all card metadata + phash for fast pre-filtering."""
        if self._cache_loaded:
            return
        cards = repo.get_all_cards(conn)
        for card in cards:
            card_id = card["card_id"]
            self._ref_cache[card_id] = {
                "name": card["name"],
                "name_de": card.get("name_de", "") or card["name"],
                "subtitle": card.get("subtitle", ""),
                "subtitle_de": card.get("subtitle_de", "") or card.get("subtitle", ""),
                "set_id": card["set_id"],
                "card_number": card["card_number"],
                "front_phash": card.get("front_phash", ""),
                "front_art_path": card.get("front_art_path", ""),
            }
        self._cache_loaded = True
        log.info(f"Reference cache loaded: {len(self._ref_cache)} cards",
                 extra={"event": "cache_loaded"})

    def _get_sift_features(self, image_path: str) -> tuple | None:
        """Load and cache SIFT features for a reference image."""
        if image_path in self._feat_cache:
            return self._feat_cache[image_path]
        p = Path(image_path)
        if not p.exists():
            return None
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        h, w = img.shape[:2]
        if w > h:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        img = cv2.resize(img, (750, 1050))
        kp, desc = self.sift.detectAndCompute(img, None)
        if desc is None or len(kp) < 10:
            return None
        self._feat_cache[image_path] = (kp, desc)
        return (kp, desc)

    def recognize(
        self,
        image: np.ndarray,
        conn: sqlite3.Connection | None = None,
        save_path: str | None = None,
    ) -> RecognitionResult:
        """Recognise a card from a camera image.
        Returns a RecognitionResult with the best match and candidates."""
        start = time.time()
        own_conn = conn is None
        if own_conn:
            conn = connect()

        try:
            self._load_reference_cache(conn)

            # Step 1: Preprocess — detect card, correct perspective
            # If image is already close to card dimensions, skip detection and just resize
            h, w = image.shape[:2]
            aspect = h / w if w > 0 else 0
            # Card aspect ratio is ~1.4 (h/w). Images may be portrait or landscape.
            # Portrait: h/w ≈ 1.4, Landscape: h/w ≈ 0.7 (card rotated 90°)
            if abs(h - 1050) < 100 and abs(w - 750) < 100:
                # Already card-sized portrait — just resize to exact dimensions
                card_img = cv2.resize(image, (750, 1050))
            elif abs(h - 750) < 100 and abs(w - 1050) < 100:
                # Card-sized landscape — rotate 90° clockwise then resize
                card_img = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                card_img = cv2.resize(card_img, (750, 1050))
            elif (h > 400 and w > 400) and (0.6 < aspect < 1.8):
                # Card-like image (portrait or landscape) — resize directly
                if aspect < 1.0:
                    # Landscape — rotate 90°
                    card_img = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                else:
                    card_img = image
                card_img = cv2.resize(card_img, (750, 1050))
            else:
                # Full scene — detect and correct perspective
                preprocessed = preprocess_card_image(image)
                card_img = preprocessed["card"]

            # Step 2: OCR
            ocr_result = self.ocr.extract_all(card_img)
            ocr_title = ocr_result["title"]
            ocr_number = ocr_result["card_number"]
            ocr_text = f"{ocr_title} {ocr_number}".strip()

            # Step 3: Compute perceptual hash of scanned card
            scan_phash = compute_perceptual_hash(card_img)

            # Step 4: Compute SIFT features of the scan
            gray = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
            scan_kp, scan_desc = self.sift.detectAndCompute(gray, None)

            # Step 5: phash pre-filter — get top 100 candidates
            max_bits = len(scan_phash) * 4 if scan_phash else 256
            phash_scores = []
            ref_items = list(self._ref_cache.items())
            for card_id, ref in ref_items:
                ref_phash = ref.get("front_phash", "")
                if scan_phash and ref_phash:
                    dist = hamming_distance(scan_phash, ref_phash)
                    score = 1.0 - (dist / max_bits)
                    phash_scores.append((card_id, ref, dist, score))
                else:
                    phash_scores.append((card_id, ref, max_bits, 0.0))

            phash_scores.sort(key=lambda x: x[3], reverse=True)
            # Keep top 30 candidates for SIFT matching (balance speed vs accuracy)
            # If there's an exact phash match, only consider those
            exact = [r for r in phash_scores if r[2] == 0]
            top_candidates = exact if exact else phash_scores[:30]

            # Step 6: SIFT+FLANN+RANSAC matching on top candidates
            candidates = []
            for card_id, ref, phash_dist, phash_score in top_candidates:
                scores: dict[str, float] = {}
                ref_art_path = ref.get("front_art_path", "")

                # phash score
                if phash_dist == 0:
                    scores["perceptual_hash"] = 1.0
                elif phash_dist <= 3:
                    scores["perceptual_hash"] = 0.95
                else:
                    scores["perceptual_hash"] = phash_score

                # SIFT+FLNN+RANSAC — the dominant signal for real photos
                sift_score = 0.0
                ransac_inliers = 0
                if scan_desc is not None and ref_art_path:
                    ref_feat = self._get_sift_features(ref_art_path)
                    if ref_feat is not None:
                        ref_kp, ref_desc = ref_feat
                        try:
                            matches = self.flann.knnMatch(scan_desc, ref_desc, k=2)
                        except cv2.error:
                            matches = []

                        # Lowe's ratio test — filters ambiguous matches
                        good_matches = []
                        for m_pair in matches:
                            if len(m_pair) >= 2:
                                m, n = m_pair[0], m_pair[1]
                                if m.distance < 0.75 * n.distance:
                                    good_matches.append(m)

                        # RANSAC homography — validates geometric consistency
                        if len(good_matches) >= 8:
                            src_pts = np.float32(
                                [scan_kp[m.queryIdx].pt for m in good_matches]
                            ).reshape(-1, 1, 2)
                            dst_pts = np.float32(
                                [ref_kp[m.trainIdx].pt for m in good_matches]
                            ).reshape(-1, 1, 2)

                            H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                            if mask is not None:
                                ransac_inliers = int(np.sum(mask))
                                sift_score = ransac_inliers / max(len(good_matches), 1)
                                if ransac_inliers >= 20:
                                    sift_score = min(1.0, sift_score * 1.3)
                                elif ransac_inliers >= 10:
                                    sift_score = min(1.0, sift_score * 1.1)
                scores["sift_ransac"] = sift_score

                # OCR title matching (bilingual)
                if ocr_title and (ref["name"] or ref.get("name_de")):
                    title_score = 0.0
                    if ref["name"]:
                        title_score = fuzz.partial_ratio(ocr_title.lower(), ref["name"].lower()) / 100.0
                        if ref.get("subtitle"):
                            sub_score = fuzz.partial_ratio(ocr_title.lower(), ref["subtitle"].lower()) / 100.0
                            title_score = max(title_score, sub_score * 0.8)
                    if ref.get("name_de") and ref["name_de"] != ref["name"]:
                        de_score = fuzz.partial_ratio(ocr_title.lower(), ref["name_de"].lower()) / 100.0
                        if ref.get("subtitle_de"):
                            de_sub = fuzz.partial_ratio(ocr_title.lower(), ref["subtitle_de"].lower()) / 100.0
                            de_score = max(de_score, de_sub * 0.8)
                        title_score = max(title_score, de_score)
                    scores["ocr"] = title_score
                else:
                    scores["ocr"] = 0.0

                # Card number matching
                if ocr_number and ref["card_number"]:
                    scores["metadata"] = 1.0 if ocr_number == ref["card_number"] else 0.0
                else:
                    scores["metadata"] = 0.0

                # Cascade scoring — SIFT+RANSAC dominant
                if scores["perceptual_hash"] >= 1.0:
                    total = 1.0
                elif scores["sift_ransac"] >= 0.5 and ransac_inliers >= 15:
                    total = min(1.0, scores["sift_ransac"] * 0.50 + scores["perceptual_hash"] * 0.25 +
                               scores["ocr"] * 0.15 + scores["metadata"] * 0.10)
                elif scores["sift_ransac"] >= 0.3 and ransac_inliers >= 8:
                    total = min(1.0, scores["sift_ransac"] * 0.45 + scores["perceptual_hash"] * 0.25 +
                               scores["ocr"] * 0.20 + scores["metadata"] * 0.10)
                elif scores["perceptual_hash"] >= 0.85 and scores["sift_ransac"] >= 0.2:
                    total = min(1.0, scores["perceptual_hash"] * 0.40 + scores["sift_ransac"] * 0.35 +
                               scores["ocr"] * 0.15 + scores["metadata"] * 0.10)
                elif scores.get("metadata", 0) > 0.9 and scores.get("ocr", 0) >= 0.5:
                    total = min(1.0, scores["ocr"] * 0.45 + scores["metadata"] * 0.35 +
                               scores["perceptual_hash"] * 0.15 + scores["sift_ransac"] * 0.05)
                else:
                    total = (scores["sift_ransac"] * 0.40 + scores["perceptual_hash"] * 0.25 +
                            scores["ocr"] * 0.25 + scores["metadata"] * 0.10)

                if total >= self.candidate_threshold * 0.3:
                    candidates.append({
                        "card_id": card_id,
                        "name": ref["name"],
                        "subtitle": ref.get("subtitle", ""),
                        "set_id": ref["set_id"],
                        "card_number": ref["card_number"],
                        "score": round(total, 4),
                        "signal_scores": {k: round(v, 4) for k, v in scores.items()},
                        "ransac_inliers": ransac_inliers,
                        "method": "sift_flann_ransac",
                    })

            # Step 6: Rank candidates
            candidates.sort(key=lambda c: c["score"], reverse=True)

            elapsed_ms = int((time.time() - start) * 1000)

            # Step 7: Determine result
            if candidates and candidates[0]["score"] >= self.auto_accept:
                best = candidates[0]
                result = RecognitionResult(
                    card_id=best["card_id"],
                    card_name=best["name"],
                    confidence=best["score"],
                    candidates=candidates[:5],
                    ocr_text=ocr_text,
                    processing_time_ms=elapsed_ms,
                    image_path=save_path,
                )
            elif candidates and candidates[0]["score"] >= self.candidate_threshold:
                best = candidates[0]
                result = RecognitionResult(
                    card_id=None,  # not auto-accepted
                    card_name=best["name"],
                    confidence=best["score"],
                    candidates=candidates[:5],
                    ocr_text=ocr_text,
                    processing_time_ms=elapsed_ms,
                    image_path=save_path,
                )
            else:
                result = RecognitionResult(
                    card_id=None,
                    card_name="Unknown",
                    confidence=0.0,
                    candidates=candidates[:5],
                    ocr_text=ocr_text,
                    processing_time_ms=elapsed_ms,
                    image_path=save_path,
                )

            log.info(
                f"Recognition: {result.card_name} ({result.confidence:.2%}) in {elapsed_ms}ms",
                extra={"event": "recognition", "card_id": result.card_id or ""},
            )
            return result

        finally:
            if own_conn:
                conn.close()

    def compute_reference_phashes(self, conn: sqlite3.Connection) -> int:
        """Compute and store perceptual hashes for all cards with images.
        Normalises images to 750x1050 (portrait), rotating landscape images
        90° clockwise first, to ensure consistent hash comparison."""
        cards = repo.get_all_cards(conn)
        count = 0
        for card in cards:
            if card.get("front_phash"):
                continue
            front_path = card.get("front_art_path")
            if not front_path or not Path(front_path).exists():
                continue
            img = cv2.imread(str(front_path))
            if img is None:
                continue
            # If image is landscape (w > h), rotate 90° clockwise to portrait
            h, w = img.shape[:2]
            if w > h:
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            # Normalise to the standard card size
            img = cv2.resize(img, (750, 1050))
            phash = compute_perceptual_hash(img)
            repo.update_card_phash(conn, card["card_id"], phash)
            count += 1
        log.info(f"Computed {count} perceptual hashes", extra={"event": "phash_compute"})
        # Invalidate cache
        self._cache_loaded = False
        return count

    def learn_from_correction(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        original_card_id: str,
        corrected_card_id: str,
        image_path: str | None = None,
    ) -> None:
        """Learn from a user correction: store the correction and add the
        corrected card image to the dataset for future training."""
        # Record the correction
        repo.record_scan_correction(conn, scan_id, original_card_id, corrected_card_id, image_path)

        # If we have the scan image, add it to the dataset for the CORRECTED card
        if image_path and Path(image_path).exists():
            from app.ml.dataset import DatasetManager
            img = cv2.imread(str(image_path))
            if img is not None:
                ds = DatasetManager(self.cfg)
                ds.add_scan_sample(img, corrected_card_id, 1.0, source="correction")
                log.info(f"Learned from correction: {original_card_id} → {corrected_card_id}",
                         extra={"event": "learn_correction", "card_id": corrected_card_id})

        # Count total corrections
        count = repo.get_correction_count(conn)
        log.info(f"Total corrections: {count}", extra={"event": "correction_count"})
