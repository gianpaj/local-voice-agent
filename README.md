# Local Voice

An English-only voice playground. Talk through **your browser's microphone and speaker**; inference runs on your GPU computer.

**Next.js + Pipecat SmallWebRTC + Parakeet + Ollama Qwen + Phonon.** No LiveKit server, Daily room, hosted inference, Whisper, cloud API key, database, or recording. The Python agent is the direct WebRTC peer; there is no separate media-server service.

Optional CPU ASR comparison: [Needle + Whistle](docs/whistle.md). Parakeet remains the default; this experiment is a model-quality tradeoff to measure, not a promised equivalent.

## Quick start

Requires Linux, an NVIDIA GPU/CUDA-capable `transcribe.cpp`, Python 3.12 via [uv](https://docs.astral.sh/uv/), Node 24, pnpm 11.16.0, existing named model weights, and loopback Ollama. Tested with a 16 GB RTX 4060 Ti.

```bash
pnpm setup
# Edit .env to reference your existing local binary/model paths.
pnpm preflight
pnpm dev
```

Open **http://localhost:3000** on the same computer. Wait for model warmup, click Start talking, and allow microphone access. Stop clears the conversation. Ctrl+C stops both project services, not Ollama. Setup preserves an existing `.env` and installs from committed lockfiles.

On another tailnet device use private **Tailscale HTTPS**, not plain HTTP to an IP. Run `pnpm build` then `pnpm start` for this shared handoff; `pnpm dev` is for local development. Production mode avoids the dev server's HMR/cross-origin WebSocket. The browser supplies that device's audio, not host-attached devices. See the [runbook](docs/runbook.md).

## Models

- Parakeet TDT 0.6B v3 Q8_0: **739 MB**, matching Handy CUDA runner. Utterance-level recognition after VAD, not live partial STT; pause at least every 30 seconds.
- Qwen3.5 9B Q4_K_M through Ollama: **6.6 GB** weights. Startup creates only `local-voice-qwen`, reusing existing blobs, with 4,096 context and 128 output tokens. Warmed before ready; no pulls or server upgrade.
- Phonon 31M English Q8_0: **64.5 MB** weights, about 65 MB selected snapshot. Kept loaded on CPU with four threads and 24 kHz PCM streaming.

Each downloaded model is below 8 GB. Qwen's **resident allocation is around 8.6 GB**; runtime memory is not checkpoint size. Phonon stays on CPU to leave GPU room for Qwen and Parakeet. See [acquisition and licenses](docs/models.md).

## Checks

```bash
pnpm test
pnpm typecheck
pnpm build
uv run --project apps/agent python -m local_voice.check --smoke
```

The smoke command synthesizes public test speech, transcribes it locally and warms Qwen. No private recording is required. Debug the apps separately:

```bash
uv run --project apps/agent python -m local_voice.server
pnpm --filter web dev
```

## Boundaries

One browser session at a time; another receives busy instead of taking over. Start/Stop, mute, microphone/speaker selection, transcript and turn states are included. Barge-in cancels the LLM, asks the native Phonon worker to stop, discards stale PCM generations and flushes transport playback.

No persistent audio/conversation store is enabled. Browser transcript is capped at 60 entries; backend history is bounded and discarded at disconnect. Temporary STT WAV/text files are deleted on success/failure/cancel. Routine logging contains metadata, not speech. There is no cloud fallback.

Timing metadata **is** retained locally: per-request STT/LLM/TTS measurements, per-turn latency and sanitized OpenTelemetry spans, without conversation content. Run `pnpm metrics` for outcome-separated p50/p95 reports or CSV/JSON export. See [observability and timing definitions](docs/observability.md), and the [performance research backlog](docs/performance-research-2026-11-05.md) for optimizations that preserve the current models.

Local automated WebRTC checks do not verify physical audio or remote-device networking. [Verification](docs/verification.md) records what actually passed and what still needs the intended user's microphone/speaker test.

Source: [gianpaj/local-voice-agent](https://github.com/gianpaj/local-voice-agent), a private repository published at the user's request. Model weights and local runtime configuration are not included. The implementation adapts the official [Pipecat P2P voice example](https://github.com/pipecat-ai/pipecat-examples/tree/7875d02bc0714d59528b0c8797664308af960a3a/p2p-webrtc/voice-agent), replacing Gemini/public STUN. See [notices](THIRD_PARTY_NOTICES.md), [design](docs/superpowers/specs/2026-11-04-local-voice-agent-design.md), and [plan](docs/superpowers/plans/2026-11-04-local-voice-agent-implementation.md).
