import os

import pytest

from freenerf import workspace as workspace_module
from freenerf.agents.task_store import TaskStore
from freenerf.sessions.store import SessionStore
from freenerf.workspace import Workspace, WorkspaceError, WorkspaceManager
from freenerf.config import Config


def test_open_creates_missing_directory(tmp_path):
    target = tmp_path / "new-workspace"
    workspace = Workspace.open(target)

    assert workspace.root == target.resolve()
    assert target.is_dir()


def test_open_rejects_files(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(WorkspaceError):
        Workspace.open(file_path)


def test_open_without_create_fails(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace.open(tmp_path / "missing", create=False)


def test_ensure_creates_layout(tmp_path):
    workspace = Workspace.open(tmp_path).ensure()

    assert workspace.sessions_db.parent.is_dir()
    assert workspace.tasks_db.parent == workspace.data_dir
    assert workspace.images_dir.is_dir()
    assert workspace.logs_dir.is_dir()
    assert workspace.backups_dir.is_dir()
    assert workspace.metadata_file.is_file()


def test_workspace_data_is_isolated(tmp_path):
    first = Workspace.open(tmp_path / "one").ensure()
    second = Workspace.open(tmp_path / "two").ensure()

    one_sessions = SessionStore(first.sessions_db)
    two_sessions = SessionStore(second.sessions_db)

    one_sessions.create("First project session")
    two_sessions.create("Second project session")

    assert [item["title"] for item in one_sessions.list_sessions()] == [
        "First project session"
    ]
    assert [item["title"] for item in two_sessions.list_sessions()] == [
        "Second project session"
    ]
    assert first.sessions_db != second.sessions_db
    assert first.tasks_db != second.tasks_db


def test_task_stores_are_isolated(tmp_path):
    first = Workspace.open(tmp_path / "one").ensure()
    second = Workspace.open(tmp_path / "two").ensure()

    TaskStore(first.tasks_db).create_task("Only in one")

    assert TaskStore(second.tasks_db).list_tasks() == []
    assert len(TaskStore(first.tasks_db).list_tasks()) == 1


def test_is_within(tmp_path):
    workspace = Workspace.open(tmp_path).ensure()

    assert workspace.is_within(tmp_path / "child" / "file.py")
    assert workspace.is_within("child/file.py")
    assert not workspace.is_within(tmp_path.parent / "elsewhere")


def test_legacy_database_migrates_once(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    legacy_session_db = legacy / "sessions.db"
    SessionStore(legacy_session_db).create("Legacy session")

    monkeypatch.setattr(workspace_module, "LEGACY_DATA_DIR", legacy)

    first = Workspace.open(tmp_path / "first").ensure()

    assert first.sessions_db.is_file()
    assert (legacy / ".migrated-to-workspace").is_file()
    assert SessionStore(first.sessions_db).list_sessions()[0]["title"] == "Legacy session"

    second = Workspace.open(tmp_path / "second").ensure()
    assert not second.sessions_db.is_file()


def test_data_dir_can_be_overridden(tmp_path, monkeypatch):
    workspace = Workspace.open(tmp_path).ensure()
    override = tmp_path / "central-data"
    monkeypatch.setenv("FREENERF_DATA_DIR", str(override))

    assert workspace.data_dir == override.resolve()
    assert workspace.sessions_db == override.resolve() / "sessions.db"


def test_manager_current_and_switch(tmp_path, monkeypatch):
    from freenerf import config as config_module

    monkeypatch.delenv("FREENERF_WORKSPACE", raising=False)
    monkeypatch.setattr(workspace_module, "LEGACY_DATA_DIR", tmp_path / "legacy")
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        config_module, "CONFIG_FILE", tmp_path / "config" / "config.json"
    )

    config = Config()
    config.workspace_root = str(tmp_path / "first")

    manager = WorkspaceManager(config)
    workspace = manager.current()

    assert workspace.root == (tmp_path / "first").resolve()
    assert config.workspace_root == str(workspace.root)

    second = manager.switch(tmp_path / "second")
    assert second.root == (tmp_path / "second").resolve()
    assert config.workspace_root == str(second.root)
    assert config.recent_workspaces[0] == str(second.root)
