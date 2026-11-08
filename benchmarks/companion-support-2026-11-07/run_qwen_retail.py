#!/usr/bin/env python3
"""Frozen retail-10 pilot: local Qwen, independent Codex CLI caller/judges.

The pinned tau2 orchestrator, tools, policy, state replay and evaluators run
unchanged. Only the provider seam is replaced; no API-provider fallback.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

import requests
from loguru import logger

from codex_cli_judge import generate as codex_generate, save, MODEL, EFFORT
from candidate_failure import CandidateFailure
import run_qwen_eq as ownership

from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.data_model.simulation import TextRunConfig
from tau2.runner.build import build_orchestrator
from tau2.runner.batch import run_single_task
from tau2.runner.helpers import select_run_tasks
from tau2.runner.presets import load_cell_language
from tau2.utils import llm_utils
import tau2.evaluator.evaluator_nl_assertions as nl_evaluator

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "runs/qwen-retail10"
JUDGE = "codex-cli/gpt-6.1-sol"
CANDIDATE = "local-voice-qwen:latest"
BASE = "http://127.0.0.1:11434"


class Provider:
    task_id = ""
    index = 0

    def call(self, model, messages, tools=None, tool_choice=None, call_name=None, **kwargs):
        self.index += 1
        name = f"{self.index:04d}-{self.task_id}-{call_name or 'generation'}"
        directory = RUN / "calls" / name
        directory.mkdir(parents=True)
        rendered = llm_utils.to_litellm_messages(messages)
        schemas = [t.openai_schema for t in tools] if tools else []
        save(directory / "tau-request.json", {"model": model, "messages": rendered,
            "tools": schemas, "tool_choice": tool_choice, "call_name": call_name})
        if model == JUDGE:
            if schemas:
                raise RuntimeError("Retail customer unexpectedly received tools; adapter does not fake their execution")
            envelope = {"type": "object", "additionalProperties": False,
                        "properties": {"content": {"type": "string"}}, "required": ["content"]}
            prompt = ("Act as the model endpoint for the following benchmark request. "
                "Follow the supplied system message and conversation roles. "
                "Return only the next assistant message in the content field, never extra commentary. "
                "If the supplied system message requires JSON, content must contain that exact JSON object as a string. "
                "If simulating a customer, stay in that customer's role and obey the task and stop rules; "
                "do not solve the agent's task on its behalf. Do not use tools, browse or inspect files. "
                "Any explanation requested is a brief evidence summary, not private deliberation.\n\n" +
                json.dumps({"messages": rendered}, ensure_ascii=False))
            answer, meta = codex_generate(prompt, envelope, directory)
            usage = meta.get("usage", {})
            message = AssistantMessage(role="assistant", content=answer["content"], cost=None,
                usage={"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0)},
                raw_data={"provider": JUDGE, "reasoning_effort": EFFORT},
                generation_time_seconds=meta["wall_seconds"])
        elif model == CANDIDATE:
            id_to_name = {c["id"]: c["function"]["name"]
                for m in rendered for c in m.get("tool_calls") or []}
            native = copy.deepcopy(rendered)
            for m in native:
                if m["role"] == "tool":
                    m["tool_name"] = id_to_name[m.pop("tool_call_id")]
                for c in m.get("tool_calls") or []:
                    c["function"]["arguments"] = json.loads(c["function"]["arguments"])
                    c.pop("name", None)
            # Conservative GGUF-vocabulary count of the full JSON request plus
            # template margin. Never discard messages to make a request fit.
            request_text = json.dumps({"messages": native, "tools": schemas}, ensure_ascii=False)
            _, counted = ownership.token_count([{"role": "user", "content": request_text}])
            counted += 1024
            if counted + 1024 > 32768:
                raise CandidateFailure(f"Context preflight {counted}+1024 >32768; no truncation")
            payload = {"model": CANDIDATE, "messages": native, "tools": schemas,
                "stream": True, "think": False, "keep_alive": "5m", "options": {
                    "num_ctx": 32768, "num_predict": 1024, "temperature": 0.5,
                    "min_p": 0.1, "top_p": 0.95, "top_k": 20,
                    "presence_penalty": 0, "repeat_penalty": 1.0, "seed": 42}}
            save(directory / "ollama-request.json", payload)
            started = time.monotonic()
            parts, calls, done, first = [], [], None, None
            with requests.post(BASE + "/api/chat", json=payload, stream=True, timeout=(10, 180)) as response:
                response.raise_for_status()
                with (directory / "ollama-events.jsonl").open("w") as raw:
                    for line in response.iter_lines(chunk_size=1):
                        if not line:
                            continue
                        event = json.loads(line)
                        raw.write(json.dumps(event, ensure_ascii=False) + "\n")
                        raw.flush()
                        if event.get("error"):
                            raise RuntimeError(event["error"])
                        msg = event.get("message", {})
                        if msg.get("content"):
                            first = first or time.monotonic() - started
                            parts.append(msg["content"])
                        calls.extend(msg.get("tool_calls") or [])
                        if event.get("done"):
                            done = event
                        if time.monotonic() - started > 180:
                            raise CandidateFailure("180-second candidate call deadline")
            if not done or done.get("done_reason") == "length":
                raise CandidateFailure("Incomplete candidate generation; retained, not retried")
            message = AssistantMessage(role="assistant", content="".join(parts) or None,
                tool_calls=[ToolCall(id=f"call_{self.index}_{i}", name=c["function"]["name"],
                    arguments=c["function"]["arguments"]) for i,c in enumerate(calls)] or None,
                cost=0.0, usage={"prompt_tokens": done["prompt_eval_count"], "completion_tokens": done["eval_count"]},
                generation_time_seconds=time.monotonic() - started)
            save(directory / "timing.json", {"first_content_seconds": first,
                "wall_seconds": message.generation_time_seconds,
                "prompt_preflight_with_margin": counted,
                "actual_prompt_tokens": done["prompt_eval_count"], "output_tokens": done["eval_count"],
                "decode_tokens_per_second": done["eval_count"] / (done["eval_duration"] / 1e9)})
        else:
            raise RuntimeError(f"Unapproved provider {model}; no external API fallback")
        save(directory / "tau-response.json", message.model_dump(mode="json"))
        return message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    logger.remove()
    logger.add(lambda msg: print(msg, end="", flush=True), level="WARNING")
    fixture = json.loads((ROOT / "fixtures/retail10.json").read_text())
    for relative, expected in fixture["source_sha256"].items():
        assert hashlib.sha256((ROOT / "vendor/tau2-bench" / relative).read_bytes()).hexdigest() == expected
    config = TextRunConfig(domain="retail", language="en", task_ids=fixture["selected_task_ids"],
        task_split_name=None, llm_agent=CANDIDATE, llm_args_agent={"temperature": 0.5},
        llm_user=JUDGE, llm_args_user={"reasoning_effort": EFFORT},
        communicate_scoring={"scorer": "semantic", "judge": {"model": JUDGE,
            "model_args": {"reasoning_effort": EFFORT}}},
        max_steps=200, max_errors=10, timeout=1800, seed=300,
        max_retries=0, auto_review=False, hallucination_retries=0, max_concurrency=1)
    loaded = load_cell_language("en", "retail", "text")
    tasks = select_run_tasks(config, loaded)
    assert [t.id for t in tasks] == fixture["selected_task_ids"]
    orch = build_orchestrator(config, tasks[0], seed=300)
    preflight = {"config": config.model_dump(mode="json"),
        "task_ids": [t.id for t in tasks], "user_tools": len(orch.user.tools or []),
        "agent_tools": len(orch.agent.tools), "language_context": orch.call_context.language.model_dump(mode="json"),
        "candidate_options": {"num_ctx":32768,"num_predict":1024,"think":False,"temperature":0.5,"seed":42},
        "custom_provider": True, "official_full_score": False,
        "notes": "Pinned tau2 engine unchanged; fresh Codex CLI sessions for caller and semantic judges. Subscription usage, no dollar cost inferred."}
    if args.preflight:
        save(ROOT / "retail-codex-preflight.json", preflight)
        print("Preflight valid; no inference calls, service changes or model changes.", flush=True)
        return
    if RUN.exists() and not args.resume:
        raise RuntimeError("Existing retail run; inspect and use --resume to preserve completed outcomes")
    RUN.mkdir(exist_ok=True)
    (RUN / "calls").mkdir(exist_ok=True)
    (RUN / "tasks").mkdir(exist_ok=True)
    if (RUN / "protocol.json").exists():
        assert json.loads((RUN / "protocol.json").read_text()) == preflight, "Run protocol drift on resume"
    else:
        save(RUN / "protocol.json", preflight)
    # A one-task integration pilot is part of the scored slice. Preserve its
    # monitoring/recovery evidence when resuming the remaining tasks.
    if args.resume:
        archive = RUN / "recovery-checkpoints"
        archive.mkdir(exist_ok=True)
        checkpoint = len(list(archive.glob("resources-*.csv"))) + 1
        for name, suffix in (("resources.csv", "csv"), ("restore.json", "json"), ("placement.json", "json")):
            if (RUN / name).exists():
                shutil.copyfile(RUN / name, archive / f"{Path(name).stem}-{checkpoint}.{suffix}")
    provider = Provider()
    provider.index = len(list((RUN / "calls").iterdir()))
    llm_utils._generate_once = provider.call
    nl_evaluator.DEFAULT_LLM_NL_ASSERTIONS = JUDGE
    nl_evaluator.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"reasoning_effort": EFFORT}
    ownership.RUN = RUN
    health = requests.get("http://127.0.0.1:3000/api/health", timeout=5).json()
    if health.get("busy"):
        raise RuntimeError("Active call; voice service unchanged")
    def interrupt(signum, frame):
        raise InterruptedError(f"Signal {signum}")
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    stop = threading.Event()
    monitor = threading.Thread(target=ownership.sample_resources, args=(stop,), daemon=True)
    try:
        subprocess.run(["systemctl", "--user", "freeze", ownership.SERVICE], check=True)
        monitor.start()
        ownership.post("/api/generate", {"model": CANDIDATE, "keep_alive": 0}, 60)
        ownership.post("/api/generate", {"model": CANDIDATE, "stream": False, "keep_alive": "5m",
                                          "options": {"num_ctx": 32768}}, 180)
        ps = requests.get(BASE + "/api/ps", timeout=5).json()
        save(RUN / "placement.json", ps)
        model = next(m for m in ps["models"] if m["name"] == CANDIDATE)
        assert model["context_length"] == 32768 and model["size_vram"] >= 0.98 * model["size"]
        for task in tasks[:args.limit] if args.limit else tasks:
            destination = RUN / "tasks" / f"{task.id}.json"
            if destination.exists() or (RUN/'tasks'/f'{task.id}.failure.json').exists():
                print(f"Retaining completed task {task.id}", flush=True)
                continue
            provider.task_id = task.id
            save(RUN / "tasks" / f"{task.id}.input.json", task.model_dump(mode="json"))
            print(f"Starting retail task {task.id}", flush=True)
            try:
                simulation = run_single_task(config, task, seed=300, save_dir=None, auto_review=False)
            except CandidateFailure as exc:
                save(RUN/'tasks'/f'{task.id}.failure.json', {'task_id':task.id,
                    'status':'candidate_budget_or_format_failure','reward':0.0,
                    'candidate_budget_failure':True,'trajectory_complete':False,
                    'upstream_outcome_evaluation_completed':False,'reason':str(exc)})
                print(f"Candidate task failure {task.id}: {exc}; no retry",flush=True)
            else:
                save(destination, simulation.model_dump(mode="json"))
                print(f"Completed retail task {task.id}: reward={simulation.reward_info.reward}", flush=True)
            completed = list((RUN / "tasks").glob("*.json"))
            completed = [p for p in completed if not p.name.endswith(".input.json")]
            save(RUN / "progress.json", {"finished": len(completed), "total": len(tasks)})
    except BaseException as exc:
        save(RUN / "error.json", {"type": type(exc).__name__, "error": str(exc), "task_id": provider.task_id})
        raise
    finally:
        stop.set()
        if monitor.is_alive():
            monitor.join(timeout=5)
        ownership.restore()


if __name__ == "__main__":
    main()
