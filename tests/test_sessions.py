import pytest

from freenerf.sessions.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def test_create_and_get(store):
    session_id = store.create("Build agent", workspace="/tmp/project")

    session = store.get(session_id)
    assert session["title"] == "Build agent"
    assert session["workspace"] == "/tmp/project"
    assert session["is_closed"] == 0


def test_add_message_with_metadata(store):
    session_id = store.create()

    store.add_message(session_id, "user", "hello")
    store.add_message(
        session_id,
        "assistant",
        "world",
        metadata={"outcome": "completed"},
    )

    messages = store.get_messages(session_id)

    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["metadata"]["outcome"] == "completed"
    assert store.message_count(session_id) == 2


def test_invalid_role_is_rejected(store):
    session_id = store.create()

    with pytest.raises(ValueError):
        store.add_message(session_id, "hacker", "x")


def test_add_message_requires_existing_session(store):
    with pytest.raises(ValueError):
        store.add_message("missing", "user", "x")


def test_search_matches_titles_and_messages(store):
    first = store.create("Refactor auth")
    second = store.create("Write docs")

    store.add_message(first, "user", "Please rewrite the login flow")
    store.add_message(second, "user", "Document the API")

    assert [item["id"] for item in store.search("auth")] == [first]
    assert [item["id"] for item in store.search("login")] == [first]
    assert [item["id"] for item in store.search("API")] == [second]
    assert len(store.search("")) == 2


def test_close_and_reopen(store):
    session_id = store.create("Temp")

    assert store.close_session(session_id)
    assert store.list_sessions() == []

    assert store.reopen_session(session_id)
    assert [item["id"] for item in store.list_sessions()] == [session_id]


def test_rename_normalizes_whitespace(store):
    session_id = store.create()

    assert store.rename(session_id, "  New   title  ")
    assert store.get(session_id)["title"] == "New title"


def test_delete_cascades_messages(store):
    session_id = store.create("Delete me")
    store.add_message(session_id, "user", "hello")

    assert store.delete(session_id)
    assert store.get(session_id) is None
    assert store.get_messages(session_id) == []


def test_sessions_are_isolated_between_databases(tmp_path):
    first = SessionStore(tmp_path / "one.db")
    second = SessionStore(tmp_path / "two.db")

    first.create("Only first")

    assert [item["title"] for item in first.list_sessions()] == ["Only first"]
    assert second.list_sessions() == []


def test_export_session(store):
    session_id = store.create("Export")
    store.add_message(session_id, "user", "hi")

    export = store.export_session(session_id)

    assert export["session"]["title"] == "Export"
    assert export["messages"][0]["content"] == "hi"


def test_export_missing_session(store):
    with pytest.raises(ValueError):
        store.export_session("missing")


def test_clear_messages_removes_only_the_selected_transcript(store):
    first = store.create("First")
    second = store.create("Second")
    store.add_message(first, "user", "first message")
    store.add_message(first, "assistant", "first reply")
    store.add_message(second, "user", "second message")

    assert store.clear_messages(first) == 2
    assert store.get_messages(first) == []
    assert store.message_count(first) == 0
    assert [item["content"] for item in store.get_messages(second)] == ["second message"]


def test_tool_activity_is_isolated_and_survives_store_reopen(tmp_path):
    database = tmp_path / "sessions.db"
    store = SessionStore(database)
    first = store.create("First")
    second = store.create("Second")

    store.add_activity(first, {"name": "read_file", "phase": "finished", "detail": "src/app.py"})
    store.add_activity(second, {"name": "run_shell", "phase": "finished", "detail": "pytest"})

    reopened = SessionStore(database)
    assert reopened.get_activity(first)[0]["name"] == "read_file"
    assert reopened.get_activity(second)[0]["name"] == "run_shell"
    assert reopened.clear_activity(first) == 1
    assert reopened.get_activity(first) == []
    assert reopened.get_activity(second)[0]["name"] == "run_shell"


def test_tool_activity_is_exported_and_cascades_on_session_delete(store):
    session_id = store.create("Export activity")
    store.add_activity(session_id, {"name": "write_file", "phase": "finished", "detail": "src/new.py"})

    export = store.export_session(session_id)
    assert export["activity"][0]["detail"] == "src/new.py"

    assert store.delete(session_id)
    assert store.get_activity(session_id) == []


def test_tool_activity_is_bounded_per_session(store):
    session_id = store.create("Bounded log")
    for index in range(5):
        store.add_activity(
            session_id,
            {"name": "tool", "phase": "finished", "detail": str(index)},
            limit=3,
        )
    assert [event["detail"] for event in store.get_activity(session_id)] == ["2", "3", "4"]
