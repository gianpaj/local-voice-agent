# Native inference performance plan

Date: 2026-11-05. Status: planned; measurements and adoption decisions are pending.

## Authorized outcome and boundaries

Keep Pipecat and the working browser voice flow. Remove avoidable native inference work while preserving the existing English Parakeet Q8_0, Phonon voice/precision/24 kHz output, and Qwen checkpoint, context, sampling and reasoning settings. The user explicitly requested planning and implementation in logical commits; this is not another design-approval handoff.

This follows [the performance investigation](../../performance-research-2026-11-05.md). The main hypothesis is that a resident Parakeet model will eliminate repeated process/CUDA initialization, checkpoint loading and temporary WAV/text files. No speedup is assumed before comparison. A persistent model consumes memory between requests, so simultaneous STT/LLM residency is an acceptance gate.

Do not migrate the framework, change models/quantization, lower VAD thresholds, truncate more context, alter the voice, add actual tool execution, modify global Ollama/OpenClaw settings, or replace the known-good native build. The original MVP's no-required-shared-build instruction remains true for its CLI mode; this later, explicitly requested optimization adds a separately configured native mode with CLI rollback.

## Coordination and commit sequence

1. **Plan and read-only inventory** — this document and `.agents/notes/native-inference.md`; commit `docs(perf): plan native inference optimization`.
2. **Rebase onto corrected observability and establish baseline** — wait for the parent-provided tested base and an exclusive benchmark slot. No adapter edits, compilation or model inference before that handoff. Do not import the earlier observer timing defects into the benchmark baseline.
3. **Reproducible alongside-build and benchmark tools** — add pinned-revision shared-library build instructions/helper and metadata-only benchmark tooling; commit `build(stt): prepare pinned native Parakeet runtime`.
4. **Supervised resident STT integration** — implement bounded lifecycle/IPC, optional configuration, health/warmup, adapter integration and meaningful fault tests; commit `perf(stt): reuse resident Parakeet inference worker`.
5. **Measured tuning and verification** — optional BLAS/thread experiments, browser regression evidence, adoption/rollback decision and exact results; commit each justified tuning change separately and finish with `docs(perf): record native inference comparison`.

All commits stay on the assigned performance branch. Parent reviews, integrates, deploys and pushes; no worker pushes, PRs or unrelated service changes.

## A. Controlled baseline

- Record repository/native revisions, native library build options, checkpoint hashes and byte sizes, model/voice/runtime versions, CPU/GPU identity, and the unchanged decode/sampling/context settings. Hash the full checkpoints once, outside timed intervals; do not commit machine-specific paths.
- Use a small fixed public/labeled English corpus: short and medium conversational utterances, numbers/dates/names, quiet pauses and silence. Keep recordings and transcripts in ignored `.local/` artifacts. Document sources and labels, including whether a case is synthetic; do not use private meeting audio as a published fixture.
- Use identical 16 kHz mono int16 PCM for CLI and native comparisons. Verify the float conversion matches the CLI reader before comparing results. Disable none of the normal punctuation/normalization defaults; request timestamp mode `none`, four STT threads and the same CUDA backend as baseline.
- Separate startup load, first inference and repeated warm inference. Run at least 20 interleaved/paired observations across several clips for each serious candidate, retaining failed/canceled outcomes. Report per-case transcript equality, aggregate WER, exact-number/entity checks, latency distribution and sample count. Small-sample p95 is descriptive, not an SLO.
- Capture STT wall time and native load/mel/encode/decode where exposed, plus persistent-worker queue wait. Native timing fields equal to zero mean unavailable, not measured zero. Distinguish one-time model load from per-request timings; never sum their quantiles.
- Capture CPU/RSS and VRAM while Qwen is loaded, not only an isolated STT microbenchmark. Record Ollama residency via its local inspection API, not an inference-triggering global reconfiguration.
- Keep user-facing TTFA defined by the corrected observability implementation: VAD stop to first generated PCM, separately from endpointing and browser/hardware playback.

## B. Alongside shared library

Build exactly `transcribe.cpp` 0.4.1, revision `5700f127e38dd53c4735ea23ef743b4b538eacd4`, and its matching Python bindings. Do not pair a newer package with an older native ABI. The binding verifies its version/header layout at import; also record the reported native commit and selected device before use.

Create a separate build directory from the existing static CLI build. Retain Release/native CPU tuning, CUDA architecture 89 for this host, and its working CUDA/GCC toolchain. Start without BLAS to isolate model residency from kernel changes. Required shape:

```text
TRANSCRIBE_BUILD_SHARED=ON
TRANSCRIBE_CUDA=ON
TRANSCRIBE_BUILD_TESTS=OFF
TRANSCRIBE_BUILD_TOOLS=OFF
TRANSCRIBE_USE_OPENMP=OFF
TRANSCRIBE_USE_SYSTEM_BLAS=OFF
CMAKE_BUILD_TYPE=Release
```

Toolchain paths and architecture are local inputs, not hardcoded portability claims. Bound compile parallelism and coordinate it with the parent; never rebuild over the active binary/library. Inspect the resulting link dependencies and load it through an explicit configured library path, not ambiguous provider auto-discovery. Keep all generated binaries outside tracked source.

The upstream Python API accepts float32 mono 16 kHz PCM, offers a reusable `Model`/`Session`, and permits only one compute call per model at a time. Use the pinned upstream binding rather than creating an independent ABI wrapper. Pin any dependency/source integration reproducibly; do not require installing unpublished provider wheels or committing native binaries/weights.

## C. Supervised resident worker contract

Preserve the existing `transcribe_pcm(..., timings=...)` adapter contract for the pipeline, but introduce an application-owned STT engine with explicit startup, per-request, interruption and close operations. CLI mode remains available for rollback; do not silently fall back after native errors and mix two runtimes in one benchmark.

- Spawn a fresh child process, not a fork of the live CUDA/Pipecat process. The child exclusively owns one model/session and serialized compute. One model load per worker generation; no model load or file conversion per normal utterance.
- Send bounded int16 PCM and request/generation identifiers over a private local pipe/socket. Validate framing, message sizes and allowed commands; no network listener, pickle from untrusted input, audio files or transcript logs. Native stdout/stderr must not corrupt the protocol or leak transcript text to retained logs.
- The native run may execute on one owning thread so a separate control path can deliver cancellation using the supported `session.cancel()`. Cancellation must reach native compute: canceling an asyncio await alone is insufficient. Check cancellation/generation state before starting as well as while running, because the binding clears a stale cancel at run start.
- Keep application queueing bounded and serialized; preserve today's segment queue and maximum utterance length. Measure queue delay separately from inference. Never emit late results after interruption or assign them to a newer request/turn.
- Retain the current 15-second inference deadline, with a separate bounded model startup deadline suitable for a cold load. On cancellation, allow a short bounded native-abort grace period, then terminate/reap the worker process group if it does not settle. A crash, corrupt response, stuck process or forced kill invalidates that generation; restart only through controlled readiness before accepting new work.
- Surface unavailable/warming/error states honestly. No busy restart loop or infinite automatic replay of a canceled utterance. The next explicit request may cause a bounded recovery, but never replays an externally visible action.
- Shutdown closes the native session/model on its owner after compute stops, closes IPC, reaps the child and clears pending requests. Check no child/thread/descriptor/GPU allocation remains after repeated connect/disconnect and application shutdown.
- Extend metadata-only measurements with runtime/build labels, worker startup/load, queue wait and native per-run timing fields. Do not change session/turn IDs or existing TTFA semantics. Missing counters stay null.

Inject a fake worker/model boundary for deterministic crash, timeout, queued cancellation, malformed/oversized response, stale-generation and shutdown tests. Real-native tests must verify actual CUDA inference separately.

## D. Optional CPU-kernel and scheduling experiments

### OpenBLAS

The active static build has CUDA but no discovered system BLAS. Add BLAS only as a separate candidate after resident/no-BLAS is measured. Prefer a locally extracted runtime/development package or local source prefix; do not change global alternatives or install system packages just to satisfy a build. Verify headers, BLAS linkage and the effective thread policy. Avoid adding an OpenMP runtime conflict to the agent process: the STT child is the isolation boundary.

Compare native no-BLAS versus native BLAS with the same PCM, checkpoint, device, threads and decode settings. Confirm upstream actually uses BLAS on a relevant operation; a linked library alone is not acceleration evidence. Reject the candidate if it worsens full-pipeline tail latency, causes numerical/transcript regression, or merely shifts time out of an isolated microbenchmark.

### Phonon CPU threads

Phonon is already resident Rust/AVX2 CPU inference. Benchmark one, two and four threads in separate initialized engine/process runs because its thread setting may be global. Keep the exact model, voice, text, chunking, precision and sample rate. Measure first PCM, total synthesis, RTF, queue underruns and CPU contention while STT/LLM are active. Adopt a different thread count only with repeatable end-to-end benefit and unchanged audio-quality checks; otherwise keep four. No GPU TTS migration in this pass.

### LLM

Inspect actual full-GPU residency, current FlashAttention selection where observable, stable prompt-prefix behavior and memory under the candidate STT worker. Existing warmup/keepalive remains. Do not change global daemon environment, cache precision, model quantization, reasoning mode, output cap or context limits. Document tool-call context-group preservation and argument budgets for the future feature; do not implement tools here.

## E. Acceptance and rollout

Required correctness gates:

1. Existing adapter/server/telemetry tests pass; fault tests prove deadlines/cancellation and bounded lifecycle.
2. Same labeled public corpus: no WER regression or number/entity regression against CLI; exact transcript differences require inspection and a documented decision, not a claim of mathematical identity from unchanged weights.
3. Silence does not acquire new hallucinations; malformed/oversized audio and canceled requests do not produce stale transcripts or audio.
4. At least ten real-model browser turns through the production/private-HTTPS path, plus barge-in, Stop, forced disconnect/reconnect and idle warm-model checks. Parent coordinates exclusive use of the running deployment. Distinct remote-device/hardware behavior remains unverified unless actually exercised.
5. Qwen remains fully GPU-resident and the pipeline stays within available GPU/RAM headroom without OOM, CPU fallback or unbounded queues. A checkpoint smaller than 8 GB is not a runtime-memory guarantee.
6. Paired results show a repeatable STT improvement, with no material full-pipeline p95 regression or cancellation/quality regressions. Report the observed effect and sample size, not a theoretical speedup. If variance prevents a conclusion, keep the candidate opt-in and retain the current default.

Parent reviews the implementation/evidence, switches ignored local configuration only after acceptance, and verifies health plus persisted measurements after restart. Rollback is an explicit switch to the preserved CLI mode/build and restart; it does not require changing any model files. Keep rejected experiments documented rather than deleting or quietly presenting them as successes.
