# Optional Whistle CPU speech recognition

Parakeet remains the default. Whistle is an explicit **different-model** experiment for lower-powered machines, not a lossless acceleration of Parakeet. Qwen, Phonon, Pipecat, VAD timing and English-only behavior stay unchanged. No Needle text/tool-routing model is loaded.

## Reuse a matching native runner and export

Read [Needle's Whistle instructions](https://github.com/cactus-compute/needle#whistle) and the [Whistle card](https://huggingface.co/Cactus-Compute/whistle). Use the native executable distributed for your platform and its matching `whistle.cact`. A same-named model or the Python management `needle` command does not establish compatibility. Check the installed binary's `--help`: it must accept `--model`, `--audio`, `--audio-language` and `--threads`.

The tested existing artifacts are a 16,919,407-byte Whistle export and native engine 3.3.1 (installer provenance `cactus-needle` 3.2.1). Neither new weights nor a new inference package are downloaded by setup/startup. Each selected checkpoint is checked against the 8 GB size limit. Use explicit acquisition separately if you do not already have compatible artifacts.

In the ignored project `.env`:

```dotenv
STT_BACKEND=whistle
NEEDLE_BIN=/path/to/native-platform/needle
WHISTLE_MODEL=/path/to/whistle.cact
STT_THREADS=4
```

Only the selected recognizer's paths are required. Preserve Parakeet's paths for rollback. Restart the application with the normal runbook commands; `/api/health` identifies the selected STT model and CPU/CUDA device. Startup runs a genuine recognition check before ready. Never point `NEEDLE_BIN` at the Python management CLI. The app passes explicit English, four threads by default, and **no** shallower `--audio-depth` setting.

Whistle receives the same bounded VAD-completed 16 kHz mono int16 PCM as Parakeet. It is not live partial recognition in this integration. Silence bypasses either runtime; a stalled/error/cancelled child is terminated/reaped and its private temporary files are removed. No fallback silently switches recognizers on failure. Vendor telemetry is disabled and Hugging Face set offline in the subprocess environment. The native runner uses local files, not a service endpoint; this is not proof of absence of all host/network traffic.

## Retained comparison

STT stage records retain the selected model identifier and numeric thread count, wall-clock CLI duration and audio length. The health response and explicit benchmark settings identify CPU/CUDA; categorical labels are not inserted into the numeric-or-null metrics dictionary. Whistle's valid positive native `ttft_ms`/`decode_tps`, when present, are separate optional counters (`native_ttft_ms`, `native_decode_tps`); absent/invalid values remain null. Native ASR TTFT is not generated-speech TTFA. See [observability](observability.md) for session/turn timing boundaries.

Use the same normalized WAV bytes and labels for both models. Report WER/CER, names/numbers, silence false positives, failures and paired latency; do not select a smaller model on latency alone or describe a few clean synthetic samples as proof of conversational accuracy. Model substitutions do not preserve quality by construction. The comparison harness/evidence below is updated after a coordinated real-inference run.

Explicit fixture preparation and comparison (requires both local runtimes and paths):

```sh
uv run --project apps/agent python scripts/prepare-stt-corpus.py --add-synthetic
uv run --project apps/agent python scripts/benchmark-stt.py \
  --manifest .local/corpus/stt-comparison/manifest.jsonl \
  --output .local/benchmarks/whistle --repeats 5
```

Preparation explicitly downloads three public labeled LibriSpeech-subset samples and the public JFK sample; `--add-synthetic` additionally runs local Phonon for name/number controls. It also generates digital silence. **App setup/startup never acquires this corpus.** Raw corpus references/recordings stay in ignored `.local/`. The default benchmark report contains hashes, scores and timings, not transcript text; `--save-transcripts` is an explicit opt-in for inspecting this public test corpus locally. Do not use private recordings or commit reports.

The harness alternates model order on each repeat, retains failures/timeouts, uses identical PCM, and hashes both selected model artifacts and executables outside timed intervals. Non-silent latency includes fresh process/model loading; warm OS caches are not cold disk startup. Silence exercises the shared fast path, not raw model decoding. p50/p95 exclude the silence fast path. WER/CER are case/punctuation folded but numeric renderings are not rewritten; separate allowed-rendering probes distinguish correct numbers/names from formatting differences. Successful-only WER is accompanied by scored/total coverage and failure counts, never missing-as-zero latency.

## Rollback

Set `STT_BACKEND=parakeet`, retain the working `PARAKEET_BIN`/`PARAKEET_MODEL`, and restart normally. No OpenClaw/global Ollama/Tailscale setting is changed by this feature.

## Measured comparison — 2026-11-05

**Decision: retain Whistle as opt-in, not the default.** Smaller CPU inference helped several short clips, but this run did not meet a no-quality-loss/no-tail-regression adoption gate.

Seven unique inputs: four public natural-speech clips, two synthetic Phonon name/number controls, and digital silence. Five alternating paired repetitions produced **35/35 successful attempts for each backend**, including 30 speech attempts and five shared silence fast-path attempts. Repetition measures timing consistency, not 30 independent speech examples. Same PCM bytes, explicit English, four threads, default full Whistle decoder and existing Parakeet Q8_0 CUDA settings. These are **fresh-process/model-load** timings with potentially warm OS caches, not resident-model timings or browser TTFA.

| Metric | Parakeet CUDA | Whistle CPU |
| --- | ---: | ---: |
| Speech process-wall p50 | 493.8 ms | 263.0 ms |
| Speech process-wall p95 | 549.8 ms | 1451.8 ms |
| Reference WER, successful speech | 0% | 8.57% |
| Reference CER, successful speech | 0% | 5.48% |
| Names/numbers probe matches | 45/45 | 35/45 |
| Failures/timeouts | 0/35 | 0/35 |
| Silence fast-path false positives | 0/5 | 0/5 |
| Checkpoint bytes | 739,508,576 | 16,919,407 |

Speech p50/p95 by unique fixture (five repetitions each):

| Public fixture ID | Seconds | Parakeet p50/p95 ms | Whistle p50/p95 ms |
| --- | ---: | ---: | ---: |
| librispeech-1272-128104-0000 | 5.855 | 495.5 / 518.4 | 714.6 / 719.2 |
| librispeech-1272-128104-0001 | 4.815 | 486.8 / 498.1 | 120.5 / 126.5 |
| librispeech-1272-128104-0002 | 12.485 | 549.5 / 553.4 | 1450.8 / 1457.8 |
| jfk | 11.000 | 523.8 / 541.1 | 1242.5 / 1245.1 |
| synthetic-names | 3.680 | 485.3 / 488.8 | 102.6 / 103.8 |
| synthetic-numbers | 3.840 | 481.4 / 489.3 | 104.8 / 113.0 |

**Quality inspection matters:** some Whistle WER errors were legitimate written/spoken normalization differences (titles and dates/times). All three numeric semantic probes passed. However, it also consistently misspelled the public proper name in two natural-speech clips and had real insertion/spelling errors in another. WER formatting differences do not explain away those regressions. Both recognizers matched the synthetic name control and JFK after case/punctuation normalization. Nine distinct probes were repeated five times; 45 trials do not mean 45 independent names/numbers.

The silence result tests adapter bypass, not the raw recognizers' response to arbitrary silence/noise. This tiny corpus does not establish outdoor, meeting, accent, multilingual or general conversational accuracy. Runtime RAM/VRAM peaks, full browser-cascade latency and tool-routing quality were not measured by this comparison; smaller checkpoint size is not a runtime-memory measurement. No tool execution or framework change was introduced.

Machine-readable retained evidence is in the ignored `.local/benchmarks/whistle/summary.json` and `attempts.jsonl`, with the exact manifest/model/executable/PCM hashes. The summary contains public fixture IDs and numeric metrics, not references/transcripts or machine-specific paths. The local run explicitly retained **public test-corpus** hypothesis text in `attempts.jsonl` for the error inspection above; default harness behavior does not retain text. No recordings, transcripts, weights or raw reports are committed.
