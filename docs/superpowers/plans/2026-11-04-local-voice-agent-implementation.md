# Local Voice Agent implementation plan

Date: 2026-11-04
Execution: User explicitly requested writing this plan and starting implementation with Codex. No additional review pause is required.
Repository: local-only `github/gianpaj/local-voice-agent`; no GitHub remote or publication.
Integration branch: `feat/pipecat-local-voice`.

## Outcome

A runnable English voice playground with Next.js/TypeScript and a Python Pipecat SmallWebRTC agent. The browser supplies its own microphone/speaker. Parakeet, Phonon and Ollama perform inference on this computer. No Whisper, LiveKit server, Daily room, hosted model API or cloud fallback. One active session; no recordings by default.

The design is in `docs/superpowers/specs/2026-11-04-local-voice-agent-design.md`. Keep the project small: no database, login system, telephone support, dealership business logic, model switcher, or persistent STT server in the first implementation.

## Ownership and Git preparation

The parent initializes the repository, commits this plan/design, and creates a trusted isolated implementation worktree at the resulting bootstrap SHA. A Codex worker owns the full connected frontend/backend implementation, because their contracts are dependent. The parent handles independent verification, review, final integration and any new Tailscale Serve route. All changes use feature branches and conventional commits. No PRs or pushes. There is deliberately no canonical remote for this newly initialized local repository.

## 1. Establish reproducible project tooling

Files: root `package.json`, `pnpm-workspace.yaml`, lockfile, `apps/web/package.json`, `apps/agent/pyproject.toml`, `uv.lock`, environment examples, `scripts/*`.

- Pin Next.js 16.4.5, React/React DOM 19.3.1, Pipecat client-js 1.16.0 and SmallWebRTC transport 1.11.2. Use Node 24 and pnpm.
- Pin Python 3.12, `pipecat-ai[webrtc,silero,ollama]==1.15.0`, and `ptts==0.5.0`. Add only libraries actually required for the server/PCM conversion/tests. Do not install optional Whisper or hosted-model extras.
- Resolve and commit lockfiles; verify installed Pipecat APIs instead of mixing latest-main examples into the pinned SDK.
- Use portable environment examples and an ignored local configuration. Models are referenced by local paths; setup must not silently download alternatives or upload audio.
- Add preflight checks for binaries, model formats/files, loopback Ollama, supported versions and available GPU memory. Document model/runtime acquisition separately from inference.

Proof: clean dependency resolution, Python import check, frontend type/build check, and a setup command that is safe to rerun. Commit the tooling baseline.

## 2. Implement bounded local model adapters

Files: `apps/agent/src/local_voice/config.py`, `stt.py`, `tts.py`, `llm.py` (or equally small focused equivalents), targeted tests.

### Parakeet

- Use the existing `transcribe-cli` with its matching Handy Q8_0 GGUF. The binary and model paths come from ignored configuration, not hardcoded committed paths.
- Accept normalized 16 kHz mono PCM, create a private temporary WAV, and run a subprocess with an argv list, CUDA, four threads, quiet mode, no timestamps and an output text file.
- Use VAD-completed utterances; do not promise live partial STT. Bound each utterance to 30 seconds and transcription to a 15-second deadline.
- On silence, produce no LLM request. On overlong input or model error, send a truthful user-facing error. On disconnect/deadline/cancellation, terminate and reap the process group and remove temporary artifacts. Never treat banners on stdout as a transcript.

### Phonon

- Load the pinned cached English model once on CPU with `ptts==0.5.0`, Q8_0 and four threads. Use direct Python bindings, not a new HTTP inference service.
- Iterate 24 kHz mono float32 PCM in an owning worker thread; clamp/convert to signed little-endian int16 frames for Pipecat.
- Stream sentence-sized output through bounded queues. Cancellation sets an event, discards old-generation frames, and closes the iterator in its worker. Canceling an async wrapper alone is not enough to stop native synthesis.

### Ollama

- Use the existing Qwen3.5:9b model blobs through the loopback compatibility API. Create only a project-specific local alias with 4,096 context and 128 predicted tokens, leaving the existing model/global daemon config unchanged.
- Disable reasoning through the verified compatibility setting; speak only final answer text. Use a concise English/plain-speech system prompt and bounded history.
- Warm the model before ready state. Cold load was approximately 29 seconds; this must not appear as an unexplained stalled first conversation.

Proof: actual public English sample transcription, actual local Qwen response, valid audible Phonon PCM, plus meaningful failure/cancellation/format tests. Record individual timing and distinguish that from assembled pipeline latency. Commit adapters.

## 3. Build the Pipecat pipeline and signaling server

Files: `apps/agent/src/local_voice/pipeline.py`, `server.py`, lifecycle/session helpers and tests.

- Pipeline: SmallWebRTC input → resampling/Silero VAD → Parakeet STT → user context → Ollama → Phonon TTS → SmallWebRTC output → assistant context.
- Retain default RTVI support for client transcripts/status. Start with about 600 ms end-of-speech silence, configurable for microphone testing.
- Provide `/health` and readiness plus POST/PATCH `/api/offer` using matching Pipecat 1.15.0 request-handler APIs. Attach FastAPI lifespan cleanup properly; do not copy the example's unused lifespan function.
- Admit one active session with an explicit busy response for a second. Do not rely on a transport's single-session setting to silently replace the current browser.
- Keep browser/server ICE-server lists empty. Do not include public STUN or any Google/Gemini credentials/defaults.
- Properly handle barge-in: cancel pending LLM/TTS, flush old-generation output and prevent stale speech restarting. Release tracks/tasks/subprocesses/history/session slot on Stop, disconnect and backend errors.
- Bind the HTTP server to loopback 7860, validate offer sizes/origins and keep routine logs free of conversation content.

Proof: health/readiness checks; valid offer/PATCH handling; second-client rejection; disconnect/error cleanup; real local model pipeline smoke. Commit server/pipeline.

## 4. Build the Next.js voice interface

Files: `apps/web/app/page.tsx`, `components/voice-agent.tsx`, `app/api/offer/route.ts`, readiness proxy, CSS/layout.

- A small polished page, not an admin platform: Start/Stop, mute, microphone selection, transcript and explicit initializing/ready/listening/transcribing/thinking/speaking/error states.
- Use the official client-js and SmallWebRTC packages; no LiveKit components remain. No extra React wrapper is needed.
- Same-origin POST/PATCH offer proxy to loopback 7860, preserving status/JSON/SDP. Never put remote-device `localhost:7860` in browser code.
- Request mic/echo cancellation from a user gesture; attach remote audio track; handle permission/device/autoplay failures. Cleanly disconnect on Stop/unmount and mark interrupted replies accurately.
- Update assistant transcript segments by segment ID rather than duplicating playback-progress notifications. Never display or synthesize hidden reasoning.
- Readiness includes model warmup; do not leave Start permanently disabled waiting for a session that only Start can create.

Proof: type/build checks, browser loading, states/transcript rendering, same-origin offer proxy, denied microphone handling, reconnect and cleanup. Commit frontend.

## 5. Provide setup/run commands and truthful documentation

Files: `scripts/setup`, `scripts/dev`, `scripts/preflight`, `README.md`, `docs/verification.md`, license/notices.

- A documented setup plus one development start command for both apps, correct working directories and signal cleanup. Use configured local paths; keep environment files ignored.
- Explain English only, one session, 30-second utterance limit, offline utterance-level ASR, CPU Phonon, GPU STT/LLM, model warmup and downloadable artifact sizes vs resident memory.
- Reference upstream example and pinned runtimes; retain applicable BSD-2-Clause/model-license attribution.
- Record commands/checks actually run and pending real remote-device tests. Do not label an app browser-tested merely because build passes.
- Do not create a remote GitHub repository, push, change OpenClaw audio, reset Tailscale Serve, or expose public services.

Proof: setup/preflight/start from the documented path, coherent health/readiness, no tracked private artifacts, and conventional commits. Worker hands back branch SHA, run commands and evidence.

## 6. Parent review, integration and local browser verification

- Review the Codex changes for the chosen contracts, no hosted defaults, bounded resources, transcript handling and genuine cancellation; fix actionable defects with Codex follow-up.
- Integrate completed commits into the local integration feature branch without rewriting others' history. Install/run from the canonical repository and verify its documented entrypoints.
- Run meaningful automated checks once, then only rerun when new edits/failures require it. Exercise real model conversion/PCM and the assembled browser pipeline with public/fake audio when available.
- Measure end-of-speech-to-first-playback, stage timings, errors and concurrent GPU/RAM. The warm short-turn target is under five seconds, not a guaranteed measured property until checked.
- A browser automation check may establish signaling/media/model flow, but cannot establish real hardware echo/microphone quality on the user's remote device. Label that boundary precisely.

## 7. Private Tailscale access and handoff

- Read current Serve status immediately before adding a new route, preserving every existing route. Proposed new listener: HTTPS 8443 → loopback Next.js 3000; choose a free alternative only if that port is now occupied.
- Verify private HTTPS and same-origin POST/PATCH signaling. Inspect actual SDP/candidate pair and two-way audio rather than treating page load as media proof. Serve is not a UDP relay.
- Do not enable Funnel or add a public TURN/STUN service without the user requesting expanded infrastructure.
- Deliver the local repo path, feature branch, start/stop instructions, private browser URL if successfully configured, checks passed and remaining limitations. No public/private GitHub repository is created by this local-only task.

## Completion criteria

The local repo contains a committed design/plan, implementation, lockfiles and runbook; documented commands bring up the Next.js/Pipecat app; Parakeet → Qwen → Phonon works through an actual tested connection; cancellation and core error paths are checked; no Whisper/cloud fallbacks/private artifacts are present; existing services remain intact. Any remaining need for the user's physical/remote-device microphone test is explicitly reported, not silently counted as verified.
