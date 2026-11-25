import http.server
import json
import threading

from freenerf.agents.runtime import Agent
from freenerf.agents.task_manager import AgentTaskManager
from freenerf.agents.task_store import TaskStore
from freenerf.config import Config
from freenerf.llm.providers import OpenAICompatible
from freenerf.tools.filesystem import FileSystem
from freenerf.tools.terminal import Terminal


def test_agent_multistep_tool_calling_over_http(tmp_path):
    payloads = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            payloads.append(payload)

            if len(payloads) == 1:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_read",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "pyproject.toml"}),
                        },
                    }],
                }
            else:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_finish",
                        "type": "function",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps({
                                "status": "completed",
                                "summary": "Read the project file",
                            }),
                        },
                    }],
                }

            body = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo-project'\n", encoding="utf-8"
    )

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        provider = OpenAICompatible("test-key", "test-model", base_url)
        store = TaskStore(tmp_path / ".freenerf" / "tasks.db")
        manager = AgentTaskManager(store, session_id="e2e")

        agent = Agent(
            provider=provider,
            filesystem=FileSystem(tmp_path),
            terminal=Terminal(tmp_path),
            console=None,
            tasks=manager,
            config=Config(),
        )

        answer = agent.chat("Read pyproject.toml and report the project name")

        assert answer == "Read the project file"
        assert len(payloads) == 2

        # The second request must contain the real tool result.
        transcript = json.dumps(payloads[1])
        assert "demo-project" in transcript
        assert agent.last_outcome.status == "completed"
        assert store.get_task(manager.task_id)["status"] == "completed"

        provider.close()
    finally:
        server.shutdown()
        server.server_close()
