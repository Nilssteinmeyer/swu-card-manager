"""Model versioning & training pipeline with automated evaluation.

Pipeline (deterministic, journal every step):
  1. SNAPSHOT   dataset-v{n}.zip — confirmed scan photos + reference images,
                SHA256 manifest (reproducible training input)
  2. SPLIT      stratified train/val per card (max val_split_per_card photos)
  3. TRAIN      clip_finetune on the snapshot (GPU, proven config)
  4. EVAL       candidate vs. current active model on THE SAME val split:
                top-1 / top-5 accuracy, mean confidence, latency
  5. GATE        improvement required (auto-promote threshold or manual)
  6. REGISTER   models table entry (version, metrics, params, paths, status)
  7. PROMOTE    explicit (admin click) — sets is_active, never overwrites
                the previous version; rollback = activate older version

A model that is worse is NEVER promoted automatically (and never replaces
the active one). The active version pointer lives in the models table.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import AppConfig
from app.core.logging import AppLogger

AppLogger.setup()
import logging

log = logging.getLogger("swu_manager.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Registry (models table)
# ---------------------------------------------------------------------------


def register_model(
    conn: sqlite3.Connection,
    version: str,
    dataset_ref: str,
    parameters: dict[str, Any],
    metrics: dict[str, Any],
    storage_path: str,
    status: str,
) -> str:
    cur = conn.execute(
        """INSERT INTO models (model_id, version, trained_at, dataset_version,
               parameters, validation_accuracy, recognition_rate,
               confusion_matrix, storage_path, is_active, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (
            f"clip-{version}",
            version,
            datetime.now().isoformat(timespec="seconds"),
            dataset_ref,
            json.dumps(parameters),
            metrics.get("top1", 0.0),
            metrics.get("top5", 0.0),
            json.dumps(metrics.get("per_card", {})),
            storage_path,
            status,
            metrics.get("notes", ""),
        ),
    )
    conn.commit()
    return f"clip-{version}"


def get_active_model(conn: sqlite3.Connection) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM models WHERE is_active = 1 ORDER BY model_id DESC LIMIT 1")
    row = cur.fetchone()
    return dict(row) if row else None


def list_models(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM models ORDER BY version DESC")
    return [dict(r) for r in cur.fetchall()]


def promote_model(conn: sqlite3.Connection, model_id: str) -> bool:
    row = conn.execute("SELECT status FROM models WHERE model_id = ?", (model_id,)).fetchone()
    if not row:
        return False
    if row["status"] == "active" and _is_currently_active(conn, model_id):
        return False  # bereits aktiv -> no-op
    # bisher aktives Modell -> retired (rollback-faehig)
    conn.execute("UPDATE models SET is_active = 0, status = 'retired' WHERE is_active = 1 AND model_id != ?", (model_id,))
    conn.execute("UPDATE models SET is_active = 1, status = 'active' WHERE model_id = ?", (model_id,))
    conn.commit()
    return True




def _is_currently_active(conn: sqlite3.Connection, model_id: str) -> bool:
    row = conn.execute("SELECT is_active FROM models WHERE model_id = ?", (model_id,)).fetchone()
    return bool(row and row["is_active"])


def retire_model(conn: sqlite3.Connection, model_id: str) -> bool:
    conn.execute("UPDATE models SET is_active = 0, status = 'retired' WHERE model_id = ?", (model_id,))
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Evaluation harness
# ---------------------------------------------------------------------------


@dataclass
class EvalSample:
    image_path: Path
    card_id: str
    source: str = "scan"  # scan | reference


@dataclass
class EvalResult:
    top1: float
    top5: float
    mean_confidence: float
    latency_ms: float
    per_card: dict[str, dict[str, float]] = field(default_factory=dict)
    sample_count: int = 0
    notes: str = ""


def build_eval_split(cfg: AppConfig, val_split_per_card: int = 5) -> list[EvalSample]:
    """Confirmed scan photos (the real-world distribution!) + per-card
    reference images, stratified per card."""
    from app.db.schema import connect

    samples: list[EvalSample] = []
    scans_dir = PROJECT_ROOT / "data" / "scans" / "confirmed"
    if scans_dir.exists():
        for p in sorted(scans_dir.glob("*.jpg")):
            # Dateiname: CARDID_TIMESTAMP.jpg
            card_id = p.name.rsplit("_", 2)[0]
            if card_id:
                samples.append(EvalSample(p, card_id, "scan"))

    # Referenzbilder als Baseline-Mindeststandard (max 1 pro Karte, damit
    # Scan-Fotos die Metrik dominieren)
    conn = connect()
    try:
        cards = conn.execute(
            "SELECT card_id, front_art_path FROM cards WHERE front_art_path IS NOT NULL LIMIT 5000"
        ).fetchall()
    finally:
        conn.close()
    seen = {s.card_id for s in samples}
    for row in cards:
        cid = row["card_id"]
        path = Path(row["front_art_path"]) if row["front_art_path"] else None
        if path and path.exists() and cid not in seen:
            samples.append(EvalSample(path, cid, "reference"))
            seen.add(cid)
    return samples


def evaluate_model(model_path: Path | None, samples: list[EvalSample], cfg: AppConfig) -> EvalResult:
    """Run the recognition engine (optionally with a candidate model) over
    the eval split and compute top-1/top-5."""
    import cv2

    from app.recognition import clip_engine as ce

    # Temporarily point the engine at the candidate weights
    original_path = ce.__dict__.get("_finetuned_path_override")
    ce._finetuned_path_override = str(model_path) if model_path else None
    try:
        # Reload model with the override
        ce._clip_model = None  # force reload
        engine = ce.RecognitionEngine(cfg)
        from app.db.schema import connect

        conn = connect()
        try:
            top1_hits, top5_hits, confs, latencies = 0, 0, [], []
            per_card: dict[str, dict[str, float]] = {}
            for s in samples:
                img = cv2.imread(str(s.image_path))
                if img is None:
                    continue
                t0 = time.time()
                result = engine.recognize(img, conn=conn)
                latencies.append((time.time() - t0) * 1000)
                if result.card_id == s.card_id:
                    top1_hits += 1
                cand_ids = [c.get("card_id") for c in (result.candidates or [])][:5]
                if s.card_id in cand_ids:
                    top5_hits += 1
                confs.append(result.confidence)
                stats = per_card.setdefault(s.card_id, {"n": 0, "top1": 0.0})
                stats["n"] += 1
                if result.card_id == s.card_id:
                    stats["top1"] += 1
            n = len(samples)
            for card, stats in per_card.items():
                stats["top1"] = round(stats["top1"] / max(1, stats["n"]), 4)
            return EvalResult(
                top1=round(top1_hits / max(1, n), 4),
                top5=round(top5_hits / max(1, n), 4),
                mean_confidence=round(float(np.mean(confs)) if confs else 0.0, 4),
                latency_ms=round(float(np.mean(latencies)) if latencies else 0.0, 1),
                per_card=per_card,
                sample_count=n,
            )
        finally:
            conn.close()
    finally:
        ce._finetuned_path_override = original_path
        ce._clip_model = None  # restore the active model on next load


# ---------------------------------------------------------------------------
# Dataset snapshot
# ---------------------------------------------------------------------------


def create_dataset_snapshot(version: str) -> Path:
    """Zip confirmed photos + manifest; returns the snapshot path."""
    snapshots_dir = PROJECT_ROOT / "data" / "dataset" / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"dataset-v{version}.zip"
    scans_dir = PROJECT_ROOT / "data" / "scans" / "confirmed"
    manifest: dict[str, Any] = {"version": version, "created": datetime.now().isoformat(), "files": {}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if scans_dir.exists():
            for p in sorted(scans_dir.glob("*.jpg")):
                zf.write(p, p.name)
                manifest["files"][p.name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    log.info(f"Dataset-Snapshot v{version}: {len(manifest['files'])} Fotos -> {path.name}")
    return path


def next_version_number(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM models")
    return cur.fetchone()[0] + 1
