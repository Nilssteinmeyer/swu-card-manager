"""
Camera module — captures images from a local webcam or an IP camera URL.
Supports both OpenCV VideoCapture (USB / built-in webcam) and HTTP MJPEG
streams (smartphone IP camera apps like 'IP Webcam').
"""
from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np

from app.core.config import AppConfig
from app.core.logging import get_logger

log = get_logger("camera")


class CameraManager:
    """Manages camera capture from local webcam or IP camera."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.device_index = self.cfg.get("camera.device_index", 0)
        self.ip_url = self.cfg.get("camera.ip_camera_url", "")
        self.width = self.cfg.get("camera.width", 1920)
        self.height = self.cfg.get("camera.height", 1080)
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> bool:
        """Open the camera. Returns True on success."""
        source = self.ip_url if self.ip_url else self.device_index
        log.info(f"Opening camera: {source}", extra={"event": "camera_open"})
        self._cap = cv2.VideoCapture(source)
        if not self._cap.isOpened():
            log.error("Failed to open camera", extra={"event": "camera_fail"})
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        log.info("Camera opened successfully", extra={"event": "camera_ok"})
        return True

    def capture(self) -> np.ndarray | None:
        """Capture a single frame. Returns the image or None."""
        if self._cap is None or not self._cap.isOpened():
            if not self.open():
                return None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            log.warning("Capture returned empty frame", extra={"event": "capture_empty"})
            return None
        return frame

    def capture_stable(self, warmup: int = 5) -> np.ndarray | None:
        """Capture a frame after discarding a few warmup frames.
        This helps with auto-exposure / white-balance settling."""
        for _ in range(warmup):
            self.capture()
            time.sleep(0.05)
        return self.capture()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            log.info("Camera closed", extra={"event": "camera_close"})

    def list_available_cameras(self, max_test: int = 5) -> list[int]:
        """Probe for available camera indices."""
        available = []
        for i in range(max_test):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(i)
                cap.release()
        return available

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
