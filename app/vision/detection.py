"""
Card detection and image preprocessing.
Detects a card-like quadrilateral in the image, corrects perspective,
and crops to a normalised card image.

SWU cards have aspect ratio ~ 2.5:3.5 (63x88mm).
Uses contour detection + perspective transform.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger("detection")

# Standard card dimensions (pixels) for normalised output
CARD_W = 750
CARD_H = 1050
CARD_ASPECT = CARD_H / CARD_W  # ~1.4


def detect_card_region(image: np.ndarray) -> np.ndarray | None:
    """Detect the largest card-like quadrilateral in the image.
    Returns the 4 corner points (clockwise from top-left) or None."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)

    # Dilate to close gaps
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edged = cv2.dilate(edged, kernel, iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Sort by area, descending
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[:5]:
        area = cv2.contourArea(contour)
        if area < 10000:  # too small
            continue

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

        if len(approx) == 4:
            corners = _order_corners(approx.reshape(4, 2))
            # Check aspect ratio is roughly card-like
            w1 = np.linalg.norm(corners[0] - corners[1])
            w2 = np.linalg.norm(corners[2] - corners[3])
            h1 = np.linalg.norm(corners[0] - corners[3])
            h2 = np.linalg.norm(corners[1] - corners[2])
            avg_w = (w1 + w2) / 2
            avg_h = (h1 + h2) / 2
            if avg_w < 10 or avg_h < 10:
                continue
            ratio = max(avg_h, avg_w) / min(avg_h, avg_w)
            if 1.1 < ratio < 1.8:  # card-like aspect ratio
                return corners

    # Fallback: use bounding rect of largest contour
    if contours:
        c = contours[0]
        rect = cv2.minAreaRect(c)
        box = cv2.boxPoints(rect)
        return _order_corners(box)

    return None


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]   # top-left has smallest sum
    rect[2] = pts[np.argmax(s)]   # bottom-right has largest sum
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect


def correct_perspective(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Apply perspective correction to produce a flat, normalised card image."""
    src = np.array(corners, dtype=np.float32)
    dst = np.array([
        [0, 0],
        [CARD_W - 1, 0],
        [CARD_W - 1, CARD_H - 1],
        [0, CARD_H - 1],
    ], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(image, matrix, (CARD_W, CARD_H))
    return warped


def preprocess_card_image(image: np.ndarray) -> dict[str, np.ndarray]:
    """Take a raw camera frame, detect the card, correct perspective,
    and return both the full corrected image and ROI crops for OCR.

    Returns dict with:
      'card'      — full perspective-corrected card image
      'title_roi'  — top portion for title OCR
      'number_roi' — bottom-left for card number OCR
      'full_roi'   — entire card for image matching
    If no card is detected, returns the original image resized.
    """
    corners = detect_card_region(image)
    if corners is not None:
        card = correct_perspective(image, corners)
    else:
        # Fallback: just resize the image
        log.debug("No card region detected, using resized original")
        card = cv2.resize(image, (CARD_W, CARD_H))

    # Extract ROIs
    # Title is typically in the top 15% of the card
    title_roi = card[int(CARD_H * 0.03):int(CARD_H * 0.15), :]
    # Card number is bottom-left
    number_roi = card[int(CARD_H * 0.92):int(CARD_H * 0.98), int(CARD_W * 0.02):int(CARD_W * 0.25)]

    return {
        "card": card,
        "title_roi": title_roi,
        "number_roi": number_roi,
        "full_roi": card,
    }


def enhance_for_ocr(roi: np.ndarray) -> np.ndarray:
    """Enhance an image region for better OCR results."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi
    # Upscale for better OCR on small text
    h, w = gray.shape[:2]
    if max(h, w) < 500:
        scale = 500 / max(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # Binarise with Otsu
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Denoise
    binary = cv2.fastNlMeansDenoising(binary, h=10)
    return binary


def compute_perceptual_hash(image: np.ndarray, hash_size: int = 16) -> str:
    """Compute a perceptual hash of the image as a hex string.
    Uses a dHash-style algorithm implemented with OpenCV for speed."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    # dHash: difference between adjacent pixels
    diff = resized[:, 1:] > resized[:, :-1]
    bits = diff.flatten()
    # Convert to hex
    hex_str = ""
    for i in range(0, len(bits), 4):
        nibble = 0
        for j in range(4):
            if i + j < len(bits) and bits[i + j]:
                nibble |= (1 << (3 - j))
        hex_str += f"{nibble:x}"
    return hex_str


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute the Hamming distance between two hex perceptual hashes."""
    if len(hash1) != len(hash2):
        return max(len(hash1), len(hash2)) * 4
    n1 = int(hash1, 16)
    n2 = int(hash2, 16)
    return bin(n1 ^ n2).count("1")
