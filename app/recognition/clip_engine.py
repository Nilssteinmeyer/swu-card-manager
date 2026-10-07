"""
CLIP-based card recognition engine — deep learning embeddings.

Uses a pretrained CLIP visual encoder to extract robust 512-dim embeddings
from card images. These embeddings are invariant to lighting, perspective,
background, and partial occlusion — the exact failure modes of classical
feature matching (SIFT/ORB).

Architecture (following the approach used by professional TCG scanner apps):
  1. Pre-compute CLIP embeddings for all reference card images (offline, once)
  2. Store embeddings in a FAISS vector index for fast cosine similarity search
  3. At scan time: embed the camera image → FAISS search → top-K candidates
  4. Re-rank with perceptual hash + OCR as supplementary signals
  5. Cascade scoring with CLIP similarity as the dominant signal

The key insight: CLIP embeddings close the "domain gap" between clean digital
card images and real phone photos, because CLIP was trained on diverse image
data and learns semantic visual features rather than pixel-level patterns.
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

log = get_logger("recognition_clip")

# Lazy-loaded globals (heavy models)
_clip_model = None
_clip_preprocess = None
_clip_device = None
_clip_embed_dim = 512
_faiss_index = None
_ref_card_ids: list[str] = []  # FAISS index → card_id mapping
_ref_metadata: dict[str, dict] = {}  # card_id → metadata
_index_built = False


def _load_clip_model():
    """Lazily load the CLIP visual model and preprocessing.
    Loads the fine-tuned model if available, otherwise the pretrained base."""
    global _clip_model, _clip_preprocess, _clip_device
    if _clip_model is not None:
        return

    import torch
    import open_clip
    from PIL import Image

    _clip_device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Loading CLIP model on {_clip_device}...", extra={"event": "clip_load"})

    # Use ViT-B/32 — good balance of accuracy and speed (512-dim embeddings)
    cfg = AppConfig.load()
    models_dir = cfg.path("models_dir")
    finetuned_path = models_dir / "clip_finetuned.pt"
    has_finetuned = finetuned_path.exists()

    # If we have fine-tuned weights, create the model WITHOUT downloading
    # pretrained weights (pretrained=False → random init, immediately
    # overwritten by our state dict). Avoids any network dependency.
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained=(None if has_finetuned else "openai")
    )

    # Load fine-tuned weights if available
    if has_finetuned:
        log.info(f"Loading fine-tuned weights from {finetuned_path}", extra={"event": "clip_finetune_load"})
        state_dict = torch.load(str(finetuned_path), map_location=_clip_device, weights_only=True)
        model.load_state_dict(state_dict)
        log.info("Fine-tuned CLIP model loaded", extra={"event": "clip_finetune_loaded"})
    else:
        log.info("Using pretrained CLIP (no fine-tuned model found)")

    model = model.to(_clip_device)
    model.eval()
    _clip_model = model
    _clip_preprocess = preprocess
    log.info(f"CLIP model loaded (ViT-B-32, dim={_clip_embed_dim})", extra={"event": "clip_loaded"})


def _embed_image(image: np.ndarray) -> np.ndarray | None:
    """Embed an image into CLIP's 512-dim vector space."""
    if _clip_model is None:
        _load_clip_model()

    import torch
    from PIL import Image

    try:
        # Convert BGR (OpenCV) → RGB (PIL)
        if len(image.shape) == 3:
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            image_rgb = image
        pil_img = Image.fromarray(image_rgb)
        preprocessed = _clip_preprocess(pil_img).unsqueeze(0).to(_clip_device)

        with torch.no_grad():
            features = _clip_model.encode_image(preprocessed)
            # L2 normalize
            features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().numpy().astype(np.float32).flatten()
    except Exception as e:
        log.warning(f"CLIP embedding failed: {e}")
        return None


def build_faiss_index(config: AppConfig | None = None, force: bool = False) -> int:
    """Pre-compute CLIP embeddings for all reference cards and build FAISS index.
    This is the key offline step — run once, then the index is reused for every scan.

    Returns the number of indexed cards.
    """
    global _faiss_index, _ref_card_ids, _ref_metadata, _index_built

    if _index_built and not force:
        return len(_ref_card_ids)

    import faiss

    cfg = config or AppConfig.load()
    models_dir = cfg.path("models_dir")
    models_dir.mkdir(parents=True, exist_ok=True)

    index_path = models_dir / "clip_faiss.index"
    metadata_path = models_dir / "clip_metadata.json"

    # Check if pre-built index exists
    if index_path.exists() and metadata_path.exists() and not force:
        log.info("Loading pre-built FAISS index...", extra={"event": "faiss_load"})
        _faiss_index = faiss.read_index(str(index_path))
        with open(metadata_path, "r") as f:
            meta = json.load(f)
        _ref_card_ids = meta["card_ids"]
        _ref_metadata = meta["metadata"]
        _index_built = True
        log.info(f"FAISS index loaded: {len(_ref_card_ids)} cards", extra={"event": "faiss_loaded"})
        return len(_ref_card_ids)

    # Build index from scratch
    _load_clip_model()

    conn = connect()
    cards = repo.get_all_cards(conn)
    conn.close()

    # Filter to cards with local images
    cards_with_images = []
    for card in cards:
        front_path = card.get("front_art_path", "")
        if front_path and Path(front_path).exists():
            cards_with_images.append(card)

    log.info(f"Building CLIP embeddings for {len(cards_with_images)} cards...",
             extra={"event": "clip_build_start"})

    embeddings = []
    card_ids = []
    metadata = {}

    import torch

    batch_size = 32
    for i in range(0, len(cards_with_images), batch_size):
        batch = cards_with_images[i:i + batch_size]
        batch_images = []
        batch_ids = []

        for card in batch:
            img = cv2.imread(str(card["front_art_path"]))
            if img is None:
                continue
            # Normalize: rotate landscape to portrait
            h, w = img.shape[:2]
            if w > h:
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            img = cv2.resize(img, (224, 224))  # CLIP input size
            batch_images.append(img)
            batch_ids.append(card["card_id"])
            metadata[card["card_id"]] = {
                "name": card["name"],
                "name_de": card.get("name_de", "") or card["name"],
                "subtitle": card.get("subtitle", ""),
                "set_id": card["set_id"],
                "card_number": card["card_number"],
                "front_phash": card.get("front_phash", ""),
                "front_art_path": card["front_art_path"],
            }

        if not batch_images:
            continue

        # Process batch
        try:
            from PIL import Image
            preprocessed_batch = []
            for img in batch_images:
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(img_rgb)
                preprocessed_batch.append(_clip_preprocess(pil_img))

            tensor_batch = torch.stack(preprocessed_batch).to(_clip_device)
            with torch.no_grad():
                features = _clip_model.encode_image(tensor_batch)
                features = features / features.norm(dim=-1, keepdim=True)

            for j, card_id in enumerate(batch_ids):
                emb = features[j].cpu().numpy().astype(np.float32).reshape(1, -1)
                embeddings.append(emb)
                card_ids.append(card_id)

        except Exception as e:
            log.warning(f"Batch {i//batch_size} failed: {e}")
            continue

        if (i // batch_size) % 10 == 0:
            log.info(f"  Processed {i + len(batch)}/{len(cards_with_images)} cards",
                     extra={"event": "clip_build_progress"})

    if not embeddings:
        log.error("No embeddings generated!", extra={"event": "clip_build_fail"})
        return 0

    # Build FAISS index (cosine similarity = inner product on L2-normalized vectors)
    dim = embeddings[0].shape[1]
    all_embeddings = np.vstack(embeddings).astype(np.float32)
    _faiss_index = faiss.IndexFlatIP(dim)
    _faiss_index.add(all_embeddings)
    _ref_card_ids = card_ids
    _ref_metadata = metadata
    _index_built = True

    # Save to disk
    faiss.write_index(_faiss_index, str(index_path))
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump({"card_ids": card_ids, "metadata": metadata}, f)

    log.info(f"FAISS index built: {len(card_ids)} cards, dim={dim}, saved to {index_path}",
             extra={"event": "faiss_built"})
    return len(card_ids)


@dataclass
class RecognitionResult:
    """Result of a card recognition attempt."""
    card_id: str | None
    card_name: str
    confidence: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    method: str = "clip_faiss"
    ocr_text: str = ""
    processing_time_ms: int = 0
    image_path: str | None = None


class RecognitionEngine:
    """CLIP-based card recognition engine with FAISS vector search.

    This replaces the SIFT/ORB approach with deep learning embeddings that
    are robust to lighting, perspective, background, and partial occlusion.
    """

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.ocr = OCREngine(self.cfg)
        self.auto_accept = self.cfg.get("recognition.auto_accept_threshold", 0.70)
        self.candidate_threshold = self.cfg.get("recognition.candidate_threshold", 0.25)

        # Load FAISS index (build if needed)
        if not _index_built:
            build_faiss_index(self.cfg)

    def recognize(
        self,
        image: np.ndarray,
        conn: sqlite3.Connection | None = None,
        save_path: str | None = None,
    ) -> RecognitionResult:
        """Recognise a card using CLIP embeddings + FAISS search."""
        start = time.time()
        own_conn = conn is None
        if own_conn:
            conn = connect()

        try:
            if not _index_built or _faiss_index is None:
                return RecognitionResult(
                    card_id=None, card_name="Error: No index",
                    confidence=0.0, processing_time_ms=0,
                )

            # Step 1: Normalize to card dimensions
            h, w = image.shape[:2]
            aspect = h / w if w > 0 else 0
            if abs(h - 1050) < 100 and abs(w - 750) < 100:
                card_img = cv2.resize(image, (750, 1050))
            elif abs(h - 750) < 100 and abs(w - 1050) < 100:
                card_img = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                card_img = cv2.resize(card_img, (750, 1050))
            elif 0.6 < aspect < 1.8 and h > 200 and w > 200:
                if aspect < 1.0:
                    card_img = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                else:
                    card_img = image
                card_img = cv2.resize(card_img, (750, 1050))
            else:
                preprocessed = preprocess_card_image(image)
                card_img = preprocessed["card"]

            # Step 2: CLIP embedding of the scan
            scan_embedding = _embed_image(card_img)
            if scan_embedding is None:
                return RecognitionResult(
                    card_id=None, card_name="Embedding failed",
                    confidence=0.0, processing_time_ms=int((time.time() - start) * 1000),
                )

            # Step 3: FAISS cosine similarity search (top-10)
            query = scan_embedding.reshape(1, -1).astype(np.float32)
            k = min(10, _faiss_index.ntotal)
            scores, indices = _faiss_index.search(query, k)

            # Step 4: OCR (supplementary signal)
            ocr_result = self.ocr.extract_all(card_img)
            ocr_title = ocr_result["title"]
            ocr_number = ocr_result["card_number"]
            ocr_text = f"{ocr_title} {ocr_number}".strip()

            # Step 5: phash for re-ranking
            scan_phash = compute_perceptual_hash(card_img)

            # Step 6: Score and rank candidates
            candidates = []
            for rank, (score, idx) in enumerate(zip(scores[0], indices[0])):
                if idx < 0 or idx >= len(_ref_card_ids):
                    continue
                card_id = _ref_card_ids[idx]
                ref = _ref_metadata.get(card_id, {})
                clip_score = float(score)  # cosine similarity (0-1 for normalized vectors)

                # phash re-ranking
                ref_phash = ref.get("front_phash", "")
                if scan_phash and ref_phash:
                    max_bits = len(scan_phash) * 4
                    phash_dist = hamming_distance(scan_phash, ref_phash)
                    phash_score = 1.0 - (phash_dist / max_bits)
                else:
                    phash_score = 0.0

                # OCR title matching (bilingual)
                ocr_score = 0.0
                if ocr_title:
                    if ref.get("name"):
                        ocr_score = fuzz.partial_ratio(ocr_title.lower(), ref["name"].lower()) / 100.0
                    if ref.get("name_de") and ref["name_de"] != ref.get("name"):
                        de_score = fuzz.partial_ratio(ocr_title.lower(), ref["name_de"].lower()) / 100.0
                        ocr_score = max(ocr_score, de_score)

                # Metadata (card number)
                meta_score = 0.0
                if ocr_number and ref.get("card_number"):
                    meta_score = 1.0 if ocr_number == ref["card_number"] else 0.0

                # Cascade scoring — CLIP is dominant
                # CLIP cosine similarity > 0.85 → very confident
                # CLIP > 0.75 → confident
                # CLIP > 0.65 → candidate, supplement with phash/OCR
                if clip_score >= 0.90:
                    total = min(1.0, clip_score * 0.85 + phash_score * 0.10 + ocr_score * 0.05)
                elif clip_score >= 0.80:
                    total = min(1.0, clip_score * 0.70 + phash_score * 0.15 + ocr_score * 0.10 + meta_score * 0.05)
                elif clip_score >= 0.70:
                    total = min(1.0, clip_score * 0.60 + phash_score * 0.20 + ocr_score * 0.15 + meta_score * 0.05)
                elif clip_score >= 0.60:
                    total = min(1.0, clip_score * 0.50 + phash_score * 0.25 + ocr_score * 0.20 + meta_score * 0.05)
                else:
                    total = min(1.0, clip_score * 0.40 + phash_score * 0.30 + ocr_score * 0.25 + meta_score * 0.05)

                # Boost for exact number match + OCR
                if meta_score > 0.9 and ocr_score > 0.7:
                    total = min(1.0, total + 0.15)

                candidates.append({
                    "card_id": card_id,
                    "name": ref.get("name", ""),
                    "subtitle": ref.get("subtitle", ""),
                    "set_id": ref.get("set_id", ""),
                    "card_number": ref.get("card_number", ""),
                    "score": round(total, 4),
                    "signal_scores": {
                        "clip": round(clip_score, 4),
                        "perceptual_hash": round(phash_score, 4),
                        "ocr": round(ocr_score, 4),
                        "metadata": round(meta_score, 4),
                    },
                    "clip_similarity": round(clip_score, 4),
                    "rank": rank,
                    "method": "clip_faiss",
                })

            # Sort by total score
            candidates.sort(key=lambda c: c["score"], reverse=True)

            elapsed_ms = int((time.time() - start) * 1000)

            # Determine result
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
                    card_id=None,
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
                f"Recognition: {result.card_name} ({result.confidence:.2%}) in {elapsed_ms}ms "
                f"[CLIP: {candidates[0]['clip_similarity']:.3f}]" if candidates else
                f"Recognition: Unknown in {elapsed_ms}ms",
                extra={"event": "recognition", "card_id": result.card_id or ""},
            )
            return result

        finally:
            if own_conn:
                conn.close()

    def compute_reference_phashes(self, conn: sqlite3.Connection) -> int:
        """Compute and store perceptual hashes for all cards with images."""
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
            h, w = img.shape[:2]
            if w > h:
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            img = cv2.resize(img, (750, 1050))
            phash = compute_perceptual_hash(img)
            repo.update_card_phash(conn, card["card_id"], phash)
            count += 1
        log.info(f"Computed {count} perceptual hashes", extra={"event": "phash_compute"})
        return count

    def learn_from_correction(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        original_card_id: str,
        corrected_card_id: str,
        image_path: str | None = None,
    ) -> None:
        """Learn from a user correction."""
        repo.record_scan_correction(conn, scan_id, original_card_id, corrected_card_id, image_path)
        if image_path and Path(image_path).exists():
            from app.ml.dataset import DatasetManager
            img = cv2.imread(str(image_path))
            if img is not None:
                ds = DatasetManager(self.cfg)
                ds.add_scan_sample(img, corrected_card_id, 1.0, source="correction")
                log.info(f"Learned from correction: {original_card_id} → {corrected_card_id}",
                         extra={"event": "learn_correction", "card_id": corrected_card_id})
        count = repo.get_correction_count(conn)
        log.info(f"Total corrections: {count}", extra={"event": "correction_count"})
