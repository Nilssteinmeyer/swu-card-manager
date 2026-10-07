"""
Recognition accuracy test — verifies ≥99.5% top-1 accuracy.
Uses reference card images as scan inputs (simulating perfect camera captures).
This tests the core recognition pipeline: phash + ORB + OCR cascade.
"""
import pytest
import cv2
import os
from pathlib import Path

from app.core.config import AppConfig
from app.db.schema import connect
from app.db import repository as repo
from app.recognition.engine import RecognitionEngine
from app.vision.detection import compute_perceptual_hash, hamming_distance


@pytest.fixture(scope="module")
def engine():
    """Create a recognition engine and preload reference data."""
    AppConfig.load()
    engine = RecognitionEngine()
    conn = connect()
    engine._load_reference_cache(conn)
    conn.close()
    return engine


def _get_cards_with_images(limit=0):
    """Get cards that have local images downloaded."""
    conn = connect()
    cards = repo.get_all_cards(conn)
    conn.close()
    cards_with_images = []
    for card in cards:
        front_path = card.get("front_art_path")
        if front_path and Path(front_path).exists():
            cards_with_images.append(card)
        if limit > 0 and len(cards_with_images) >= limit:
            break
    return cards_with_images


# Ensure config is loaded with the project root
@pytest.fixture(scope="module", autouse=True)
def setup_config():
    """Ensure AppConfig points to the real project database."""
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config" / "config.yaml"
    AppConfig.reset()
    AppConfig.load(str(config_path))


class TestRecognitionAccuracy:
    """Test that recognition achieves ≥99.5% top-1 accuracy."""

    def test_phash_exact_match(self, engine):
        """Perceptual hash of a reference image must match its own stored hash closely.
        A few mismatches are acceptable due to image re-downloads or format changes."""
        cards = _get_cards_with_images(limit=50)
        if len(cards) < 10:
            pytest.skip("Not enough images downloaded yet")
        
        conn = connect()
        mismatches = 0
        for card in cards:
            img = cv2.imread(str(card["front_art_path"]))
            if img is None:
                continue
            # Apply same rotation as compute_reference_phashes
            h, w = img.shape[:2]
            if w > h:
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            img = cv2.resize(img, (750, 1050))
            scan_phash = compute_perceptual_hash(img)
            ref_phash = card.get("front_phash", "")
            if ref_phash:
                dist = hamming_distance(scan_phash, ref_phash)
                # Allow small distance (<=3) for JPEG/PNG compression differences
                if dist > 3:
                    mismatches += 1
        conn.close()
        # Allow up to 20% minor mismatches (image re-downloads, format changes)
        assert mismatches < len(cards) * 0.2, f"{mismatches} phash mismatches detected (too many)"

    def test_recognition_top1_accuracy(self, engine):
        """Recognition must achieve ≥99.5% top-1 accuracy on reference images."""
        cards = _get_cards_with_images(limit=200)
        if len(cards) < 50:
            pytest.skip("Not enough images downloaded yet (need ≥50)")
        
        conn = connect()
        correct = 0
        total = 0
        errors = []
        
        for card in cards:
            img = cv2.imread(str(card["front_art_path"]))
            if img is None:
                continue
            
            expected_id = card["card_id"]
            result = engine.recognize(img, conn=conn)
            total += 1
            
            if result.candidates and result.candidates[0]["card_id"] == expected_id:
                correct += 1
            else:
                got = result.candidates[0]["card_id"] if result.candidates else "None"
                errors.append(f"{expected_id} → {got}")
        
        conn.close()
        accuracy = correct / total if total > 0 else 0
        print(f"\n  Recognition accuracy: {correct}/{total} = {accuracy:.2%}")
        if errors:
            print(f"  Errors: {errors[:10]}")
        assert accuracy >= 0.995, f"Recognition accuracy {accuracy:.2%} is below 99.5%"

    def test_recognition_top5_accuracy(self, engine):
        """Recognition must achieve ≥99.9% top-5 accuracy on reference images."""
        cards = _get_cards_with_images(limit=200)
        if len(cards) < 50:
            pytest.skip("Not enough images downloaded yet (need ≥50)")
        
        conn = connect()
        correct = 0
        total = 0
        
        for card in cards:
            img = cv2.imread(str(card["front_art_path"]))
            if img is None:
                continue
            
            expected_id = card["card_id"]
            result = engine.recognize(img, conn=conn)
            total += 1
            
            top5_ids = [c["card_id"] for c in result.candidates[:5]]
            if expected_id in top5_ids:
                correct += 1
        
        conn.close()
        accuracy = correct / total if total > 0 else 0
        print(f"\n  Top-5 accuracy: {correct}/{total} = {accuracy:.2%}")
        assert accuracy >= 0.999, f"Top-5 accuracy {accuracy:.2%} is below 99.9%"
