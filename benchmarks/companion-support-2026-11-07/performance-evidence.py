#!/usr/bin/env python3
"""Export allowlisted saved counters and aggregate them; never make model calls."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics


HERE = Path(__file__).resolve().parent
FIELDS = (
    "model", "suite", "stage", "case_id", "call_id", "runtime",
    "completed_timing", "finish_reason", "failure", "text_observed",
    "tool_calls_observed", "first_text_seconds", "wall_seconds",
    "server_prompt_seconds", "server_decode_seconds", "decode_tokens_per_second",
    "output_tokens", "reported_prompt_tokens", "cached_prompt_tokens",
    "fresh_prompt_tokens", "ollama_total_duration_ns", "ollama_load_duration_ns",
    "ollama_prompt_eval_count", "ollama_prompt_eval_duration_ns",
    "ollama_eval_count", "ollama_eval_duration_ns", "llama_cache_n",
    "llama_prompt_n", "llama_prompt_ms", "llama_predicted_n",
    "llama_predicted_ms", "llama_predicted_per_second",
)
METRICS = (
    "first_text_seconds", "wall_seconds", "server_prompt_seconds",
    "server_decode_seconds", "decode_tokens_per_second", "output_tokens",
    "reported_prompt_tokens", "cached_prompt_tokens", "fresh_prompt_tokens",
)


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def scan_stream(path, runtime):
    """Read one event at a time; do not retain/export any generated text."""
    done, text, tools = None, False, False
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            if runtime == "ollama":
                message = event.get("message") or {}
                text |= bool(message.get("content"))
                tools |= bool(message.get("tool_calls"))
                if event.get("done"):
                    done = {key: event.get(key) for key in (
                        "done_reason", "total_duration", "load_duration",
                        "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration",
                    )}
            else:
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    text |= bool(delta.get("content"))
                    tools |= bool(delta.get("tool_calls"))
    return done, text, tools


def export_saved(runs_root, output):
    rows, sources = [], {}
    project = runs_root.parent

    def record(path):
        # Provenance has relative filenames and hashes, not prompts or host paths.
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sources[path.relative_to(project).as_posix()] = digest.hexdigest()

    def ollama_row(call_id, suite, timing, stream_path, complete):
        done, text, tools = scan_stream(stream_path, "ollama")
        if done is None:
            raise ValueError(f"Missing terminal event: {call_id}")
        if suite == "eq":
            case_id, stage = timing["scenario_id"], timing["stage"]
            first, wall = timing["first_answer_token_seconds"], timing["seconds"]
        else:
            case_id, stage = call_id.split("-")[1], "agent_response"
            first, wall = timing.get("first_content_seconds"), timing.get("wall_seconds")
        decode_seconds = done["eval_duration"] / 1e9
        row = dict(model="qwen", suite=suite, stage=stage, case_id=case_id,
                   call_id=call_id, runtime="ollama", completed_timing=int(complete),
                   finish_reason=done["done_reason"],
                   failure="output_cap" if done["done_reason"] == "length" else "",
                   text_observed=int(text), tool_calls_observed=int(tools),
                   first_text_seconds=first, wall_seconds=wall,
                   server_prompt_seconds=done["prompt_eval_duration"] / 1e9,
                   server_decode_seconds=decode_seconds,
                   decode_tokens_per_second=done["eval_count"] / decode_seconds,
                   output_tokens=done["eval_count"], reported_prompt_tokens=done["prompt_eval_count"])
        for key in ("total_duration", "load_duration", "prompt_eval_count",
                    "prompt_eval_duration", "eval_count", "eval_duration"):
            row["ollama_" + key + ("_ns" if "duration" in key else "")] = done[key]
        return row

    qwen_eq = runs_root / "qwen-eq10"
    record(qwen_eq / "calls.jsonl")
    for line in (qwen_eq / "calls.jsonl").read_text().splitlines():
        timing = json.loads(line)
        if timing["error"]:
            raise ValueError("Unexpected Qwen EQ error")
        call_id = timing["label"]
        stream_path = qwen_eq / "calls" / f"{call_id}.response.jsonl"
        record(stream_path)
        record(qwen_eq / "calls" / f"{call_id}.request.json")
        rows.append(ollama_row(call_id, "eq", timing, stream_path, True))

    for request in sorted((runs_root / "qwen-retail10/calls").glob("*/ollama-request.json")):
        directory = request.parent
        record(request)
        stream_path, timing_path = directory / "ollama-events.jsonl", directory / "timing.json"
        record(stream_path)
        timing = read_json(timing_path) if timing_path.exists() else {}
        if timing_path.exists():
            record(timing_path)
        row = ollama_row(directory.name, "retail", timing, stream_path, timing_path.exists())
        if not timing and row["failure"] != "output_cap":
            raise ValueError("Unexplained missing Qwen timing")
        rows.append(row)

    for suite, folder in (("eq", "eq10"), ("retail", "retail10")):
        for path in sorted((runs_root / "nanbeige-iq2m" / folder / "calls").glob("*/timing.json")):
            timing, directory = read_json(path), path.parent
            if timing["error"] or timing["finish_reason"] not in ("stop", "tool_calls"):
                raise ValueError("Unexpected Nanbeige incomplete call")
            for name in ("timing.json", "request.json", "response.jsonl"):
                record(directory / name)
            _, text, tools = scan_stream(directory / "response.jsonl", "llama.cpp")
            call_id, counters, usage = directory.name, timing["timings"], timing["usage"]
            row = dict(model="nanbeige", suite=suite,
                       stage=call_id.split("-")[-1] if suite == "eq" else "agent_response",
                       case_id=call_id.split("-")[1], call_id=call_id, runtime="llama.cpp",
                       completed_timing=1, finish_reason=timing["finish_reason"], failure="",
                       text_observed=int(text), tool_calls_observed=int(tools),
                       first_text_seconds=timing["first_content_seconds"], wall_seconds=timing["wall_seconds"],
                       server_prompt_seconds=counters["prompt_ms"] / 1000,
                       server_decode_seconds=counters["predicted_ms"] / 1000,
                       decode_tokens_per_second=counters["predicted_per_second"],
                       output_tokens=usage["completion_tokens"], reported_prompt_tokens=usage["prompt_tokens"],
                       cached_prompt_tokens=counters["cache_n"], fresh_prompt_tokens=counters["prompt_n"])
            if counters["prompt_n"] + counters["cache_n"] != usage["prompt_tokens"]:
                raise ValueError("Nanbeige prompt/cache accounting mismatch")
            for key in ("cache_n", "prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "predicted_per_second"):
                row["llama_" + key] = counters[key]
            rows.append(row)

    for name in ("run_qwen_eq.py", "run_qwen_retail.py", "nanbeige_provider.py"):
        record(project / name)
    if len({(r["model"], r["suite"], r["call_id"]) for r in rows}) != len(rows):
        raise ValueError("Duplicate candidate call IDs")
    with (output / "candidate-call-metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    write_json(output / "source-hashes.json", {
        "source_base": "original local evaluation project; paths below are relative",
        "hash_algorithm": "sha256", "files": sources,
        "export_policy": "Allowlisted counters only; source content remains local. No inference rerun.",
    })


def distribution(values):
    values = sorted(float(v) for v in values if v != "" and v is not None)
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite metric")
    position = .95 * (len(values) - 1)
    index = int(position)
    p95 = values[index] + (values[min(index + 1, len(values) - 1)] - values[index]) * (position - index)
    return dict(n=len(values), mean=statistics.mean(values), p50=statistics.median(values), p95=p95)


def summarize(output):
    with (output / "candidate-call-metrics.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    cohorts = []
    for model in ("qwen", "nanbeige"):
        for suite, stage in (("eq", None), ("eq", "dialogue"), ("eq", "debrief"), ("retail", None)):
            attempted = [r for r in rows if r["model"] == model and r["suite"] == suite
                         and (stage is None or r["stage"] == stage)]
            complete = [r for r in attempted if r["completed_timing"] == "1"]
            cohorts.append(dict(model=model, suite=suite, stage=stage or "all",
                attempts=len(attempted), completed_timing=len(complete),
                missing_completed_timing=len(attempted) - len(complete),
                text_bearing_calls=sum(r["first_text_seconds"] != "" for r in complete),
                tool_only_calls=sum(r["text_observed"] == "0" and r["tool_calls_observed"] == "1" for r in complete),
                failures=[dict(call_id=r["call_id"], reason=r["failure"]) for r in attempted if r["failure"]],
                metrics={key: distribution(r[key] for r in complete) for key in METRICS}))
    write_json(output / "summary.json", {
        "aggregation": "Completed calls only; nulls excluded independently per metric. EQ stage subgroups overlap EQ all.",
        "p95_method": "Linear interpolation at 0.95 * (n - 1) in sorted observations",
        "first_text_definition": "Client request start to first nonempty content chunk; not first tool event or audible response",
        "cohorts": cohorts,
    })
    for cohort in cohorts:
        if cohort["stage"] == "all":
            print(cohort["model"], cohort["suite"],
                  f'{cohort["completed_timing"]}/{cohort["attempts"]} timings',
                  {k: round(cohort["metrics"][k]["p50"], 3) for k in METRICS[:5]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, help="Optional original saved runs directory; export counters before aggregation")
    parser.add_argument("--output", type=Path, default=HERE / "performance")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.runs_root:
        export_saved(args.runs_root.resolve(), args.output)
    summarize(args.output)


if __name__ == "__main__":
    main()
