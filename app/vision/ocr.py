"""
OCR module — extracts text from card image regions using Tesseract.
Extracts the card title and card number, which are the primary
identifiers used for matching against the database.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.vision.detection import enhance_for_ocr

log = get_logger("ocr")


class OCREngine:
    """Wraps Tesseract OCR for card text extraction."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.tesseract_path = self.cfg.get("ocr.tesseract_path", "")
        self.language = self.cfg.get("ocr.language", "eng")
        self.min_confidence = self.cfg.get("ocr.min_confidence", 40)
        self._pytesseract = None
        self._init_pytesseract()

    def _init_pytesseract(self):
        try:
            import pytesseract
            if self.tesseract_path and Path(self.tesseract_path).exists():
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
            # Set tessdata directory to local project tessdata (has deu.traineddata)
            tessdata_dir = self.cfg.get("ocr.tessdata_dir", "tessdata")
            tessdata_path = self.cfg.root / tessdata_dir
            if tessdata_path.exists():
                # Tesseract needs the parent dir of tessdata
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
                self._tessdata_prefix = str(tessdata_path)
            else:
                self._tessdata_prefix = None
            self._pytesseract = pytesseract
            log.info(f"Tesseract OCR initialised (lang={self.language}, tessdata={tessdata_path})",
                     extra={"event": "ocr_init"})
        except ImportError:
            log.error("pytesseract not installed", extra={"event": "ocr_fail"})
            self._pytesseract = None
        except Exception as e:
            log.error(f"Tesseract init failed: {e}", extra={"event": "ocr_fail"})
            self._pytesseract = None

    def is_available(self) -> bool:
        return self._pytesseract is not None

    def extract_text(self, image: np.ndarray) -> str:
        """Extract raw text from an image region."""
        if not self.is_available():
            return ""
        enhanced = enhance_for_ocr(image)
        try:
            config = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzÄÖÜäöüß0123456789-'/ "
            if self._tessdata_prefix:
                config = f'--tessdata-dir "{self._tessdata_prefix}" ' + config
            text = self._pytesseract.image_to_string(
                enhanced, lang=self.language, config=config,
            )
            return text.strip()
        except Exception as e:
            log.warning(f"OCR extraction failed: {e}")
            return ""

    def extract_title(self, card_image: np.ndarray) -> str:
        """Extract the card title from the top region of the card.
        Tries multiple PSM modes and returns the longest result.
        Handles both German and English card titles."""
        if not self.is_available():
            return ""
        h, w = card_image.shape[:2]
        # Title is in the top 20% of the card
        title_roi = card_image[int(h * 0.03):int(h * 0.20), int(w * 0.05):int(w * 0.95)]
        enhanced = enhance_for_ocr(title_roi)
        best_text = ""
        for psm in [7, 11, 6]:
            try:
                config = f"--psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzÄÖÜäöüß0123456789-'/ "
                if self._tessdata_prefix:
                    config = f'--tessdata-dir "{self._tessdata_prefix}" ' + config
                text = self._pytesseract.image_to_string(
                    enhanced, lang=self.language, config=config,
                ).strip()
                if len(text) > len(best_text):
                    best_text = text
            except Exception:
                continue
        return best_text

    def extract_card_number(self, card_image: np.ndarray) -> str:
        """Extract the card number from the bottom region of the card.
        SWU cards have the number at the bottom in format 'NNN/NNN' or 'NNN'."""
        if not self.is_available():
            return ""
        h, w = card_image.shape[:2]
        # Bottom-left region
        number_roi = card_image[int(h * 0.90):int(h * 0.99), int(w * 0.01):int(w * 0.30)]
        enhanced = enhance_for_ocr(number_roi)
        try:
            text = self._pytesseract.image_to_string(
                enhanced, lang=self.language, config="--psm 7"
            ).strip()
            # Extract first number sequence
            import re
            match = re.search(r"(\d+)", text)
            return match.group(1) if match else ""
        except Exception:
            return ""

    def extract_all(self, card_image: np.ndarray) -> dict[str, str]:
        """Extract title and card number from a card image."""
        return {
            "title": self.extract_title(card_image),
            "card_number": self.extract_card_number(card_image),
        }

    def extract_detailed(self, image: np.ndarray) -> dict[str, Any]:
        """Extract text with confidence scores."""
        if not self.is_available():
            return {"text": "", "confidence": 0}
        enhanced = enhance_for_ocr(image)
        try:
            data = self._pytesseract.image_to_data(
                enhanced,
                lang=self.language,
                config="--psm 6",
                output_type=self._pytesseract.Output.DICT,
            )
            texts = []
            confidences = []
            for i, txt in enumerate(data["text"]):
                if txt.strip():
                    conf = int(data["conf"][i])
                    if conf >= self.min_confidence:
                        texts.append(txt)
                        confidences.append(conf)
            combined = " ".join(texts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0
            return {"text": combined, "confidence": avg_conf / 100.0}
        except Exception as e:
            log.warning(f"Detailed OCR failed: {e}")
            return {"text": "", "confidence": 0}
