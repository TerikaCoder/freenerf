import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque


class TaskManager:
    def __init__(self, cwd=".", max_tasks=8, output_limit=12000,
                 store=None, session_id=None):
        self.cwd = cwd
        self.max_tasks = max_tasks
        self.output_limit = output_limit
        self.store = store
        self.session_id = session_id
        self.tasks = {}
        self.lock = threading.RLock()

    def recover_unverified(self, limit=100):
        """Mark persisted running processes as unverified after restart.

        This does not attach to, signal, or control existing PIDs.
        """
        if not self.store:
            return []

        processes = self.store.list_processes(
            statuses=("running",),
            limit=limit,
            session_id=self.session_id,
        )

        recovered = []
        for process in processes:
            task_id = process.get("task_id")
            if not task_id:
                continue

            self.store.update_process(
                task_id, "unverified", session_id=self.session_id
            )
            process["status"] = "unverified"
            recovered.append(process)

        return recovered

    def start(self, command, task_id=None):
        with self.lock:
            active = sum(
                task["process"].poll() is None
                for task in self.tasks.values()
            )
            if active >= self.max_tasks:
                return "Task limit reached"

            if self.store and not task_id:
                task_id = self.store.create_task(
                    title=str(command),
                    description="Shell process: " + str(command),
                    session_id=self.session_id,
                )

            if self.store and task_id:
                owned_task = self.store.get_task(
                    task_id, session_id=self.session_id
                )
                if owned_task is None:
                    return "Task not found in this session"

            process_id = uuid.uuid4().hex[:8]

            process = subprocess.Popen(
                command,
                shell=True,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=(os.name == "posix"),
            )

            task = {
                "id": process_id,
                "task_id": task_id,
                "command": command,
                "pid": process.pid,
                "process": process,
                "output": deque(),
                "output_size": 0,
                "reader_done": threading.Event(),
                "status": "running",
                "started": time.time(),
                "finished": None,
                "exit_code": None,
                "persisted_status": None,
            }

            self.tasks[process_id] = task

            if self.store and task_id:
                self.store.save_process(
                    task_id, process_id, process.pid, command,
                    session_id=self.session_id,
                )

            thread = threading.Thread(
                target=self._read_output,
                args=(process_id,),
                daemon=True,
            )
            task["reader_thread"] = thread
            thread.start()

            return process_id

    def _output_text(self, task):
        return "".join(task["output"])[-self.output_limit:]

    def _persist(self, task):
        if not self.store or not task.get("task_id"):
            return
        self.store.update_process(
            task["task_id"],
            task["status"],
            exit_code=task["exit_code"],
            output=self._output_text(task),
            session_id=self.session_id,
        )

    def _read_output(self, process_id):
        with self.lock:
            task = self.tasks.get(process_id)

        if task is None:
            return

        try:
            for line in task["process"].stdout:
                with self.lock:
                    task["output"].append(line)
                    task["output_size"] += len(line)

                    while task["output_size"] > self.output_limit:
                        removed = task["output"].popleft()
                        task["output_size"] -= len(removed)
        finally:
            task["process"].stdout.close()
            task["reader_done"].set()

            with self.lock:
                self._refresh(task)
                self._persist(task)

    def _refresh(self, task):
        code = task["process"].poll()

        if code is None:
            return

        task["exit_code"] = code

        if task["status"] == "running" and task["reader_done"].is_set():
            task["status"] = "completed" if code == 0 else "failed"
            task["finished"] = time.time()
            self._persist(task)

    def status(self, process_id):
        with self.lock:
            task = self.tasks.get(process_id)
            if task:
                self._refresh(task)
                return {
                    "id": task["id"],
                    "task_id": task["task_id"],
                    "command": task["command"],
                    "pid": task["pid"],
                    "status": task["status"],
                    "exit_code": task["exit_code"],
                    "output": self._output_text(task),
                    "elapsed": round(time.time() - task["started"], 2),
                    "persisted": False,
                }

        if self.store:
            process = self.store.get_process_by_id(
                process_id, session_id=self.session_id
            )
            if process:
                return {
                    "id": process.get("process_id"),
                    "task_id": process.get("task_id"),
                    "command": process.get("command"),
                    "pid": process.get("pid"),
                    "status": process.get("status"),
                    "exit_code": process.get("exit_code"),
                    "output": process.get("output") or "",
                    "started": process.get("started_at"),
                    "finished": process.get("finished_at"),
                    "persisted": True,
                    "note": "Persisted process record; live process state is not verified.",
                }

        return "Task not found"

    def list_tasks(self):
        with self.lock:
            result = []
            known_ids = set()

            for task in self.tasks.values():
                self._refresh(task)
                known_ids.add(task["id"])
                result.append({
                    "id": task["id"],
                    "task_id": task["task_id"],
                    "command": task["command"],
                    "pid": task["pid"],
                    "status": task["status"],
                    "exit_code": task["exit_code"],
                    "started": task["started"],
                    "finished": task["finished"],
                    "persisted": False,
                })

            if self.store:
                for process in self.store.list_processes(
                    statuses=("unverified",),
                    limit=100,
                    session_id=self.session_id,
                ):
                    process_id = process.get("process_id")
                    if not process_id or process_id in known_ids:
                        continue

                    result.append({
                        "id": process_id,
                        "task_id": process.get("task_id"),
                        "command": process.get("command"),
                        "pid": process.get("pid"),
                        "status": "unverified",
                        "exit_code": process.get("exit_code"),
                        "started": process.get("started_at"),
                        "finished": process.get("finished_at"),
                        "persisted": True,
                    })

            return result

    def stop(self, process_id):
        with self.lock:
            task = self.tasks.get(process_id)
            if not task:
                return "Task not found"

            self._refresh(task)

            if task["status"] != "running":
                return f"Task is {task['status']}"

            process = task["process"]

        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass

        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait()

        with self.lock:
            task["status"] = "stopped"
            task["exit_code"] = process.returncode
            task["finished"] = time.time()
            self._persist(task)

        return "Task stopped"
