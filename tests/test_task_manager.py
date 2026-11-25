import time

from freenerf.tools.task_manager import TaskManager


def wait_for_status(manager, task_id, expected, timeout=5):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        result = manager.status(task_id)
        if isinstance(result, dict) and result["status"] == expected:
            return result
        time.sleep(0.05)

    raise AssertionError(
        f"Task did not reach {expected}: {manager.status(task_id)}"
    )


def test_task_completes(tmp_path):
    manager = TaskManager(cwd=tmp_path)
    task_id = manager.start("echo BUILD_OK")

    result = wait_for_status(manager, task_id, "completed")

    assert result["exit_code"] == 0
    assert "BUILD_OK" in result["output"]


def test_task_failure(tmp_path):
    manager = TaskManager(cwd=tmp_path)
    task_id = manager.start("echo BUILD_ERROR && exit 7")

    result = wait_for_status(manager, task_id, "failed")

    assert result["exit_code"] == 7
    assert "BUILD_ERROR" in result["output"]


def test_task_captures_stderr(tmp_path):
    manager = TaskManager(cwd=tmp_path)
    task_id = manager.start("echo ERROR_MESSAGE >&2")

    result = wait_for_status(manager, task_id, "completed")

    assert "ERROR_MESSAGE" in result["output"]


def test_task_stop(tmp_path):
    manager = TaskManager(cwd=tmp_path)
    task_id = manager.start("sleep 30")

    time.sleep(0.1)
    assert manager.stop(task_id) == "Task stopped"
    assert manager.status(task_id)["status"] == "stopped"


def test_max_concurrent_tasks(tmp_path):
    manager = TaskManager(cwd=tmp_path, max_tasks=1)
    first = manager.start("sleep 2")

    second = manager.start("echo SHOULD_NOT_RUN")

    assert isinstance(first, str)
    assert second == "Task limit reached"

    manager.stop(first)


def test_output_limit(tmp_path):
    manager = TaskManager(cwd=tmp_path, output_limit=100)
    task_id = manager.start(
        "python -c \"print('X' * 1000)\""
    )

    result = wait_for_status(manager, task_id, "completed")

    assert len(result["output"]) <= 100
