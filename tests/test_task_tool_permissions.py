from freenerf.agents.permissions import evaluate_permission
from freenerf.agents.runtime import Agent, TOOLS


def test_task_tools_registered():
    names = {
        item["function"]["name"]
        for item in TOOLS
    }
    assert {"task_start", "task_status", "task_list", "task_stop"} <= names


def test_task_start_requires_approval_in_normal_mode():
    assert evaluate_permission("normal", "task_start", {"command": "echo test"})[0] == "ask"


def test_task_status_and_list_are_read_only():
    assert evaluate_permission("normal", "task_status", {"process_id": "x"})[0] == "allow"
    assert evaluate_permission("normal", "task_list", {})[0] == "allow"


def test_task_stop_requires_approval_in_normal_mode():
    assert evaluate_permission("normal", "task_stop", {"process_id": "x"})[0] == "ask"


def test_denied_mode_blocks_task_tools():
    for name, args in (
        ("task_start", {"command": "echo test"}),
        ("task_status", {"process_id": "x"}),
        ("task_list", {}),
        ("task_stop", {"process_id": "x"}),
    ):
        assert evaluate_permission("denied", name, args)[0] == "deny"


def test_task_dispatch_uses_manager():
    class MockManager:
        def start(self, command):
            return "abc123"

        def status(self, process_id):
            return {"status": "running", "id": process_id}

        def list_tasks(self):
            return [{"id": "abc123"}]

        def stop(self, process_id):
            return "Task stopped"

    agent = Agent.__new__(Agent)
    agent.task_manager = MockManager()
    agent.permission_mode = "full"
    agent.approve = None
    agent.console = None

    assert agent.execute_tool("task_start", {"command": "echo test"}) == "Task started: abc123"
    assert agent.execute_tool("task_status", {"process_id": "abc123"})["status"] == "running"
    assert agent.execute_tool("task_list", {}) == [{"id": "abc123"}]
    assert agent.execute_tool("task_stop", {"process_id": "abc123"}) == "Task stopped"
