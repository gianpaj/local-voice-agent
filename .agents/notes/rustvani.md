# Rustvani experiment journal

## 2026-11-05: source inventory and bounded plan

- Verified the parent-prepared `spike/rustvani-comparison` worktree starts at `d28fcd89e749e50df96276cd56898644ffe06ef5` with no changes.
- Inspected upstream `Allenmylath/rustvani` at `b134ad055c754efaa690fec72f530d5390fd6180`, package version `0.4.0-dev.27`, BSD-2-Clause. Pin that revision; upstream HEAD/version are moving quickly.
- This is a bonus, isolated comparison. Keep the working Pipecat/browser service unchanged. No external inference, API keys, model replacements, global service changes or Tailscale edits.
- Disable default features: they include hosted STT/TTS, ONNX Runtime and Postgres. Core pipeline/FrameHandler support is sufficient for a headless experiment.
- Rustvani has no built-in adapter for our Parakeet GGUF or Phonon voice. A resident local Python bridge can reuse the exact installed Parakeet CLI, Qwen request settings, and Rust-backed `ptts` CPU model while Rustvani provides real inter-stage frame orchestration. This is **not** a fully Rust inference implementation or proof of framework speedup.
- Its OpenAI handler lacks arbitrary request extras such as our `reasoning_effort: none`; do not silently change Qwen behavior to use it. Use an explicitly local request adapter for the experiment, and document the tool-calling integration gap.
- The WebRTC transport is real P2P, but signaling is WebSocket/RAVI, not the existing HTTP offer/RTVI frontend protocol. Public Google STUN is a default; any future private deployment must explicitly set an empty ICE list.
- No Rust toolchain was present in the inspected standard locations. Install a minimal, isolated toolchain only after the parent's build/resource window opens. No compilation or inference during the parent's exclusive baseline measurements.

## Implementation sequence

1. Commit plan/source findings.
2. Add a pinned minimal Rust crate, bounded metadata-only headless harness, resident local model bridge and reproducible run instructions.
3. Compile and execute an actual public English WAV through Parakeet → Qwen → Phonon when the parent grants resources. Record stage boundaries and separate framework handoff from model/IPC cost.
4. Record what passed and what remains unverified. No browser/cancellation parity or quality/speed claim without evidence. Commit the runnable experiment and evidence separately where useful.

Private recordings, generated audio, model paths, raw text and benchmark JSON stay in ignored `.local/` storage, not this journal.

## Minimal build and model-free verification

- Parent granted a two-job compile window, not model inference. Installed Rust 1.99.1 with `--no-modify-path` into ignored project-local storage; no global toolchain changes.
- Pinned Rustvani builds successfully with `default-features = false`; release harness binary is approximately 2.2 MB. Lockfile retained. Build assets/cache/target are ignored.
- Four model-free integration checks pass: real Rustvani frame routing with nonzero mock PCM and text-sentinel redaction; adapter failure retained rather than success/zero latency; wrong WAV format rejected; oversized IPC bounded/rejected.
- Fixed our own cancellation-frame direction after inspection: task-control conversion is an upstream escape API; explicit downstream lifecycle cancellation uses `Frame::cancel()`. Tests then complete in 0.036 seconds with graceful pipeline shutdown, not the earlier forced deadline.
- Bridge requests preserve the baseline model/prompt/reasoning/precision settings. Full-reply TTS aggregation is explicitly a reduced-scope experiment, not production sentence-streaming parity.
- Actual model execution remains gated on the parent's exclusive inference window. No measured Rust-versus-Pipecat speedup or browser compatibility claim.

## Real-model window and decision

- Parent granted an exclusive inference window after Whistle comparison; native compilation was paused. Ran exactly two public 16 kHz fixtures, LibriSpeech 0000 and JFK, against the same installed Parakeet/Qwen/Phonon stack. Both succeeded with nonzero PCM, no forced pipeline/bridge shutdown, and no leftover experiment/bridge/sampler process observed afterward. Immediately notified parent `RESOURCES FREE` so native compilation could resume.
- Rust STT→LLM handoffs: 0.051/0.047 ms; LLM→TTS: 0.0075/0.0057 ms. CLI STT 501.53/517.29 ms; LLM first answer token 192.15/172.24 ms. Warm Phonon first PCM 25.79/20.73 ms.
- Completed-input-frame→headless-first-PCM: 1429.45/1229.53 ms. This excludes VAD/network/playback and buffers the full reply, so is explicitly **not comparable** to production browser TTFA or evidence of a framework speedup.
- Sampled experiment process tree peak RSS 733.76/730.25 MiB excludes the existing Ollama server. Sampled total GPU peak 9406/9430 MiB includes all processes. Sampling intervals and limitations retained in JSON/docs.
- Metadata artifacts retain fixture source/hash, exact model/request settings, native/framework boundaries, errors/missing-as-null and resource labels. All artifacts are ignored; no transcripts or recordings committed.
- Verdict: actual headless feasibility established; no browser, interruption, quality or speed parity proven. Do not adopt as the deployed baseline. The frame handoff is tiny in these two runs relative to model/buffering time, consistent with prioritizing native inference beneath Pipecat.
