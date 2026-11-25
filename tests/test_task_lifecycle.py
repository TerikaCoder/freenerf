import pytest

from freenerf.agents.task_manager import AgentTaskManager, TaskManagerError
from freenerf.agents.task_store import TaskStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def make_manager(store, session_id="s1"):
    return AgentTaskManager(store, session_id=session_id, workspace="/tmp/ws")


def test_start_creates_running_task(store):
    manager = make_manager(store)

    task = manager.start("Build the feature", title="Build")

    assert task["title"] == "Build"
    assert task["status"] == "running"
    assert task["session_id"] == "s1"
    assert task["workspace"] == "/tmp/ws"
    assert task["logs"]


def test_plan_and_progress(store):
    manager = make_manager(store)
    manager.start("Do work")

    manager.set_plan([{"title": "Inspect"}, {"title": "Implement"}])
    manager.update_step(1, "completed", result="Looked at code")

    task = manager.snapshot()

    assert task["progress"] == 50
    assert task["done_steps"] == 1
    assert task["step_count"] == 2
    assert task["current_step_number"] == 1


def test_invalid_step_number(store):
    manager = make_manager(store)
    manager.start("Do work")
    manager.set_plan(["Only step"])

    with pytest.raises(TaskManagerError):
        manager.update_step(5, "completed")


def test_invalid_step_status(store):
    manager = make_manager(store)
    manager.start("Do work")
    manager.set_plan(["Only step"])

    with pytest.raises(TaskManagerError):
        manager.update_step(1, "melted")


def test_invalid_final_status(store):
    manager = make_manager(store)
    manager.start("Do work")

    with pytest.raises(TaskManagerError):
        manager.finish("victorious", "done")


def test_finish_and_implicit_status(store):
    manager = make_manager(store)
    manager.start("Do work")
    manager.set_plan(["Inspect", "Fix"])
    manager.update_step(1, "completed", result="ok")

    task = manager.finish_implicit("Partial work")

    assert task["status"] == "partial"
    assert task["summary"] == "Partial work"


def test_implicit_completion_when_all_steps_done(store):
    manager = make_manager(store)
    manager.start("Do work")
    manager.set_plan(["Inspect"])
    manager.update_step(1, "completed", result="ok")

    assert manager.finish_implicit("All done")["status"] == "completed"


def test_cancel_and_resume(store):
    manager = make_manager(store)
    manager.start("Do work")

    manager.cancel("Cancelled")
    assert manager.snapshot()["status"] == "cancelled"

    resumable = manager.resumable()
    assert resumable == []

    manager.start("Another task")
    assert len(manager.resumable()) == 1


def test_tasks_are_scoped_by_session(store):
    first = make_manager(store, session_id="one")
    second = make_manager(store, session_id="two")

    first.start("First session task")
    second.start("Second session task")

    ids = [task["id"] for task in first.resumable()]
    assert len(ids) == 1
    assert store.get_task(ids[0])["title"] == "First session task"
    assert store.get_task(ids[0])["session_id"] == "one"


def test_tool_logs_are_redacted(store):
    manager = make_manager(store)
    manager.start("Do work")
    manager.log_tool("run_shell", "api_key=supersecret123", True)

    logs = store.get_logs(manager.task_id)
    joined = "\n".join(item["message"] for item in logs)

    assert "supersecret123" not in joined
    assert "[REDACTED]" in joined


def test_missing_active_task_raises(store):
    manager = make_manager(store)

    with pytest.raises(TaskManagerError):
        manager.set_plan(["x"])


def test_new_request_supersedes_unfinished_task(store):
    manager = make_manager(store)
    manager.start("First request")
    first_id = manager.task_id

    manager.start("Second request")

    assert store.get_task(first_id)["status"] == "blocked"
    assert manager.snapshot()["title"] == "Second request"


def test_task_history(store):
    manager = make_manager(store)
    manager.start("First")
    manager.finish("completed", "done")
    manager.start("Second")

    history = manager.history()
    assert len(history) == 2
