import asyncio
from pathlib import Path

from textual.widgets import Input, OptionList, Static, TextArea

from freenerf.agents.task_store import TaskStore
from freenerf.config import Config
from freenerf.sessions.store import SessionStore
from freenerf.tui.app import FreenerfApp
from freenerf.workspace import Workspace


class FakeProvider:
    supports_tools = True
    supports_streaming = False
    supports_images = False

    def chat(self, messages, tools=None, stream_callback=None):
        return {"content": "Hello from the fake model", "tool_calls": []}

    def close(self):
        pass


def make_app(tmp_path):
    workspace = Workspace.open(tmp_path / "workspace").ensure()
    config = Config()
    config.workspace_root = str(workspace.root)
    config.typewriter_enabled = False
    config.streaming = False

    app = FreenerfApp(
        config,
        workspace=workspace,
        session_store=SessionStore(workspace.sessions_db),
        task_store=TaskStore(workspace.tasks_db),
    )
    return app, config, workspace


def test_app_mounts_commands_and_screens(tmp_path):
    app, config, _ = make_app(tmp_path)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            assert app.query_one("#prompt")
            assert app.query_one("#chat")
            assert app.query_one("#task-panel")
            assert app.query_one("#task-panel").display is False
            assert app.query_one("#sidebar").display is False

            prompt = app.query_one("#prompt", TextArea)
            prompt.text = "/help"
            app.action_send_prompt()
            await pilot.pause()

            app.execute_command("/permissions full")
            await pilot.pause()
            assert config.permission_mode == "full"
            assert app.agent.permission_mode == "full"

            app.action_settings()
            await pilot.pause()
            assert type(app.screen).__name__ == "SettingsScreen"
            await pilot.press("escape")
            await pilot.pause()

            app.action_history()
            await pilot.pause()
            assert type(app.screen).__name__ == "SessionHistoryScreen"
            await pilot.press("escape")
            await pilot.pause()

            app.action_workspace()
            await pilot.pause()
            assert type(app.screen).__name__ == "WorkspaceScreen"
            await pilot.press("escape")
            await pilot.pause()

            app._sync_sidebar(80)
            assert app.query_one("#sidebar").display is False
            app._sync_sidebar(120)
            assert app.query_one("#sidebar").display is False
            app.action_toggle_sidebar()
            assert app.query_one("#sidebar").display is True

            task_id = app.task_store.create_task(
                "Test task", session_id=app.session_id
            )
            app.apply_task_update(app.task_store.get_task(task_id))
            assert app.query_one("#task-panel").display is True

            app.task_store.set_status(task_id, "completed", "done")
            app.apply_task_update(app.task_store.get_task(task_id))
            assert app.query_one("#task-panel").display is True
            app._auto_hide_task_panel()
            assert app.query_one("#task-panel").display is False
            assert app.task_store.get_task(task_id) is not None

    asyncio.run(run())


def test_narrow_layout_hides_sidebar_and_keeps_chat_usable(tmp_path):
    app, _, _ = make_app(tmp_path)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            assert app.query_one("#sidebar").display is False
            assert app.query_one("#hint").display is False
            composer = app.query_one("#composer")
            composer_height = composer.styles.height
            composer_min_height = composer.styles.min_height
            assert composer_height.value == 4
            assert composer_height.unit.name == "CELLS"
            assert composer_min_height.value == 4
            assert composer_min_height.unit.name == "CELLS"
            assert composer.size.height == 4
            assert app.query_one("#composer-row").size.height == 4
            assert app.query_one("#prompt").size.height == 4
            assert app.query_one("#send").size.height == 4
            assert app.query_one("#brandbar").size.height == 1
            assert app.query_one("#task-panel").display is False
            assert app.query_one("#prompt").display is True
            welcome = app.query_one("#welcome")
            assert welcome.display is True
            assert welcome.size.height <= 1
            assert app.query_one("#chat").display is True
            assert app.query_one("#chat").size.height >= 8

            # At 24 rows the hint stays hidden regardless of width. Simulate
            # a taller terminal before checking the width-only breakpoint.
            app._compact_height = False
            app._sync_sidebar(120)
            assert app.query_one("#sidebar").display is False
            assert app.query_one("#hint").display is True

            task_id = app.task_store.create_task(
                "Narrow task", session_id=app.session_id
            )
            app.apply_task_update(app.task_store.get_task(task_id))
            assert app.query_one("#task-panel").display is True

    asyncio.run(run())


def test_keyboard_shortcuts_open_screens(tmp_path):
    app, config, _ = make_app(tmp_path)
    config.save = lambda: None

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").focus()

            await pilot.press("ctrl+s")
            await pilot.pause()
            assert type(app.screen).__name__ == "SettingsScreen"
            await pilot.press("escape")
            await pilot.pause()

            await pilot.press("ctrl+h")
            await pilot.pause()
            assert type(app.screen).__name__ == "SessionHistoryScreen"
            await pilot.press("escape")
            await pilot.pause()

            await pilot.press("ctrl+g")
            await pilot.pause()
            assert type(app.screen).__name__ == "WorkspaceScreen"
            await pilot.press("escape")
            await pilot.pause()

            before = app.session_id
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert app.session_id != before

    asyncio.run(run())


def test_settings_apply_updates_config_and_agent(tmp_path):
    app, config, _ = make_app(tmp_path)
    config.save = lambda: None

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_settings()
            await pilot.pause()

            screen = app.screen
            assert type(screen).__name__ == "SettingsScreen"

            screen.query_one("#settings-max-steps", Input).value = "9"
            screen.query_one("#settings-temperature", Input).value = "0.5"
            screen.query_one(
                "#settings-key-openai_api_key", Input
            ).value = "local-key"
            screen.apply_pressed()
            await pilot.pause()

            assert config.max_agent_steps == 9
            assert config.temperature == 0.5
            assert config.openai_api_key == "local-key"
            assert app.agent.max_steps == 9
            assert type(app.screen).__name__ == "Screen"

    asyncio.run(run())


def test_settings_do_not_validate_hidden_custom_font_field(tmp_path):
    app, config, _ = make_app(tmp_path)
    config.save = lambda: None

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_settings()
            await pilot.pause()

            screen = app.screen
            screen.query_one("#settings-font-size").value = "normal"
            screen.query_one("#settings-custom-font-size", Input).value = ""
            screen.apply_pressed()
            await pilot.pause()

            assert type(app.screen).__name__ == "Screen"
            assert config.font_size == "normal"

    asyncio.run(run())


class StreamingProvider(FakeProvider):
    supports_streaming = True

    def chat(self, messages, tools=None, stream_callback=None):
        if stream_callback:
            stream_callback("Hello ")
            stream_callback("streamed")
        return {"content": "Hello streamed", "tool_calls": []}


def test_streaming_reply_is_committed_once(tmp_path):
    app, _, workspace = make_app(tmp_path)
    app.agent.provider = StreamingProvider()
    app.agent.streaming = True

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            prompt = app.query_one("#prompt", TextArea)
            prompt.text = "Say hello while streaming"
            app.action_send_prompt()

            for _ in range(100):
                await asyncio.sleep(0.05)
                if not app.busy:
                    break

            await pilot.pause()
            assert app.busy is False
            assert app.query_one("#streaming").display is False

            store = SessionStore(workspace.sessions_db)
            answers = [
                item["content"]
                for item in store.get_messages(app.session_id)
                if item["role"] == "assistant"
            ]
            assert answers == ["Hello streamed"]

    asyncio.run(run())


def test_chat_turn_flows_through_agent_and_store(tmp_path):
    app, _, workspace = make_app(tmp_path)
    app.agent.provider = FakeProvider()

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            prompt = app.query_one("#prompt", TextArea)
            prompt.text = "Say hello"
            app.action_send_prompt()

            for _ in range(100):
                await asyncio.sleep(0.05)
                if not app.busy:
                    break

            await pilot.pause()
            assert app.busy is False
            assert app.query_one("#task-panel").display is True
            app._auto_hide_task_panel()
            assert app.query_one("#task-panel").display is False

            store = SessionStore(workspace.sessions_db)
            messages = store.get_messages(app.session_id)
            roles = [item["role"] for item in messages]
            assert "user" in roles
            assert any(
                item["role"] == "assistant"
                and "Hello from the fake model" in item["content"]
                for item in messages
            )

            assert app.agent.last_outcome.status == "completed"

    asyncio.run(run())


def test_task_panel_tracks_active_task_and_is_isolated_per_session(tmp_path):
    app, _, _ = make_app(tmp_path)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            first_session = app.session_id

            first_task = app.task_store.create_task(
                "First session task", session_id=first_session
            )
            app.apply_task_update(app.task_store.get_task(first_task))
            assert app.query_one("#task-panel").display is True

            app.task_store.set_status(first_task, "completed", "finished")
            app.apply_task_update(app.task_store.get_task(first_task))
            app._schedule_task_hide(0.05)
            for _ in range(20):
                await asyncio.sleep(0.02)
                if app.query_one("#task-panel").display is False:
                    break
            assert app.query_one("#task-panel").display is False

            app.action_new_session()
            await pilot.pause()
            second_session = app.session_id
            assert second_session != first_session
            assert app.query_one("#task-panel").display is False

            # A late update from the old session must not leak into the new one.
            app.apply_task_update(app.task_store.get_task(first_task))
            assert app._current_task is None
            assert app.query_one("#task-panel").display is False

            second_task = app.task_store.create_task(
                "Second session task", session_id=second_session
            )
            app.apply_task_update(app.task_store.get_task(second_task))
            assert app.query_one("#task-panel").display is True
            assert app._current_task["id"] == second_task

            app.action_toggle_task_panel()
            assert app.query_one("#task-panel").display is False
            app.action_toggle_task_panel()
            assert app.query_one("#task-panel").display is True

            app.activate_session(first_session)
            await pilot.pause()
            assert app._current_task["id"] == first_task
            assert app.query_one("#task-panel").display is False

    asyncio.run(run())


def test_activating_a_closed_session_reopens_it(tmp_path):
    app, _, workspace = make_app(tmp_path)
    session_id = app.session_id
    app.store.close_session(session_id)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.store.get(session_id)["is_closed"] == 1

            app.activate_session(session_id)
            await pilot.pause()

            assert app.session_id == session_id
            assert app.store.get(session_id)["is_closed"] == 0

    asyncio.run(run())


def test_active_old_session_remains_visible_in_tab_strip(tmp_path):
    app, _, _ = make_app(tmp_path)
    first_session = app.session_id

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            for _ in range(6):
                app.action_new_session()
            await pilot.pause()

            app.activate_session(first_session)
            await pilot.pause()

            assert app.session_id == first_session
            assert app.query_one(f"#session-{first_session}")

    asyncio.run(run())


def test_workspace_picker_browses_home_storage_and_folders(tmp_path):
    app, _, workspace = make_app(tmp_path)
    child = workspace.root / "child"
    child.mkdir()
    (workspace.root / "note.txt").write_text("hello", encoding="utf-8")

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.action_workspace()
            await pilot.pause()

            screen = app.screen
            assert type(screen).__name__ == "WorkspaceScreen"
            assert screen.query_one("#workspace-path", Static)
            assert screen.current_path == workspace.root

            screen.load(workspace.root)
            options = screen.query_one("#workspace-list", OptionList)
            ids = [
                options.get_option_at_index(index).id
                for index in range(options.option_count)
            ]
            assert f"dir:{child}" in ids
            assert f"file:{workspace.root / 'note.txt'}" in ids

            options.focus()
            options.highlighted = 0
            await pilot.press("enter")
            await pilot.pause()
            assert screen.current_path == child

            await pilot.press("backspace")
            await pilot.pause()
            assert screen.current_path == workspace.root

            screen.home_pressed()
            assert screen.current_path == Path.home().resolve()

            storage = Path("/storage/emulated/0")
            if storage.is_dir():
                screen.storage_pressed()
                assert screen.current_path == storage.resolve()

            screen.load(workspace.root)
            screen.use_pressed()
            await pilot.pause()
            assert type(app.screen).__name__ == "Screen"

    asyncio.run(run())


def test_workspace_switch_rebuilds_isolated_stores(tmp_path):
    app, _, first = make_app(tmp_path)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            second = Workspace.open(tmp_path / "second").ensure()
            app._switch_workspace(second)
            await pilot.pause()

            assert app.workspace.root == second.root
            assert app.store.database == second.sessions_db
            assert app.task_store.database == second.tasks_db
            assert app.store.database != first.sessions_db

            sessions = app.store.list_sessions()
            assert sessions
            assert sessions[0]["workspace"] == str(second.root)

    asyncio.run(run())


def test_clear_command_clears_database_and_agent_context(tmp_path):
    app, _, workspace = make_app(tmp_path)
    session_id = app.session_id
    app.store.add_message(session_id, "user", "old prompt")
    app.store.add_activity(session_id, {"name": "read_file", "phase": "finished"})
    app.agent.messages.append({"role": "user", "content": "old prompt"})

    async def run():
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.execute_command("/clear")
            await pilot.pause()
            assert app.store.get_messages(session_id) == []
            assert app.store.get_activity(session_id) == []
            assert len(app.agent.messages) == 1
            assert app.query_one("#welcome").display is True
            assert app.query_one("#chat").display is True

    asyncio.run(run())


def test_file_mentions_are_workspace_scoped_and_persisted_in_agent_history(tmp_path):
    app, _, workspace = make_app(tmp_path)
    source = workspace.root / "example.py"
    source.write_text("print('from project')", encoding="utf-8")

    expanded, error = app.expand_file_mentions("Review @example.py")
    assert error == ""
    assert "print('from project')" in expanded
    assert "FILE: example.py" in expanded

    outside = tmp_path / "outside.txt"
    outside.write_text("not allowed", encoding="utf-8")
    expanded, error = app.expand_file_mentions(f"Review @{outside}")
    assert expanded == f"Review @{outside}"
    assert "outside project" in error
