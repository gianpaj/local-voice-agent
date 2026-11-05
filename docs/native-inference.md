# Optional resident Parakeet runtime

The default remains `PARAKEET_MODE=cli` until the same-checkpoint comparison and full-pipeline acceptance pass. Resident mode keeps the existing Q8_0 checkpoint loaded in an isolated, supervised native child; Pipecat, the voice, decoding defaults and LLM settings stay unchanged. It does not run Whisper or fall back to a different model.

## Build alongside the working CLI

Use an existing unmodified `transcribe.cpp` checkout at revision `5700f127e38dd53c4735ea23ef743b4b538eacd4` (0.4.1). On this Linux/CUDA host:

```bash
python3 scripts/build-parakeet.py \
  --source /path/to/transcribe.cpp \
  --output .local/parakeet-native \
  --cuda-arch 89 --jobs 2
```

Architecture 89 is the current RTX 4060 Ti, not a portable GPU default. The helper defaults to CMake `native`; configure a supported architecture/toolchain for another machine. This helper uses GCC 13 by default (`--cc`/`--cxx` override), Release/native CPU tuning, shared libtranscribe/ggml and CUDA; it does not modify the existing static build, install global packages, pull models or upgrade any daemon.

The matching pure-Python upstream bindings are copied into the ignored output directory. The app does not install an unrelated provider wheel. The child selects `TRANSCRIBE_LIBRARY` explicitly and verifies binding/native ABI, version and revision before opening the unchanged checkpoint. Build output includes a local `build-manifest.json` with options and the library hash. Keep it with private experiment artifacts, not in Git.

Start without BLAS to isolate residency. An optional local OpenBLAS prefix can be passed using `--blas-prefix` with a **different** output directory; do not claim acceleration from linkage alone. BLAS/thread tuning requires its own comparison.

## Configure and start

Add the paths printed by the helper to the ignored `.env`:

```dotenv
PARAKEET_MODE=resident
PARAKEET_LIBRARY=/path/to/parakeet-native/build/src/libtranscribe.so
PARAKEET_BINDINGS=/path/to/parakeet-native/bindings
PARAKEET_STARTUP_TIMEOUT=45
STT_THREADS=4
```

Keep the existing `PARAKEET_BIN` and `PARAKEET_MODEL` so CLI rollback is immediately available. Use the project's normal setup/build/start commands. Startup loads the native model once, warms the other models and performs genuine speech-to-text readiness; merely loading a library is not the readiness test. The shared CUDA model stays resident until app shutdown, including between browser sessions.

`/health` reports `stt_runtime` and does not claim ready when the child is gone. Health polling can request one bounded readiness reload, but never replays old audio. If that reload fails, it stays unready rather than repeatedly crashing/reloading; fix configuration and restart the app. A successful explicit inference resets that recovery budget.

The inherited local socket accepts bounded raw int16 PCM, not network requests. One compute operation is serialized per model, with at most two waiting requests. No PCM/transcript files are written. Native logs are silenced and stdout/stderr are not protocol transports. Request IDs and generation invalidation prevent late results from being delivered as a new turn. Cancellation reaches the native abort callback; if it stalls, the owned process group is terminated/reaped. Shutdown and malformed responses have bounded cleanup, not an indefinite await on a native thread.

There is **no silent fallback** between CLI/resident modes. Inference errors are reported and retained as failures; recovery does not retry the previous utterance. `PARAKEET_STARTUP_TIMEOUT` bounds a cold worker separately from the existing STT inference deadline.

## Measure before adopting

Use the shared labeled corpus prepared by `scripts/prepare-stt-corpus.py` (from the optional Whistle comparison), with the same PCM hashes and scoring implementation:

```bash
uv run --project apps/agent python scripts/benchmark-native-stt.py \
  --manifest .local/corpus/stt-comparison/manifest.jsonl \
  --library /path/to/parakeet-native/build/src/libtranscribe.so \
  --bindings /path/to/parakeet-native/bindings \
  --repeats 5
```

The script alternates CLI/resident order, separates startup/first/warm inference, records exact/normalized transcript hashes, WER/CER and number/entity probes, and keeps metadata-only JSONL plus `summary.json` under `.local/benchmarks/`. Missing native timing values stay null. Silence is explicitly the common adapter fast path, not a test of raw model silence decoding.

The report includes sampled device-wide VRAM/worker RSS and Ollama residency before/after. Sampling can miss brief peaks, and device-wide values are not process-exclusive allocation. Run with compilation and unrelated inference paused. The case set is a regression probe, not a general accuracy benchmark. Compare full-pipeline TTFA/interruptions/reconnect as well as STT latency; do not add stage p95s together.

Resident stage metrics add `worker_queue_ms`, `worker_startup_ms`, and `native_mel_ms`/`native_encode_ms`/`native_decode_ms`/`native_total_ms`. Startup telemetry retains the one-time model load separately. Native timing zero means unavailable. No categorical mode/backend strings are inserted into numeric `metrics`.

## Rollback

Set `PARAKEET_MODE=cli` and restart using the normal launcher. The preserved CLI/model paths continue to work; no checkpoint conversion, model replacement or global configuration change is required. Keep generated builds/results ignored for reproducibility and review.
