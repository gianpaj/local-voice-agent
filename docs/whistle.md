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

STT stage records retain the selected model, `stt_backend`, `stt_device`, thread count, wall-clock CLI duration and audio length. Whistle's valid positive native `ttft_ms`/`decode_tps`, when present, are separate optional counters (`native_ttft_ms`, `native_decode_tps`); absent/invalid values remain null. Native ASR TTFT is not generated-speech TTFA. See [observability](observability.md) for session/turn timing boundaries.

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
