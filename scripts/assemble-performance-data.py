#!/usr/bin/env python3
"""Compile measured local experiment summaries into the offline report schema.

Inputs stay local. This deliberately extracts metadata, not raw utterances,
hypotheses, environment values, or machine paths.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import metrics as analysis


DEFINITIONS = {
    "latency_p50_ms": ("STT adapter median", "ms", "lower", "Completed PCM utterance to transcription result; includes a fresh process/model load in CLI mode, but not in an already-loaded resident worker. Silence fast paths excluded."),
    "latency_p95_ms": ("STT adapter p95", "ms", "lower", "Descriptive interpolated 95th percentile of successful speech requests in this small fixed corpus. Check failure counts separately; not a production service guarantee."),
    "wer_pct": ("Word error rate", "%", "lower", "Word edits / reference words over successful speech attempts, with Unicode case/punctuation folding but no number rewriting. Formatting differences can count as errors; entity checks add a semantic view."),
    "cer_pct": ("Character error rate", "%", "lower", "Character edits / reference characters under the same normalization. Lower is better; a tiny English corpus does not establish broad accent/noise quality."),
    "probe_accuracy_pct": ("Name / number probe accuracy", "%", "higher", "Matched named-entity/number probes, allowing documented spoken/written number alternatives. Missing probes are not scored as zero."),
    "first_answer_p50_ms": ("LLM first answer median", "ms", "lower", "LLM request to first actual answer-text token/frame, not an empty chunk, internal thinking token or tool result. Prompts/history must match before comparing engines."),
    "first_pcm_p50_ms": ("TTS first generated PCM median", "ms", "lower", "TTS synthesis request to first PCM chunk; may contain leading silence. This is not browser playback or physical speaker onset."),
    "agent_ttfa_p50_ms": ("Agent generated-audio TTFA median", "ms", "lower", "Inline VAD stop notification to first generated TTS PCM. Excludes the earlier configured 600 ms endpointing wait, network, browser playout and speaker hardware."),
    "server_speaking_p50_ms": ("Server speaking-event median", "ms", "lower", "Inline VAD stop to server BotStartedSpeaking event, kept separate from first generated PCM and from acoustic onset."),
    "headless_first_pcm_p50_ms": ("Headless completed-input → PCM median", "ms", "lower", "Rustvani experiment: injected completed utterance to PCM sink, with full LLM-reply buffering. No VAD, browser transport or barge-in. NOT comparable to streaming browser TTFA."),
    "browser_playback_ms": ("Browser / acoustic onset", "ms", "lower", "Not measured. Decoded audio energy proves received non-silent audio, not the time a human heard it."),
    "runtime_rss_mib": ("Sampled process-tree RSS", "MiB", "lower", "Sampled RSS of the experiment process tree; excludes the preexisting Ollama daemon. Sampling can miss peaks; not total host RAM."),
    "checkpoint_mib": ("Checkpoint size", "MiB", "lower", "Bytes on disk / 2^20. NOT runtime RAM or VRAM. All selected model files are under the user's 8 GB per-model limit."),
    "native_mel_p50_ms": ("Native mel median", "ms", "lower", "Native feature extraction counter for successful resident speech requests. Zero/unreported counters remain missing; marginal medians must not be added together."),
    "native_encode_p50_ms": ("Native encoder median", "ms", "lower", "CUDA encoder counter for successful resident speech requests, with the same precision and checkpoint. First inference can include kernel warmup."),
    "native_decode_p50_ms": ("Native decoder median", "ms", "lower", "Host decoder counter for successful resident speech requests. No OpenBLAS was linked in this comparison."),
    "total_p50_ms": ("Request lifetime median", "ms", "lower", "Per-stage request lifetime through completion or cancellation cleanup. Check per-sample outcomes; TTS can include streaming backpressure."),
    "rtf_p50": ("Real-time factor median", "×", "lower", "Processing seconds / generated or submitted audio seconds. Below 1 is faster than real time; does not measure browser delivery."),
}


def read(path):
    return json.loads(path.read_text())


def identity(path):
    return {"artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "artifact": path.name}


def percentile(values, fraction=0.5):
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return analysis.percentile(values, fraction) if values else None


def source(label, url):
    return {"label": label, "url": url}


def safe_rust_settings(run):
    # Fixed experiment contract, never embed arbitrary upstream settings/prompts.
    settings = run.get("model_settings", {})
    allowed = {
        "llm": {"context":4096, "history":"single-turn", "max_tokens":128, "model":"local-voice-qwen", "reasoning_effort":"none", "system_prompt":"project-baseline", "temperature":0.5},
        "stt": {"backend":"cuda", "checkpoint_format":"GGUF-Q8_0", "input_rate":16000, "runtime":"existing-transcribe-cli", "threads":4, "timestamps":"none"},
        "tts": {"buffering":"full_reply", "device":"cpu", "model":"phonon-31m-en", "quantization":"q8_0", "runtime":"ptts", "sample_rate":24000, "threads":4, "version":"0.5.0"},
    }
    return {stage:{key:expected if settings.get(stage, {}).get(key) == expected else None for key,expected in values.items()} for stage,values in allowed.items()}


def safe_rust_sample(run):
    numeric = ("audio_duration_ms", "audio_energy_sum", "audio_input_duration_ms", "audio_samples", "bridge_shutdown_forced", "browser_playback_measured", "injected_utterance_to_first_pcm_sink_ms", "llm_model_adapter_ms", "llm_model_first_answer_token_ms", "llm_to_tts_framework_handoff_ms", "pipeline_shutdown_forced", "stt_model_adapter_ms", "stt_to_llm_framework_handoff_ms", "tts_model_adapter_ms", "tts_model_first_pcm_ms", "vad_included")
    sample = {key:run.get(key) if isinstance(run.get(key), (int,float,bool)) else None for key in numeric}
    sample.update(id=run["fixture_id"], fixture_sha256=run["fixture_sha256"], model_settings=safe_rust_settings(run))
    resources = run.get("resource_observations", {})
    sample["resource_observations"] = {key:resources.get(key) for key in ("experiment_process_tree_sampled_peak_rss_mib", "rss_sample_interval_ms", "includes_existing_ollama_rss", "gpu_total_sampled_peak_mib", "gpu_sample_interval_ms", "gpu_includes_all_processes")}
    return sample


def asr_cases(document, *, native=False):
    group = "Parakeet: same checkpoint, CLI vs resident" if native else "ASR substitution: same public corpus"
    boundary = "16 kHz completed PCM → adapter result; non-silent utterances"
    result = []
    for summary in document["summary"]:
        backend = summary["backend"]
        whistle = backend == "whistle"
        resident = backend.endswith("resident")
        latency = summary.get("latency_successful_speech_ms") or {}
        measures = {"latency_p50_ms": latency.get("p50"), "latency_p95_ms": latency.get("p95")}
        for metric, field in (("wer_pct", "wer_successful_only"), ("cer_pct", "cer_successful_only"), ("probe_accuracy_pct", "probe_accuracy_successful_only")):
            value = summary.get(field)
            measures[metric] = value * 100 if value is not None else None
        checkpoint = document.get("identity", {}).get("checkpoint_bytes") if native else document["models"].get("whistle_bytes" if whistle else "parakeet_bytes")
        measures["checkpoint_mib"] = checkpoint / 2**20 if checkpoint is not None else None
        samples = []
        for case in document.get("cases", []):
            match = next((m for m in case.get("modes" if native else "models", []) if m["backend"] == backend), None)
            if match:
                samples.append({"id": case["case_id"], "kind": case["fixture_kind"], "pcm_sha256": case["pcm_sha256"], "audio_seconds": case["audio_seconds"], **{k: match.get(k) for k in ("attempts", "outcomes", "speech_attempts_scored", "wer_successful_only", "cer_successful_only", "probe_accuracy_successful_only", "silence_false_positives", "latency_successful_speech_ms")}})
        statuses = summary.get("outcomes", {})
        result.append({
            "id": ("native-" if native else "substitution-") + backend,
            "component": "stt", "label": "Whistle / Needle CPU" if whistle else "Parakeet / resident CUDA" if resident else "Parakeet / fresh CLI CUDA",
            "model": "Whistle" if whistle else "Parakeet TDT 0.6B v3 Q8_0",
            "engine": "Needle native 3.3.1" if whistle else "transcribe.cpp 0.4.1 / CUDA, no host BLAS",
            "mode": "resident model" if resident else "fresh process + model load (OS cache may be warm)",
            "boundary": boundary, "comparison_group": group,
            "decision": "opt_in" if whistle else "candidate" if resident else "baseline",
            "metrics": measures,
            "outcomes": {"attempts": summary["attempts"], "failed": sum(v for k, v in statuses.items() if k != "ok"), "statuses": statuses, "speech_attempts_scored": summary["speech_attempts_scored"], "silence_false_positives": summary["silence_false_positives"]},
            "quality": {"model_substitution": not native, "same_settings_within_model": True, "parity": document.get("parity"), "normalization": document.get("normalization", document["settings"].get("normalization")), "scope": "6 English speech fixtures plus digital silence; repeats are not independent speakers"},
            "configuration": document["settings"],
            "methodology": "Five alternating-order paired repetitions of seven identical public/synthetic PCM fixtures. Timed runs serialized with other experiments. Latency excludes silence; all attempts/outcomes remain visible.",
            "notes": ["Whistle is faster on several short clips, but has worse tail latency and recognition errors here. Keep Parakeet as default; Whistle is a small optional CPU backend."] if whistle else ["Same checkpoint and precision; residency removes per-utterance process/CUDA/model initialization."] if native else ["This is the original quality reference, not a resident throughput measurement."],
            "limitations": ["Small English corpus; no meeting/noise/accent quality guarantee.", "Silence is handled by the shared adapter fast path, not raw-model decoding.", "Checkpoint size is not runtime memory."] + (["Device-wide peak includes simultaneous resident allocation and CLI model loading; not a per-mode VRAM comparison."] if native else ["Different model: this is not a lossless kernel optimization. Runtime memory peaks were not measured."]),
            "samples": samples,
            "evidence": {"manifest_sha256": document.get("manifest_sha256", document.get("identity", {}).get("manifest_sha256")), "identity": document.get("identity", document.get("models")), "resident_worker_startup": document.get("startup") if resident else None, "shared_experiment_resources_not_per_mode": {k:v for k,v in document.get("resources", {}).items() if k not in {"samples", "peak_worker_rss_bytes"}}, "resident_worker_sampled_peak_rss_bytes": document.get("resources", {}).get("peak_worker_rss_bytes") if resident else None, "created_at": document.get("created_at")},
            "sources": [source("Matching Parakeet export", "https://huggingface.co/handy-computer/parakeet-tdt-0.6b-v3-gguf"), source("Needle runtime", "https://github.com/cactus-compute/needle")],
        })
    return result


def rust_cases(document):
    runs = document["runs"]
    return [{
        "id": "rustvani-headless", "component": "framework", "label": "Rustvani isolated prototype", "model": "Same Parakeet + Qwen + Phonon",
        "engine": "Rustvani 0.4.0-dev.27 + private Python model bridge", "mode": "headless, full-reply buffered TTS",
        "boundary": "Injected completed utterance → PCM sink; no browser or VAD", "comparison_group": "Rustvani feasibility only", "decision": "not_adopted",
        "metrics": {"headless_first_pcm_p50_ms": percentile([r["injected_utterance_to_first_pcm_sink_ms"] for r in runs]), "first_answer_p50_ms": percentile([r["llm_model_first_answer_token_ms"] for r in runs]), "first_pcm_p50_ms": percentile([r["tts_model_first_pcm_ms"] for r in runs]), "browser_playback_ms": None, "runtime_rss_mib": max((r["resource_observations"]["experiment_process_tree_sampled_peak_rss_mib"] for r in runs), default=None)},
        "quality": {"same_model_settings": True, "model_quality_improvement_claimed": False, "framework_speedup_claimed": False, "received_nonzero_audio": all(r["audio_energy_sum"] > 0 for r in runs)},
        "outcomes": {"attempts": len(runs), "failed": sum(r["status"] != "ok" for r in runs), "forced_shutdowns": sum(r["bridge_shutdown_forced"] or r["pipeline_shutdown_forced"] for r in runs)},
        "configuration": safe_rust_settings(runs[0]) if runs else {},
        "methodology": "Two actual public-utterance round trips through the Rust framework, existing local model services and a bounded stdio bridge. Warm Phonon and Ollama; STT still uses the fresh CLI.",
        "notes": ["Actual framework handoffs were tens of microseconds in these two runs; not evidence that rewriting orchestration will improve model latency.", "Keep Pipecat in production. Rustvani is feasible, but not a drop-in browser replacement."],
        "limitations": ["Only two runs, no comparative Pipecat headless control.", "Full LLM reply buffered before TTS: unlike the production streaming pipeline.", "Browser transport, history, barge-in, reconnect and tool execution not exercised.", "RSS excludes existing Ollama; sampled total GPU is device-wide and can miss peaks.", "Upstream default logs/providers and tool-loop semantics require further integration work."],
        "samples": [safe_rust_sample(r) for r in runs],
        "evidence": {"revision": runs[0]["rustvani_revision"] if runs else None, "raw_text_audio_saved": False},
        "sources": [source("Pinned Rustvani framework", "https://github.com/Allenmylath/rustvani/tree/b134ad055c754efaa690fec72f530d5390fd6180")],
    }]


def browser_cases(path, session_ids, *, mode="cli", tag="baseline"):
    records, counts = analysis.read_records([path])
    selected = [r for r in records if r.get("session_id") in session_ids]
    rows = []
    for component, stage, key, label, model in (
        ("llm", "llm", "first_answer_token_ms", "Qwen / actual answer tokens", "local-voice-qwen (Qwen3.5 9B Q4_K_M)"),
        ("tts", "tts", "first_pcm_ms", "Phonon / resident Rust CPU", "Phonon 31M English Q8_0"),
        ("pipeline", None, "agent_ttfa_ms", "Pipecat / streaming browser baseline", "Parakeet CLI → Qwen → Phonon"),
    ):
        values = [r for r in selected if r["record_type"] == ("turn" if component == "pipeline" else "stage") and (not stage or r.get("stage") == stage)]
        key_out = {"llm":"first_answer_p50_ms", "tts":"first_pcm_p50_ms", "pipeline":"agent_ttfa_p50_ms"}[component]
        measured = {key_out: percentile([r["metrics"].get(key) for r in values]), "browser_playback_ms": None}
        if component == "pipeline":
            measured["server_speaking_p50_ms"] = percentile([r["metrics"].get("server_speaking_ms") for r in values])
        statuses = Counter(r["status"] for r in values)
        if component != "pipeline":
            measured["total_p50_ms"] = percentile([r["metrics"].get("total_ms") for r in values])
        if component == "tts":
            measured["rtf_p50"] = percentile([r["metrics"].get("rtf") for r in values])
        rows.append({"id": "browser-" + tag + "-" + component, "component":component, "label":label.replace("baseline",tag) + (" / resident STT" if mode == "resident" else ""), "model":model.replace("Parakeet CLI", "Parakeet resident") if mode == "resident" else model, "engine":"Pipecat 1.15.0", "mode":"warm, real Chromium over private HTTPS", "boundary": DEFINITIONS[key_out][3], "comparison_group": "Browser " + tag + " / " + component, "decision":"validated" if mode == "resident" else "baseline", "metrics":measured,
            "outcomes":{"attempts":len(values), "failed":sum(v for k,v in statuses.items() if k in {"error","timeout"}), "statuses":dict(statuses), "missing_first_observation":sum(r["metrics"].get(key) is None for r in values)},
            "quality":{"model_or_voice_changed":False, "purpose":"Timing semantics, real decoded audio, interruption/reconnect checks; not a controlled model-quality benchmark."},
            "configuration":{"stt_mode":mode, "stt_backend":"parakeet", "stt_threads":4, "phonon_threads":4, "llm_context":4096, "llm_max_output_tokens":128, "reasoning_effort":"none", "vad_stop_seconds":0.6},
            "methodology":"Explicitly selected corrected browser validation sessions only. Historical initial buggy measurements are excluded. Counts include interrupted/cancelled outcomes; medians use available numeric observations, with missing observations counted. Browser groups have different prompts/history and are not a controlled speedup comparison.",
            "notes":["No LLM checkpoint, quantization, context, voice, TTS sample rate or reasoning policy was reduced for speed."],
            "limitations":["Small browser validation sample, not a stable performance baseline; prompts/history vary.", "Interrupted and successful requests remain explicitly identified per sample.", "Chrome ran on the GPU host through the private HTTPS origin; a separate remote device has not been tested.", "No acoustic onset measurement or tool calling test."],
            "samples":[{"id":r.get("request_id") or r.get("turn_id"), "session_id":r["session_id"], "status":r["status"], **r["metrics"]} for r in values], "evidence":{"selected_sessions":session_ids, "read_counts":dict(counts)},
            "sources":[source("Pipecat tracing utilities", "https://docs.pipecat.ai/api-reference/server/utilities/opentelemetry"), source("Phonon model", "https://huggingface.co/gradium/phonon-31m-en")],
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whistle", type=Path, required=True)
    parser.add_argument("--rustvani", type=Path, required=True)
    parser.add_argument("--native", type=Path)
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--optimized-metrics", type=Path)
    parser.add_argument("--optimized-session", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path(".local/reports/comparison.json"))
    args = parser.parse_args()
    rows = asr_cases(read(args.whistle)) + rust_cases(read(args.rustvani))
    if args.native:
        rows = asr_cases(read(args.native), native=True) + rows
        attempts_path = args.native.with_name("attempts.jsonl")
        if attempts_path.is_file():
            attempts = [json.loads(line) for line in attempts_path.read_text().splitlines() if line.strip()]
            resident = next(row for row in rows if row["id"] == "native-parakeet-resident")
            speech = [r for r in attempts if r.get("backend") == "parakeet-resident" and r.get("status") == "ok" and r.get("reference_words",0) > 0]
            for name in ("mel", "encode", "decode"):
                resident["metrics"]["native_" + name + "_p50_ms"] = percentile([r.get("timings",{}).get("native_" + name + "_ms") for r in speech])
            resident["evidence"]["attempts"] = identity(attempts_path)
    if args.metrics:
        if not args.session:
            parser.error("--metrics requires explicit corrected --session UUIDs")
        rows += browser_cases(args.metrics, args.session)
    if args.optimized_metrics:
        if not args.optimized_session:
            parser.error("--optimized-metrics requires explicit --optimized-session UUIDs")
        rows += browser_cases(args.optimized_metrics, args.optimized_session, mode="resident", tag="integrated acceptance")
    data = {"schema_version":1, "generated_at":datetime.now(timezone.utc).isoformat(), "summary":"Keep Pipecat; optimize the native inference underneath. Compare the same-model Parakeet residency experiment, the smaller Whistle alternative, and the isolated Rustvani prototype without mixing their measurement boundaries.",
        "caveats":["Compare within an experiment group, not across unrelated corpora or timing boundaries.", "Six English speech fixtures plus silence and five repeats are a smoke corpus, not a broad quality study.", "Generated PCM is not audible speaker onset; missing measurements stay null.", "All models remain local and below 8 GB of weights each. Runtime RAM/VRAM is a different budget."],
        "metric_definitions":{key:dict(zip(("label","unit","direction","description"),value)) for key,value in DEFINITIONS.items()}, "comparisons":rows,
        "evidence_inputs":{"whistle":identity(args.whistle),"rustvani":identity(args.rustvani), **({"native":identity(args.native)} if args.native else {})},
        "timeline":[]}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    args.output.chmod(0o600)
    print(f"Compiled {len(rows)} metadata-only comparison cases")


if __name__ == "__main__":
    main()
