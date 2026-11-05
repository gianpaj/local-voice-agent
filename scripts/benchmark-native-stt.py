#!/usr/bin/env python3
"""Paired same-checkpoint CLI/resident STT measurements; outputs are local metadata."""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid


def support_module(path):
    spec = importlib.util.spec_from_file_location("stt_comparison_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resident_rss(pid):
    if not pid:
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


async def gpu_snapshot():
    process = await asyncio.create_subprocess_exec(
        "nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), 2)
        if process.returncode == 0:
            rows = [line.split(",") for line in raw.decode().splitlines()]
            return [{"vram_mib": int(row[0]), "gpu_percent": int(row[1])} for row in rows]
    except (TimeoutError, ValueError):
        process.kill()
        await process.wait()
    return []


async def benchmark(args):
    import httpx
    from local_voice.config import Config
    from local_voice.resident_stt import ResidentParakeet
    from local_voice.stt import transcribe_pcm

    helpers = support_module(args.support_script)
    base = Config.from_env()
    if getattr(base, "stt_backend", "parakeet") != "parakeet":
        base = replace(base, stt_backend="parakeet")
    configs = {"cli": replace(base, parakeet_mode="cli"),
               "resident": replace(base, parakeet_mode="resident", parakeet_library=args.library.resolve(),
                                   parakeet_bindings=args.bindings.resolve())}
    for config in configs.values():
        config.validate()
    cases = helpers.load_cases(args.manifest)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Hashing is outside timed inference intervals.
    identity = {"checkpoint_sha256": helpers.sha(base.parakeet_model),
                "checkpoint_bytes": base.parakeet_model.stat().st_size,
                "cli_sha256": helpers.sha(base.parakeet_bin), "library_sha256": helpers.sha(args.library),
                "manifest_sha256": helpers.sha(args.manifest)}
    engine = ResidentParakeet(configs["resident"])
    samples, records, pairs = [], [], []
    done = asyncio.Event()

    async def sample_resources():
        while not done.is_set():
            # Sampling overhead is shared by both modes; GPU memory is device-wide.
            samples.append({"elapsed_ms": (time.monotonic() - began) * 1000,
                            "gpu": await gpu_snapshot(),
                            "resident_worker_rss_bytes": resident_rss(engine.process.pid if engine.process else None)})
            try:
                await asyncio.wait_for(done.wait(), 0.5)
            except TimeoutError:
                pass

    async def ollama_residency():
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            try:
                response = await client.get(base.ollama_url + "/api/ps")
                response.raise_for_status()
                return [{key: model.get(key) for key in ("name", "size", "size_vram", "context_length")}
                        for model in response.json().get("models", [])]
            except (httpx.HTTPError, ValueError):
                return None

    residency_before = await ollama_residency()
    began = time.monotonic()
    sampling = asyncio.create_task(sample_resources())
    try:
        startup = await engine.load()
        first_inference = True
        attempts_path = args.output / "attempts.jsonl"
        with attempts_path.open("w", encoding="utf-8") as output:
            os.chmod(attempts_path, 0o600)
            for iteration in range(args.repeats):
                for case in cases:
                    order = ("cli", "resident") if (iteration + cases.index(case)) % 2 == 0 else ("resident", "cli")
                    hypotheses = {}
                    for mode in order:
                        timings = {}
                        started = time.monotonic()
                        speech = bool(helpers.normalize(case["reference"]))
                        record = {"case_id": case["id"], "iteration": iteration, "backend": "parakeet-" + mode,
                                  "fixture_kind": case.get("kind", "unspecified"), "pcm_sha256": case["pcm_sha256"],
                                  "audio_seconds": len(case["pcm"]) / 32000,
                                  "reference_words": len(helpers.normalize(case["reference"]).split()),
                                  "phase": "fresh-process/model-load" if mode == "cli" and speech else
                                           "first-inference" if first_inference and speech else "warm-inference" if speech else "silence-fast-path",
                                  "status": "ok", "score": None, "error_type": None}
                        try:
                            hypothesis = await transcribe_pcm(configs[mode], case["pcm"], timings=timings,
                                                              **({"engine": engine} if mode == "resident" else {}))
                            hypotheses[mode] = hypothesis
                            record["score"] = helpers.score(case["reference"], hypothesis, case["probes"])
                            record["hypothesis_sha256"] = hashlib.sha256(hypothesis.encode()).hexdigest()
                            record["normalized_hypothesis_sha256"] = hashlib.sha256(helpers.normalize(hypothesis).encode()).hexdigest()
                        except TimeoutError:
                            record.update(status="timeout", error_type="TimeoutError")
                        except Exception as error:
                            record.update(status="error", error_type=type(error).__name__)
                        record.update(process_wall_ms=(time.monotonic() - started) * 1000, timings=timings)
                        if mode == "resident" and speech:
                            first_inference = False
                        records.append(record)
                        output.write(json.dumps(record, allow_nan=False) + "\n")
                        output.flush()
                    pairs.append({"case_id": case["id"], "iteration": iteration, "pcm_sha256": case["pcm_sha256"],
                                  "both_succeeded": len(hypotheses) == 2,
                                  "exact_text_equal": hypotheses.get("cli") == hypotheses.get("resident") if len(hypotheses) == 2 else None,
                                  "normalized_text_equal": helpers.normalize(hypotheses["cli"]) == helpers.normalize(hypotheses["resident"]) if len(hypotheses) == 2 else None})
    finally:
        done.set()
        await sampling
        await engine.close()
    residency_after = await ollama_residency()
    gpu_values = [g["vram_mib"] for sample in samples for g in sample["gpu"]]
    rss_values = [sample["resident_worker_rss_bytes"] for sample in samples if sample["resident_worker_rss_bytes"] is not None]
    report = {"schema_version": 1, "run_id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
              "comparison": "same Parakeet checkpoint/settings; fresh CLI versus persistent native worker",
              "identity": identity, "startup": startup,
              "settings": {"backend": "cuda", "timestamps": "none", "stt_threads": base.stt_threads,
                           "model_or_quantization_changed": False, "repeats": args.repeats,
                           "normalization": "Unicode word tokens, case/punctuation folded; numbers not rewritten",
                           "silence_mode": "adapter fast path, not raw model decoding",
                           "resource_sampling": "device-wide nvidia-smi every approximately 0.5s; worker RSS from /proc"},
              "summary": helpers.summary(records), "pairs": pairs,
              "by_phase": {phase: helpers.summary([r for r in records if r["phase"] == phase])
                           for phase in sorted({r["phase"] for r in records})},
              "parity": {"pairs": len(pairs), "both_succeeded": sum(p["both_succeeded"] for p in pairs),
                         "exact_matches": sum(p["exact_text_equal"] is True for p in pairs),
                         "normalized_matches": sum(p["normalized_text_equal"] is True for p in pairs)},
              "resources": {"peak_device_vram_mib": max(gpu_values) if gpu_values else None,
                            "peak_worker_rss_bytes": max(rss_values) if rss_values else None,
                            "ollama_before": residency_before, "ollama_after": residency_after,
                            "samples": samples},
              "cases": [{"case_id": case["id"], "fixture_kind": case.get("kind", "unspecified"),
                         "pcm_sha256": case["pcm_sha256"], "audio_seconds": len(case["pcm"]) / 32000,
                         "modes": helpers.summary([r for r in records if r["case_id"] == case["id"]])} for case in cases]}
    summary_path = args.output / "summary.json"
    summary_path.write_text(json.dumps(report, allow_nan=False, indent=2) + "\n")
    os.chmod(summary_path, 0o600)
    print(json.dumps({"summary": str(summary_path), "parity": report["parity"], "modes": report["summary"]}, indent=2))
    if any(record["status"] != "ok" for record in records):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--support-script", type=Path, default=Path(__file__).with_name("benchmark-stt.py"),
                        help="Shared corpus/scoring implementation from the Whistle comparison")
    parser.add_argument("--output", type=Path, default=Path(".local/benchmarks/native-parakeet"))
    parser.add_argument("--repeats", type=int, choices=range(1, 101), default=5)
    asyncio.run(benchmark(parser.parse_args()))
