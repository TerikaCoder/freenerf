from freenerf.agents.task_store import TaskStore


def test_process_storage(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create_task("Build project")

    assert store.save_process(
        task_id, "proc001", 1234, "gradle build"
    )

    process = store.get_process(task_id)
    assert process["process_id"] == "proc001"
    assert process["pid"] == 1234
    assert process["status"] == "running"

    assert store.update_process(
        task_id, "completed", exit_code=0, output="BUILD SUCCESS"
    )

    process = store.get_process(task_id)
    assert process["status"] == "completed"
    assert process["exit_code"] == 0
    assert "BUILD SUCCESS" in process["output"]

    assert len(store.list_processes()) == 1


def test_process_missing_task(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    assert not store.save_process(
        "missing", "proc002", 1234, "echo test"
    )


def test_process_persistence(tmp_path):
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    task_id = store.create_task("Persistent task")
    store.save_process(task_id, "proc003", 5678, "sleep 10")

    reopened = TaskStore(database)
    process = reopened.get_process(task_id)

    assert process["pid"] == 5678
    assert process["command"] == "sleep 10"


def test_recovery_marks_old_running_process_unverified(tmp_path):
    database = tmp_path / "tasks.db"
    store = TaskStore(database)

    task_id = store.create_task("Old background task")
    store.save_process(task_id, "old001", 9876, "sleep 30")

    reopened = TaskStore(database)
    manager_processes = reopened.list_processes(statuses=("running",))

    assert len(manager_processes) == 1
    assert manager_processes[0]["process_id"] == "old001"
    assert manager_processes[0]["pid"] == 9876


def test_task_manager_recovery_marks_process_unverified(tmp_path):
    from freenerf.tools.task_manager import TaskManager

    store = TaskStore(tmp_path / "recovery.db")
    task_id = store.create_task("Restarted task")
    store.save_process(task_id, "old002", 4321, "sleep 30")

    manager = TaskManager(store=store)
    recovered = manager.recover_unverified()

    assert len(recovered) == 1
    assert recovered[0]["process_id"] == "old002"
    assert recovered[0]["pid"] == 4321
    assert recovered[0]["status"] == "unverified"

    saved = store.get_process(task_id)
    assert saved["status"] == "unverified"
    assert saved["pid"] == 4321


def test_task_manager_lists_persisted_unverified_process(tmp_path):
    from freenerf.tools.task_manager import TaskManager

    store = TaskStore(tmp_path / "list.db")
    task_id = store.create_task("Old task")
    store.save_process(task_id, "old003", 7654, "sleep 30")
    store.update_process(task_id, "unverified", output="Previous output")

    manager = TaskManager(store=store)
    tasks = manager.list_tasks()

    assert len(tasks) == 1
    assert tasks[0]["id"] == "old003"
    assert tasks[0]["task_id"] == task_id
    assert tasks[0]["pid"] == 7654
    assert tasks[0]["status"] == "unverified"
    assert tasks[0]["persisted"] is True


def test_task_manager_status_reads_persisted_process(tmp_path):
    from freenerf.tools.task_manager import TaskManager

    store = TaskStore(tmp_path / "status.db")
    task_id = store.create_task("Old task")
    store.save_process(task_id, "old004", 8765, "sleep 30")
    store.update_process(
        task_id, "unverified", output="Previous output"
    )

    manager = TaskManager(store=store)
    status = manager.status("old004")

    assert status["id"] == "old004"
    assert status["task_id"] == task_id
    assert status["pid"] == 8765
    assert status["status"] == "unverified"
    assert status["output"] == "Previous output"
    assert status["persisted"] is True
    assert "not verified" in status["note"].lower()


def test_process_session_isolation(tmp_path):
    store = TaskStore(tmp_path / "isolation.db")
    task_a = store.create_task("Task A", session_id="session-A")
    task_b = store.create_task("Task B", session_id="session-B")

    assert store.save_process(
        task_b, "proc-B", 2222, "echo B",
        session_id="session-B",
    )

    assert store.get_process(
        task_b, session_id="session-A"
    ) is None

    assert not store.update_process(
        task_b, "completed", session_id="session-A"
    )

    assert not store.save_process(
        task_b, "proc-X", 3333, "echo X",
        session_id="session-A",
    )

    assert store.get_process(
        task_b, session_id="session-B"
    )["status"] == "running"

    assert store.get_process_by_id(
        "proc-B", session_id="session-A"
    ) is None

    assert store.list_processes(
        session_id="session-A"
    ) == []

    assert len(store.list_processes(
        session_id="session-B"
    )) == 1


def test_task_manager_recovery_is_session_scoped(tmp_path):
    from freenerf.tools.task_manager import TaskManager

    store = TaskStore(tmp_path / "recovery_isolation.db")
    task_a = store.create_task("Task A", session_id="session-A")
    task_b = store.create_task("Task B", session_id="session-B")

    store.save_process(
        task_a, "proc-A", 1111, "echo A",
        session_id="session-A",
    )
    store.save_process(
        task_b, "proc-B", 2222, "echo B",
        session_id="session-B",
    )

    manager = TaskManager(store=store, session_id="session-A")
    recovered = manager.recover_unverified()

    assert [p["process_id"] for p in recovered] == ["proc-A"]
    assert store.get_process(
        task_a, session_id="session-A"
    )["status"] == "unverified"
    assert store.get_process(
        task_b, session_id="session-B"
    )["status"] == "running"
