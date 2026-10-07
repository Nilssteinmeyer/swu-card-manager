"""Vision and recognition tests."""
import pytest
import numpy as np
import cv2

from app.vision.detection import (
    detect_card_region,
    correct_perspective,
    preprocess_card_image,
    compute_perceptual_hash,
    hamming_distance,
    _order_corners,
)


def _make_synthetic_card(width=300, height=420):
    """Create a synthetic card-like image (white rectangle on dark background)."""
    img = np.zeros((600, 800, 3), dtype=np.uint8)
    # Card outline (white rectangle)
    x1, y1, x2, y2 = 250, 90, 250 + width, 90 + height
    cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), -1)
    # Add some text
    cv2.putText(img, "TEST CARD", (x1 + 30, y1 + 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.putText(img, "010", (x1 + 30, y2 - 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    return img


class TestDetection:
    def test_order_corners(self):
        pts = np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=np.float32)
        ordered = _order_corners(pts)
        # top-left should be (100,100), top-right (200,100), etc.
        assert np.allclose(ordered[0], [100, 100])
        assert np.allclose(ordered[1], [200, 100])
        assert np.allclose(ordered[2], [200, 200])
        assert np.allclose(ordered[3], [100, 200])

    def test_detect_card_region(self):
        img = _make_synthetic_card()
        corners = detect_card_region(img)
        assert corners is not None
        assert corners.shape == (4, 2)

    def test_correct_perspective(self):
        img = _make_synthetic_card()
        corners = detect_card_region(img)
        if corners is not None:
            warped = correct_perspective(img, corners)
            assert warped.shape[0] == 1050
            assert warped.shape[1] == 750

    def test_preprocess_card_image(self):
        img = _make_synthetic_card()
        result = preprocess_card_image(img)
        assert "card" in result
        assert "title_roi" in result
        assert "number_roi" in result
        assert result["card"].shape[0] == 1050


class TestPerceptualHash:
    def test_identical_images_same_hash(self):
        img1 = _make_synthetic_card()
        img2 = _make_synthetic_card()
        h1 = compute_perceptual_hash(img1)
        h2 = compute_perceptual_hash(img2)
        assert h1 == h2
        assert hamming_distance(h1, h2) == 0

    def test_different_images_different_hash(self):
        img1 = _make_synthetic_card()
        img2 = _make_synthetic_card()
        # Modify second image
        cv2.rectangle(img2, (300, 100), (400, 200), (0, 0, 255), -1)
        h1 = compute_perceptual_hash(img1)
        h2 = compute_perceptual_hash(img2)
        assert hamming_distance(h1, h2) > 0

    def test_hamming_distance(self):
        assert hamming_distance("0000", "0000") == 0
        assert hamming_distance("0000", "ffff") > 0
        assert hamming_distance("0000", "0001") == 1
