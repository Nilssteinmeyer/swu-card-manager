"""Tests for the model versioning pipeline (registry, snapshot, gating)."""
import json
import sqlite3
from pathlib import Path

import pytest

from app.db.schema import connect, init_database
from app.ml import pipeline as pl


@pytest.fixture()
def db(tmp_path):
    conn = connect(Path(tmp_path) / "test.db")
    init_database(conn)
    yield conn
    conn.close()


def test_register_and_list_models(db):
    mid = pl.register_model(
        db, version="1", dataset_ref="dataset-v1",
        parameters={"epochs": 5, "batch_size": 32},
        metrics={"top1": 0.82, "top5": 0.95, "mean_confidence": 0.7, "per_card": {}},
        storage_path="data/models/clip_v1.pt",
        status="candidate",
    )
    assert mid == "clip-1"
    models = pl.list_models(db)
    assert len(models) == 1
    assert models[0]["version"] == "1"
    assert models[0]["status"] == "candidate"
    assert models[0]["is_active"] == 0
    assert json.loads(models[0]["parameters"])["epochs"] == 5


def test_promote_model_activates_and_deactivates(db):
    pl.register_model(db, "1", "dataset-v1", {}, {"top1": 0.8}, "p1", "candidate")
    pl.register_model(db, "2", "dataset-v2", {}, {"top1": 0.85}, "p2", "candidate")

    assert pl.promote_model(db, "clip-1") is True
    active = pl.get_active_model(db)
    assert active["model_id"] == "clip-1"
    assert active["status"] == "active"

    # Promote v2 -> v1 inactive
    assert pl.promote_model(db, "clip-2") is True
    active = pl.get_active_model(db)
    assert active["model_id"] == "clip-2"
    assert pl.list_models(db)[1]["is_active"] == 0  # v1 deaktiviert


def test_promote_rejected_for_active_status(db):
    pl.register_model(db, "1", "d", {}, {}, "p", "candidate")
    pl.promote_model(db, "clip-1")
    # already active -> no double promote
    assert pl.promote_model(db, "clip-1") is False


def test_retire_and_rollback(db):
    pl.register_model(db, "1", "d", {}, {"top1": 0.9}, "p1", "candidate")
    pl.register_model(db, "2", "d", {}, {"top1": 0.5}, "p2", "candidate")
    pl.promote_model(db, "clip-1")
    pl.promote_model(db, "clip-2")  # v2 wird active
    # Rollback: v1 retired re-aktivieren
    assert pl.promote_model(db, "clip-1") is True
    assert pl.get_active_model(db)["model_id"] == "clip-1"


def test_worse_model_never_auto_active(db):
    """A registered candidate stays candidate — the active model is untouched."""
    pl.register_model(db, "1", "d", {}, {"top1": 0.9}, "p1", "candidate")
    pl.promote_model(db, "clip-1")
    pl.register_model(db, "2", "d", {}, {"top1": 0.3}, "p2", "candidate")
    active = pl.get_active_model(db)
    assert active["model_id"] == "clip-1", "schlechteres Modell darf nie automatisch aktiv werden!"


def test_next_version_number(db):
    assert pl.next_version_number(db) == 1
    pl.register_model(db, "1", "d", {}, {}, "p", "candidate")
    assert pl.next_version_number(db) == 2


def test_dataset_snapshot(tmp_path, monkeypatch):
    """Snapshot zips all confirmed photos with a SHA-256 manifest."""
    scans = Path("data/scans/confirmed")
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)  # project root
    if not scans.exists() or not list(scans.glob("*.jpg")):
        pytest.skip("keine Scan-Fotos vorhanden")
    path = pl.create_dataset_snapshot("99")
    assert path.exists()
    import zipfile

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["version"] == "99"
        assert len(manifest["files"]) > 0
    path.unlink()  # cleanup test snapshot
