"""
Persistent project state / checkpoint system.
Tracks tasks, decisions, environment, milestones, and errors.
Enables resume-after-crash: any interrupted task (STATE=RUNNING) is detected
on restart and can be rolled back or resumed safely.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import AppConfig
from app.core.logging import get_logger

log = get_logger("state")

STATE_DIR = AppConfig.load().root / "project_state"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    tmp.replace(path)  # atomic


# ---------------------------------------------------------------------------
# Current state
# ---------------------------------------------------------------------------

def load_current_state() -> dict[str, Any]:
    return _read_json(STATE_DIR / "current_state.json") or {
        "phase": "init",
        "phase_started": _now(),
        "last_updated": _now(),
        "active_task": None,
        "database_ready": False,
        "data_imported_sets": [],
        "model_version": None,
        "dataset_version": None,
    }


def save_current_state(state: dict[str, Any]) -> None:
    state["last_updated"] = _now()
    _write_json(STATE_DIR / "current_state.json", state)


def update_state(**kwargs: Any) -> dict[str, Any]:
    state = load_current_state()
    state.update(kwargs)
    save_current_state(state)
    return state


# ---------------------------------------------------------------------------
# Task queue (transactional, resumable)
# ---------------------------------------------------------------------------

def load_task_queue() -> list[dict[str, Any]]:
    return _read_json(STATE_DIR / "task_queue.json") or []


def save_task_queue(tasks: list[dict[str, Any]]) -> None:
    _write_json(STATE_DIR / "task_queue.json", tasks)


def add_task(task_id: str, name: str, **meta: Any) -> dict[str, Any]:
    tasks = load_task_queue()
    if any(t["id"] == task_id for t in tasks):
        log.warning("Task already in queue, skipping", extra={"event": "task_dup", "task_id": task_id})
        return next(t for t in tasks if t["id"] == task_id)
    task = {
        "id": task_id,
        "name": name,
        "state": "PENDING",
        "created": _now(),
        "started": None,
        "completed": None,
        "meta": meta,
    }
    tasks.append(task)
    save_task_queue(tasks)
    return task


def start_task(task_id: str, name: str = "") -> dict[str, Any] | None:
    """Mark a task as RUNNING. If the task doesn't exist yet, create it first."""
    tasks = load_task_queue()
    if not any(t["id"] == task_id for t in tasks):
        add_task(task_id, name or task_id)
        tasks = load_task_queue()
    for t in tasks:
        if t["id"] == task_id:
            t["state"] = "RUNNING"
            t["started"] = _now()
            save_task_queue(tasks)
            update_state(active_task=task_id)
            log.info("Task started", extra={"event": "task_start", "task_id": task_id})
            return t
    return None


def complete_task(task_id: str, result: Any = None) -> None:
    tasks = load_task_queue()
    for t in tasks:
        if t["id"] == task_id:
            t["state"] = "COMPLETED"
            t["completed"] = _now()
            if result is not None:
                t["result"] = result
            save_task_queue(tasks)
            completed = load_completed_tasks()
            completed.append(t)
            _write_json(STATE_DIR / "completed_tasks.json", completed)
            tasks = [x for x in tasks if x["id"] != task_id]
            save_task_queue(tasks)
            update_state(active_task=None)
            log.info("Task completed", extra={"event": "task_complete", "task_id": task_id})
            return
    log.warning("Task not found for completion", extra={"task_id": task_id})


def fail_task(task_id: str, error: str, traceback: str = "") -> None:
    tasks = load_task_queue()
    for t in tasks:
        if t["id"] == task_id:
            t["state"] = "FAILED"
            t["completed"] = _now()
            t["error"] = error
            t["traceback"] = traceback
            save_task_queue(tasks)
            failed = load_failed_tasks()
            failed.append(t)
            _write_json(STATE_DIR / "failed_tasks.json", failed)
            tasks = [x for x in tasks if x["id"] != task_id]
            save_task_queue(tasks)
            update_state(active_task=None)
            log.error("Task failed", extra={"event": "task_fail", "task_id": task_id})
            return
    log.warning("Task not found for failure", extra={"task_id": task_id})


def load_completed_tasks() -> list[dict[str, Any]]:
    return _read_json(STATE_DIR / "completed_tasks.json") or []


def load_failed_tasks() -> list[dict[str, Any]]:
    return _read_json(STATE_DIR / "failed_tasks.json") or []


def load_decisions() -> list[dict[str, Any]]:
    return _read_json(STATE_DIR / "decisions.json") or []


def add_decision(title: str, rationale: str, **meta: Any) -> None:
    decisions = load_decisions()
    decisions.append({
        "title": title,
        "rationale": rationale,
        "timestamp": _now(),
        **meta,
    })
    _write_json(STATE_DIR / "decisions.json", decisions)


def load_environment() -> dict[str, Any]:
    return _read_json(STATE_DIR / "environment.json") or {}


def save_environment(env: dict[str, Any]) -> None:
    _write_json(STATE_DIR / "environment.json", env)


def load_milestones() -> list[dict[str, Any]]:
    return _read_json(STATE_DIR / "milestones.json") or []


def add_milestone(name: str, status: str = "completed", **meta: Any) -> None:
    milestones = load_milestones()
    milestones.append({"name": name, "status": status, "timestamp": _now(), **meta})
    _write_json(STATE_DIR / "milestones.json", milestones)


def log_error(error_id: str, message: str, context: dict[str, Any] | None = None) -> None:
    """Write a detailed error record to project_state/errors/."""
    errors_dir = STATE_DIR / "errors"
    errors_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": error_id,
        "message": message,
        "context": context or {},
        "timestamp": _now(),
    }
    _write_json(errors_dir / f"{error_id}.json", rec)


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

def detect_interrupted_tasks() -> list[dict[str, Any]]:
    """Find tasks left in RUNNING state from a previous session."""
    tasks = load_task_queue()
    return [t for t in tasks if t.get("state") == "RUNNING"]


def recover_interrupted_tasks() -> list[str]:
    """Mark interrupted RUNNING tasks as FAILED so they can be re-queued.
    Returns the list of recovered task ids."""
    interrupted = detect_interrupted_tasks()
    recovered = []
    for t in interrupted:
        fail_task(t["id"], "Interrupted by previous session / crash")
        recovered.append(t["id"])
    if recovered:
        log.warning(f"Recovered {len(recovered)} interrupted tasks", extra={"event": "recovery"})
    return recovered
