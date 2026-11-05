#!/usr/bin/env python3
"""Paired local ASR comparisons. Public/labeled WAV manifest and outputs stay private.

Run under the agent environment. No model acquisition, cloud inference or text
model is performed. Timings include a fresh native CLI/model load for each call.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import time
import wave

def normalize(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for column, actual in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (expected != actual)))
        previous = current
    return previous[-1]


def score(reference, hypothesis, probes=()):
    expected, actual = normalize(reference), normalize(hypothesis)
    words = expected.split()
    # A probe is a list of allowed spoken/written renderings, e.g. ["23",
    # "twenty three"]. Reference WER itself does not normalize numeric forms.
    tokens = f" {actual} "
    return {
        "reference_words": len(words),
        "word_errors": distance(words, actual.split()),
        "reference_characters": len(expected),
        "character_errors": distance(expected, actual),
        "silence_false_positive": int(not words and bool(actual)),
        "probes": len(probes),
        "probe_matches": sum(any(f" {normalize(option)} " in tokens for option in alternatives)
                             for alternatives in probes),
    }


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summary(records):
    groups = []
    for backend in sorted({r["backend"] for r in records}):
        rows = [r for r in records if r["backend"] == backend]
        good = [r for r in rows if r["status"] == "ok"]
        scored = [r for r in good if r["score"]["reference_words"]]
        words = sum(r["score"]["reference_words"] for r in scored)
        characters = sum(r["score"]["reference_characters"] for r in scored)
        probes = sum(r["score"]["probes"] for r in good)
        speech_cases = sum(bool(r["reference_words"]) for r in rows)
        latencies = [r["process_wall_ms"] for r in good if r["reference_words"]]
        groups.append({
            "backend": backend, "attempts": len(rows),
            "outcomes": dict(Counter(r["status"] for r in rows)),
            "speech_attempts_scored": len(scored), "speech_attempts_total": speech_cases,
            "wer_successful_only": sum(r["score"]["word_errors"] for r in scored) / words if words else None,
            "cer_successful_only": sum(r["score"]["character_errors"] for r in scored) / characters if characters else None,
            "probe_accuracy_successful_only": sum(r["score"]["probe_matches"] for r in good) / probes if probes else None,
            "silence_false_positives": sum(r["score"]["silence_false_positive"] for r in good),
            "latency_successful_speech_ms": {"p50": statistics.median(latencies), "p95": percentile(latencies, .95)} if latencies else None,
        })
    return groups


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_cases(manifest):
    cases = []
    ids = set()
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case.get("id"), str) or not case["id"] or case["id"] in ids:
            raise ValueError("Manifest requires unique nonempty string IDs")
        ids.add(case["id"])
        if not isinstance(case.get("reference"), str):
            raise ValueError("Manifest requires a string reference (empty is silence)")
        probes = case.get("probes", [])
        if not isinstance(probes, list) or any(not isinstance(p, list) or not p or
                any(not isinstance(value, str) or not normalize(value) for value in p) for p in probes):
            raise ValueError("probes must be lists of nonempty allowed string renderings")
        path = manifest.parent / case["audio"]
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                raise ValueError("All benchmark audio must be normalized 16 kHz mono int16 WAV")
            pcm = wav.readframes(wav.getnframes())
        cases.append({**case, "pcm": pcm, "probes": probes,
                      "pcm_sha256": hashlib.sha256(pcm).hexdigest()})
    if not cases:
        raise ValueError("Manifest is empty")
    return cases


async def benchmark(args):
    from local_voice.config import Config
    from local_voice.stt import transcribe_pcm

    config = Config.from_env()
    # The selected app default remains untouched. Both candidates are validated
    # explicitly; no silent fallback or missing model download is allowed.
    configs = {backend: replace(config, stt_backend=backend) for backend in ("parakeet", "whistle")}
    for candidate in configs.values():
        candidate.validate()
    cases = load_cases(args.manifest)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    with (args.output / "attempts.jsonl").open("w", encoding="utf-8") as output:
        for iteration in range(args.repeats):
            # Alternate order to reduce consistent thermal/cache-order bias.
            order = ("parakeet", "whistle") if iteration % 2 == 0 else ("whistle", "parakeet")
            for case in cases:
                for backend in order:
                    timings = {}
                    started = time.monotonic()
                    record = {"case_id": case["id"], "iteration": iteration,
                              "fixture_kind": case.get("kind", "unspecified"),
                              "backend": backend, "pcm_sha256": case["pcm_sha256"],
                              "audio_seconds": len(case["pcm"]) / 32000,
                              "reference_words": len(normalize(case["reference"]).split()),
                              "status": "ok", "score": None, "error_type": None}
                    try:
                        hypothesis = await transcribe_pcm(configs[backend], case["pcm"], timings=timings)
                        record["score"] = score(case["reference"], hypothesis, case["probes"])
                        record["hypothesis_sha256"] = hashlib.sha256(hypothesis.encode()).hexdigest()
                        record["normalized_hypothesis_sha256"] = hashlib.sha256(normalize(hypothesis).encode()).hexdigest()
                        if args.save_transcripts:
                            record.update(reference=case["reference"], hypothesis=hypothesis)
                    except TimeoutError:
                        record.update(status="timeout", error_type="TimeoutError")
                    except Exception as error:
                        record.update(status="error", error_type=type(error).__name__)
                    record.update(process_wall_ms=(time.monotonic() - started) * 1000, timings=timings)
                    records.append(record)
                    output.write(json.dumps(record, allow_nan=False) + "\n")
                    output.flush()
    report = {"timing_mode": "fresh-process/model-load per non-silent utterance; OS caches may be warm",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "comparison": "different ASR model; not a quality-preserving kernel optimization",
              "silence_mode": "shared adapter fast path, not raw model silence decoding",
              "normalization": "Unicode word tokens, case/punctuation folded; numbers are not rewritten",
              "manifest_sha256": sha(args.manifest), "repeats": args.repeats,
              "settings": {"language": "en", "stt_threads": config.stt_threads,
                           "whistle_device": "cpu", "whistle_audio_depth": "default-full",
                           "parakeet_device": "cuda", "parakeet_timestamps": "none",
                           "checkpoint_or_decode_changed": False},
              "models": {"parakeet_sha256": sha(config.parakeet_model),
                         "parakeet_bytes": config.parakeet_model.stat().st_size,
                         "whistle_sha256": sha(config.whistle_model),
                         "whistle_bytes": config.whistle_model.stat().st_size,
                         "needle_executable_sha256": sha(config.needle_bin),
                         "parakeet_executable_sha256": sha(config.parakeet_bin)},
              "summary": summary(records),
              "cases": [{"case_id": case["id"], "fixture_kind": case.get("kind", "unspecified"),
                         "pcm_sha256": case["pcm_sha256"], "audio_seconds": len(case["pcm"]) / 32000,
                         "models": summary([r for r in records if r["case_id"] == case["id"]])}
                        for case in cases]}
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    if any(r["status"] != "ok" for r in records):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Local JSONL: id, audio (relative WAV), reference, optional probes [[alternatives]]")
    parser.add_argument("--output", type=Path, default=Path(".local/benchmarks/whistle"))
    parser.add_argument("--repeats", type=int, choices=range(1, 101), default=5)
    parser.add_argument("--save-transcripts", action="store_true", help="Explicitly retain public corpus text in the private report")
    asyncio.run(benchmark(parser.parse_args()))
