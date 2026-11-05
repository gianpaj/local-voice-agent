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

**Waiting for parent handoff:** corrected observability base plus an exclusive build/benchmark window. No heavy compile, inference or adapter edits have been performed by this worker. The first commit is plan/notes only; implementation resumes on the parent-confirmed updated base.

## Evidence still required

- Corrected telemetry baseline and model/build hashes.
- Public/labeled English corpus sources, references, transcript parity/WER and number/entity checks.
- Native shared no-BLAS build/ABI/CUDA verification; warm-versus-cold measurements.
- Worker fault/cancel/restart tests and no stale output or leaked process/VRAM.
- Full-pipeline residency/RAM/VRAM, browser ten-turn/barge-in/Stop/reconnect evidence.
- Optional BLAS and TTS-thread experiments; preserve unchanged defaults if no repeatable gain.
- Adoption decision, rollback instructions and parent-reviewed commits.
