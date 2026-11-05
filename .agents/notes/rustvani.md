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
