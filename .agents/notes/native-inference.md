# Native inference working journal

This is a project-specific handoff/experiment record, not a learned skill. Keep private paths, tokens, model weights, recordings, transcripts and raw traces out of this file. Local benchmark artifacts belong in ignored `.local/` directories.

## Current objective

Keep Pipecat; improve the existing native inference path without changing English model/voice capability. User explicitly authorized step-by-step planning, implementation and logical commits. Parent coordinates integration, service restarts, exclusive benchmarks and pushes.

Plan: [native inference performance](../../docs/superpowers/plans/2026-11-05-native-inference-performance.md).

## 2026-11-05 — planning inventory

- Assigned feature branch: `perf/native-inference`; starting repository HEAD verified clean at `5e823991f4dec066ea6bb17e4cd728e92f6693c2`.
- Read repository instructions, performance research, current STT/config/TTS, and the installed upstream binding/build/header sources. Applied the brainstorming intent/design checks within the user's explicit plan-and-implement authorization; no additional approval pause is required.
- Pinned upstream native source verified clean at `5700f127e38dd53c4735ea23ef743b4b538eacd4` (0.4.1).
- Existing static CMake cache: Release, GCC 13, CUDA enabled, CUDA architecture 89, native CPU tuning enabled, `TRANSCRIBE_BUILD_SHARED=OFF`, `GGML_BLAS=OFF`; discovery lists OpenBLAS as not found. Runtime BLAS files exist, but development headers/link inputs are not yet verified. Do not infer OpenBLAS support from cuBLAS.
- Matching upstream Python package is ctypes-only `transcribe-cpp` 0.4.1. Its working-tree instructions use an explicitly selected shared library; the import checks ABI/version. Its package metadata normally depends on native provider wheels, while upstream uv development overrides intentionally avoid provider downloads. Decide reproducible app integration before adding dependencies; do not accidentally install a different provider.
- Shared-library build can be isolated alongside the current CLI with `TRANSCRIBE_BUILD_SHARED=ON`. Preserve CUDA/CPU toolchain flags and start without system BLAS so model residency is the only first comparison variable.
- Binding supports reusable model/session, mono 16 kHz float32 PCM and cross-thread `Session.cancel()`. One compute operation per model; cancellation is cleared at a new run, so supervisor generation/cancel ordering matters.
- Header timing contract: load/mel/encode/decode milliseconds; zero means unknown/not measured. Model load is one-time and repeated in session timing snapshots; never add it to every warm request or report native zero as measured zero.
- Current Phonon uses one owning executor thread, resident Rust CPU model, four native threads and bounded 24 kHz PCM queue. It already has a native cancellation/generation boundary. Tune only with controlled full-pipeline evidence.

## Coordination gate

Parent supplied corrected observability base `76358a24db86e26859875e9784f4de1dec4a2bd2` and authorized a two-job native build plus model-free tests. The build was paused and resumed on the parent's resource schedule while the independent Whistle/Rust comparisons ran. Timed model comparisons still require a parent-coordinated idle window.

## 2026-11-05 — resident implementation and build

- `a1d8b7f`: reproducible pinned alongside-build helper. Local shared CUDA/SM89/Release build completed without BLAS; existing static CLI untouched. Link inspection confirms local shared ggml CPU/CUDA and CUDA/cuBLAS dependencies. The local build manifest retains effective options and library hash; no binaries or private paths are committed.
- `44240ca`: optional resident STT supervisor/child, bounded raw-PCM framing, pinned API/version/revision validation, one owned reusable model/session, serialized compute, one active plus two waiting calls, cancellation reaching native code and kill/reap fallback, request ID validation, controlled reload, startup/readiness/shutdown integration, rollback mode and operations guide. Default remains CLI pending acceptance.
- Verified upstream int16 conversion uses `sample * 0.000030517578125f`; resident normalization is the identical float32 scale. Timestamp mode remains `none`; no language/decode-family/precision overrides were introduced.
- 36 backend tests pass on this isolated worktree, including 14 new actual-child/fake-native protocol, reuse, cancellation, timeout, malformed-size, crash/reload, stuck-call kill, bounded admission and one-attempt readiness recovery checks. Existing corrected turn ownership/telemetry tests remain passing. Real CUDA model inference is a separate remaining gate.
- Added native request queue/startup and mel/encode/decode/total timing fields, all numeric/null. One-time model load is startup metadata, not added to every warm request. No new transcript/audio retention.
- Coordinated dispatcher/config seam with the Whistle worker: an application-owned `engine` keyword is used only for resident Parakeet. Parent composes backend selection so selecting Whistle does not initialize unused Parakeet. No VAD/turn-ownership redesign in this change.
- Prepared a paired CLI/resident benchmark using the Whistle comparison's exact seven labeled public/synthetic/silence fixtures and shared WER/entity scoring implementation. Results remain pending; no speed or quality-parity claim yet.

## 2026-11-05 — controlled evidence and review fixes

- Exclusive native/CLI comparison completed: 70/70 attempts successful, 35/35 exact/normalized transcript matches, both 0% WER/CER on this small corpus, both 100% number/entity probes, zero adapter-silence false positives. Speech p50/p95: CLI 487.6/538.5 ms, resident 52.3/113.4 ms; 29 warm resident calls p50 48.7 ms. Startup 341.6 ms, model load 243.5 ms, first inference 158.9 ms are separate costs.
- Qwen stayed fully GPU-resident at unchanged context 4,096 before/after. Device-wide sampled peak 10,299 MiB (includes unrelated embedding model and overlap with temporary CLI), worker RSS 536,350,720 bytes; no instantaneous GPU/RAM peak claim.
- Actual native CUDA cancellation/deadline/recovery/shutdown checks passed. Five native cancel-plus-follow-up checks retained the same worker and the identical next reference transcript. Combined times 283–295 ms are not pure abort latency. In-flight shutdown reaped its child in 66.3 ms. Evidence is ignored metadata-only JSON, no transcript/audio content.
- Reviewer identified and implementation fixed: offer/readiness inconsistency, TERM-resistant shutdown waiting behind an inference lock, process ownership lost on repeated cleanup cancellation, and close-before-spawn-handle startup race. Strongly retained shielded cleanup tasks now finish owned reaping before references clear; pending process creation is canceled on close, and closed is checked after creation.
- Parent configuration metadata API was cherry-picked (`0ddbdb5` → equivalent local `e7604ad`); attached sessions now retain safe backend/mode/thread labels outside numeric metrics.
- 40 backend tests pass; independent read-only lifecycle/ABI review reports no outstanding blocking issues. No timing rerun was needed for cleanup/readiness-only fixes; actual benchmark/lifecycle checks exercised the corresponding native path, and new edge cases have deterministic subprocess regressions.
- Detailed measured conditions/results: [native inference results](../../docs/native-inference-results-2026-11-05.md). Parent still owns browser/deployment acceptance, optional local default switch and push. OpenBLAS/TTS-thread/model/runtime changes remain unadopted rather than presented as unmeasured improvements.

## Evidence still required

- Corrected telemetry baseline and model/build hashes.
- Public/labeled English corpus sources, references, transcript parity/WER and number/entity checks.
- Native shared no-BLAS build/ABI/CUDA verification; warm-versus-cold measurements.
- Worker fault/cancel/restart tests and no stale output or leaked process/VRAM.
- Full-pipeline residency/RAM/VRAM, browser ten-turn/barge-in/Stop/reconnect evidence.
- Optional BLAS and TTS-thread experiments; preserve unchanged defaults if no repeatable gain.
- Adoption decision, rollback instructions and parent-reviewed commits.
