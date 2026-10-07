"""State management and recovery tests."""
import pytest
from app.core.config import AppConfig
from app.core import state


@pytest.fixture(autouse=True)
def setup_state(tmp_path, monkeypatch):
    """Redirect state directory to tmp_path for testing."""
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "project_state")
    state.STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Reset singleton
    AppConfig.reset()
    AppConfig.load()


class TestState:
    def test_current_state(self):
        s = state.load_current_state()
        assert s["phase"] == "init"
        state.update_state(phase="phase1", database_ready=True)
        s = state.load_current_state()
        assert s["phase"] == "phase1"
        assert s["database_ready"] is True

    def test_task_lifecycle(self):
        t = state.add_task("test_1", "Test task")
        assert t["state"] == "PENDING"
        state.start_task("test_1")
        tasks = state.load_task_queue()
        assert any(t["state"] == "RUNNING" for t in tasks)
        state.complete_task("test_1", result={"ok": True})
        completed = state.load_completed_tasks()
        assert len(completed) == 1
        assert completed[0]["result"]["ok"] is True

    def test_task_failure(self):
        state.add_task("test_fail", "Failing task")
        state.start_task("test_fail")
        state.fail_task("test_fail", "Something went wrong")
        failed = state.load_failed_tasks()
        assert len(failed) == 1
        assert failed[0]["error"] == "Something went wrong"

    def test_interrupted_recovery(self):
        state.add_task("task_a", "Task A")
        state.add_task("task_b", "Task B")
        state.start_task("task_a")
        # Simulate crash: task_a is left RUNNING
        interrupted = state.detect_interrupted_tasks()
        assert len(interrupted) == 1
        assert interrupted[0]["id"] == "task_a"
        recovered = state.recover_interrupted_tasks()
        assert "task_a" in recovered
        # Task should now be in failed tasks
        failed = state.load_failed_tasks()
        assert any(f["id"] == "task_a" for f in failed)

    def test_decisions(self):
        state.add_decision("Use SQLite", "Simple and reliable")
        decisions = state.load_decisions()
        assert len(decisions) == 1
        assert decisions[0]["title"] == "Use SQLite"

    def test_milestones(self):
        state.add_milestone("Phase 0 complete", "completed")
        milestones = state.load_milestones()
        assert len(milestones) == 1
        assert milestones[0]["status"] == "completed"

    def test_error_logging(self):
        state.log_error("err_001", "Test error", {"context": "test"})
        errors = list((state.STATE_DIR / "errors").glob("*.json"))
        assert len(errors) == 1
