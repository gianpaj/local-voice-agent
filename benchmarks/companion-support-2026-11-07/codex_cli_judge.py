"""One isolated, structured Codex CLI inference call using the user's CLI login.

No credentials are read/copied. No tools, shared session, shell hooks or project
instructions are needed by the judge. CLI authentication uses CODEX_JUDGE_HOME.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import jsonschema

MODEL = "gpt-6.1-sol"
EFFORT = "xhigh"


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def generate(prompt, schema, directory, timeout=600):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    empty = directory / "empty-workspace"
    empty.mkdir(exist_ok=True)
    (directory / "prompt.txt").write_text(prompt)
    save(directory / "schema.json", schema)
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules",
        "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--disable", "shell_tool", "--disable", "multi_agent",
        "--disable", "apps", "--disable", "skill_search",
        "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
        "--model", MODEL, "-c", f'model_reasoning_effort="{EFFORT}"',
        "--json", "--color", "never", "--cd", str(empty),
        "--output-schema", str(directory / "schema.json"),
        "--output-last-message", str(directory / "answer.json"), "-",
    ]
    env = dict(os.environ)
    env["CODEX_HOME"] = os.environ.get("CODEX_JUDGE_HOME", str(Path.home() / ".codex"))
    # Use the explicitly selected existing ChatGPT login, never API fallback.
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    started = time.monotonic()
    metadata = {
        "model_requested": MODEL, "reasoning_effort": EFFORT,
        "auth_route": "existing Codex CLI ChatGPT login",
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
        "timeout_seconds": timeout, "fresh_ephemeral_session": True,
        "user_config_ignored": True, "tools_allowed": False,
        "separate_api_requests": False,
    }
    save(directory / "request.json", metadata)
    try:
        with (directory / "events.jsonl").open("w") as stdout, (directory / "stderr.txt").open("w") as stderr:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout,
                stderr=stderr, text=True, env=env, start_new_session=True)
            try:
                process.communicate(prompt, timeout=timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
        metadata["exit_code"] = process.returncode
        events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines() if x.strip()]
        forbidden = []
        for event in events:
            item = event.get("item", {})
            if item.get("type") in {"command_execution", "mcp_tool_call", "web_search", "file_change", "tool_call"}:
                forbidden.append(item.get("type"))
            if event.get("type") == "turn.completed":
                metadata["usage"] = event.get("usage")
        metadata["forbidden_tool_events"] = forbidden
        if process.returncode:
            raise RuntimeError(f"Codex CLI exited {process.returncode}; see retained stderr/events")
        if forbidden:
            raise RuntimeError(f"Unexpected judge tool execution: {forbidden}")
        answer = json.loads((directory / "answer.json").read_text())
        jsonschema.validate(answer, schema)
        metadata["status"] = "completed"
        return answer, metadata
    except BaseException as exc:
        metadata["status"] = "error"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        metadata["wall_seconds"] = round(time.monotonic() - started, 3)
        save(directory / "result.json", metadata)
