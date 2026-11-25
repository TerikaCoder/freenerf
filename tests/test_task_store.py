import pytest

from freenerf.agents.task_store import TaskStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_create_and_get_task(store):
    task_id = store.create_task("Build agent", "Upgrade Freenerf")

    task = store.get_task(task_id)

    assert task["title"] == "Build agent"
    assert task["description"] == "Upgrade Freenerf"
    assert task["status"] == "pending"
    assert task["steps"] == []


def test_save_and_update_plan(store):
    task_id = store.create_task("Build agent")

    store.set_plan(task_id, [
        {"title": "Inspect"},
        {"title": "Implement"},
    ])

    assert store.update_step(
        task_id, 0, "completed", result="Inspection OK"
    )

    task = store.get_task(task_id)

    assert len(task["steps"]) == 2
    assert task["steps"][0]["status"] == "completed"
    assert task["steps"][0]["result"] == "Inspection OK"


def test_resumable_tasks(store):
    running = store.create_task("Running")
    completed = store.create_task("Completed")

    store.set_status(running, "running")
    store.set_status(completed, "completed")

    tasks = store.resumable_tasks()

    ids = [task["id"] for task in tasks]

    assert running in ids
    assert completed not in ids


def test_task_persistence(store):
    task_id = store.create_task("Persistent task")
    store.set_plan(task_id, ["Inspect", "Test"])
    store.update_step(task_id, 0, "completed", result="OK")

    reopened = TaskStore(store.database)
    task = reopened.get_task(task_id)

    assert task is not None
    assert task["steps"][0]["result"] == "OK"


def test_invalid_status(store):
    task_id = store.create_task("Invalid status")

    with pytest.raises(ValueError):
        store.set_status(task_id, "unknown")


def test_missing_task(store):
    assert store.get_task("does-not-exist") is None
    assert store.set_status("does-not-exist", "running") is False


def test_task_session_isolation(store):
    task_a = store.create_task("Task A", session_id="session-A")
    task_b = store.create_task("Task B", session_id="session-B")

    assert store.get_task(task_a, session_id="session-A") is not None
    assert store.get_task(task_a, session_id="session-B") is None
    assert store.get_task(task_b, session_id="session-A") is None

    assert store.set_status(
        task_b, "running", session_id="session-A"
    ) is False

    assert store.set_plan(
        task_b, ["Unauthorized"], session_id="session-A"
    ) is False

    assert store.get_task(task_b, session_id="session-B")["steps"] == []


def test_step_session_isolation(store):
    task_id = store.create_task("Private task", session_id="session-A")
    store.set_plan(task_id, ["Inspect"], session_id="session-A")

    assert store.update_step(
        task_id, 0, "completed",
        result="Unauthorized",
        session_id="session-B",
    ) is False

    task = store.get_task(task_id, session_id="session-A")
    assert task["steps"][0]["status"] == "pending"
    assert task["steps"][0]["result"] is None


def test_resumable_tasks_session_isolation(store):
    task_a = store.create_task("A", session_id="session-A")
    task_b = store.create_task("B", session_id="session-B")

    store.set_status(task_a, "running", session_id="session-A")
    store.set_status(task_b, "running", session_id="session-B")

    tasks_a = store.resumable_tasks(session_id="session-A")
    tasks_b = store.resumable_tasks(session_id="session-B")

    assert [task["id"] for task in tasks_a] == [task_a]
    assert [task["id"] for task in tasks_b] == [task_b]
