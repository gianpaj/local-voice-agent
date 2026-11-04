# English-only local voice agent MVP

Date: 2026-11-04
Status: User directed repository initialization, plan writing, and implementation on 2026-11-04. This remains a design, not a completed-installation receipt.

## Purpose and agreed constraints

Gian wants a small, reusable voice-agent website and repository, running its models on his existing GPU computer. He selected the simpler Pipecat approach after comparing LiveKit, accepted its Python backend trade-off, requested English only, explicitly excluded Whisper, and requested Codex/Claude delegation.

The retained frontend requirement is Next.js/TypeScript. No LiveKit Cloud, LiveKit media server, Daily service, hosted STT/TTS/LLM, telephone integration, dealership scheduling, or meeting diarization belongs in this MVP. Each model's downloaded artifacts must be below 8 GB. This is a per-model disk-size limit, not an assertion that resident memory equals checkpoint size.

Audio-device assumption: the person talks using the microphone and speaker of the device running the browser; that device reaches the GPU computer through Tailscale. Devices attached directly to the GPU computer are a separate transport and are not included here. The website also works from a browser on the GPU computer itself. This assumption must be corrected before implementation if Gian instead meant a remotely controlled, host-attached microphone.

Success means opening the private HTTPS page, pressing Start, speaking English, seeing the recognized text and assistant reply, hearing the reply, and interrupting or ending it without stale speech continuing. The project must be reproducible without changing OpenClaw's existing transcription configuration.

## Chosen approach and alternatives

Use Pipecat SmallWebRTC: one browser peer talks directly to one Python agent endpoint, without a separate SFU/room/media server. Next.js supplies the UI and same-origin signaling proxy. Ollama remains the only separate inference daemon; STT uses the existing native CLI, and TTS runs inside the agent process.

LiveKit would add a separate server and agent-dispatch machinery not needed for a one-person playground. Host-local audio would remove WebRTC but would not carry the remote browser's microphone. A custom browser WebSocket audio implementation would require extra transport, buffering, and playback code; it is not the selected approach.

## Repository and service boundaries

Proposed repository: `gianpaj/local-voice-agent`, private initially. The local repository is initialized; no GitHub remote has been created. Implementation belongs on `feat/pipecat-local-voice`, with conventional commits, no main pushes, and no automatic PR creation.

- `apps/web`: Next.js/TypeScript, a small voice-session view, and POST/PATCH signaling proxy routes.
- `apps/agent`: Python 3.12, Pipecat pipeline, Parakeet adapter, Phonon adapter, session lifecycle, and bounded configuration.
- `scripts`: reproducible setup, preflight, development start/stop, and smoke-check commands.
- `docs`: architecture, tested versions, model acquisition instructions, runbook, measured latency/memory, and verification limits.

Model weights, recordings, private transcripts, cache directories, credentials, and local absolute paths do not enter Git. Environment examples document placeholders. Preserve BSD-2-Clause notices from reused Pipecat examples and model-license attribution. Reuse installed weights through local configuration, while documenting explicit acquisition for other machines.

The web server listens on loopback port 3000, the agent's HTTP signaling endpoint on loopback 7860, and Ollama stays on loopback 11434. The agent is also the WebRTC media endpoint; calling this 'no media server' means no *separate* LiveKit/Daily/SFU service.

## Browser experience

The initial page includes Start/Stop, microphone permission/error handling, a microphone selector, mute, a transcript, and visible states: initializing, ready, listening, transcribing, thinking, speaking, interrupted, and error. Disable Start until preflight and warmup succeed. One browser session is admitted at a time; a second receives an explicit busy response rather than stealing another user's session.

Use a deliberately small Next.js client with the official `@pipecat-ai/client-js==1.16.0` and `@pipecat-ai/small-webrtc-transport==1.11.2` packages; no separate React wrapper is necessary. Construct `SmallWebRTCTransport({ iceServers: [] })` and connect through same-origin `/api/offer`. Retain Pipecat 1.15.0's default RTVI processor/observer for transcript/status events rather than writing a second event protocol. Update assistant display segments by `segment_id` instead of appending every playback-progress event. `onUserInput`, `onBotOutput`, speaking/interruption, ready, transport, and error callbacks drive UI state. Attach only the remote audio track to playback.

The browser creates an audio track with echo cancellation/noise suppression requested. Device-specific echo behavior still needs real testing. Start is a user gesture that requests microphone access and permits playback; denied permission, unavailable device, disconnected peer, and autoplay failure produce actionable UI messages. The official SmallWebRTC package depends on Daily's JS library for device management, but does not require a Daily account, room, or media server; verify external asset/network behavior rather than claiming that the package name proves a cloud connection or that no external request is possible.

User transcripts are final utterance-level results, not live partial STT. Assistant text may arrive incrementally. The UI must distinguish spoken/canceled replies, must not display model reasoning as a reply, and must clear session history on Stop. No recording is enabled by default.

## Audio and inference flow

Browser microphone → direct WebRTC over the tailnet → Pipecat input/resampling → Silero VAD → completed 16 kHz mono utterance → Parakeet → session context → local Ollama → sentence-sized response chunks → local Phonon → 24 kHz mono PCM → Pipecat output/WebRTC → browser speaker.

### STT: Parakeet TDT 0.6B v3 Q8_0

Reuse the existing Handy GGUF, 739,508,576 bytes, with its matching `transcribe.cpp` runner pinned to `5700f127e38dd53c4735ea23ef743b4b538eacd4`. This is offline ASR, not a native streaming model.

For each VAD-finished utterance, the adapter uses `asyncio.create_subprocess_exec` with an argument list, a private temporary directory, normalized 16 kHz mono PCM WAV input, CUDA, four threads, and transcript-only `--output`. Do not parse stdout banners as speech. Bound utterance capture to 30 seconds and transcription to a 15-second deadline. An overlong utterance is reported as too long rather than silently presented as fully transcribed. Silence/empty text does not trigger the LLM. Clean up input and transcript files on success, failure, disconnect, or cancellation; terminate and reap the process group on cancellation/deadline.

Keep the per-utterance subprocess for the first MVP. A persistent `transcribe_cpp` binding would need a separate shared-library build and concurrency validation; it is a later optimization, not a prerequisite or a claimed completed capability. Do not substitute NeMo/ONNX runners for the Handy export based only on model name.

### LLM: installed Qwen3.5 9B through Ollama

Reuse the existing 6,594,474,711-byte quantization, digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`, without pulling a replacement or upgrading Ollama for this design. Use Pipecat's Ollama/OpenAI-compatible service directed only at the loopback URL. Provide a local-English-assistant system instruction, plain speech output, no Markdown, and short responses. Disable reasoning with the tested compatibility setting and never synthesize a reasoning field.

Use a 4,096-token context and a 128-token output limit, with bounded session history, one inference request at a time, a 30-second reply deadline, and explicit model warmup. Create a project-specific local alias `local-voice-qwen` from the installed source model with `num_ctx 4096` and `num_predict 128`; it reuses existing weight blobs and does not modify the source model or global daemon defaults. The installed server is 0.24.0; its tested compatibility endpoint already returned a correct short answer with `reasoning_effort: none`. Verify the selected Pipecat service transmits that setting and that the alias's actual context allocation is 4,096 before admitting audio.

### TTS: Phonon 31M English on CPU

Pin `ptts==0.5.0` and the cached English model revision `92741c5b07d085ff840e26fea8ce43110b87852f`. Its Q8 weights are 64,512,352 bytes and the complete selected snapshot is 65,051,089 bytes. Run in process on CPU with four synthesis threads, keeping the model loaded. This model is Apache-2.0 and English only.

The verified API emits 24 kHz mono float32 PCM chunks; convert/clamp to little-endian signed int16 audio frames with matching rate/channel metadata. Use bounded sentence aggregation and output queues, not one full answer before beginning synthesis.

One synthesis worker owns and iterates each stream. On interruption, set a cancellation event, discard stale PCM using generation/turn IDs, and close the iterator from its owning worker between chunks. Merely canceling `asyncio.to_thread` does not stop Rust synthesis. Do not call blocking stream closure on the event loop or promise instantaneous cancellation.

## Turn handling and resource safety

Silero VAD runs locally on CPU. Start with approximately 600 ms end-of-speech silence and tune using actual microphone tests. Barge-in marks the current reply canceled, requests LLM/TTS cancellation, drops queued old-generation audio, and lets the new user turn proceed. Stop/disconnect releases the microphone and peer connection, cancels background tasks/subprocesses, clears bounded queues and history, and returns the single-session slot.

No cloud fallback is allowed. Missing model, invalid export, unavailable GPU, deadline, or model crash must be visible rather than silently switching providers. Bind inference and signaling HTTP services to loopback; restrict signaling origins and validate input shape/size. Keep logs to status/timing/error metadata by default, not full audio or private conversation contents.

## Tailscale access

Preserve existing Serve routes. Allocate a separate HTTPS listener, proposed port 8443, proxying the Next.js app on loopback 3000. Proxy same-origin POST and PATCH `/api/offer` requests to the agent's offer handler. Browser and server ICE-server lists are empty, removing public Google STUN from the example.

Tailscale Serve supplies private HTTPS for the page and HTTP signaling only; it does not relay UDP WebRTC media. Configure and inspect actual host candidates, then verify the browser-selected candidate pair and bidirectional media over the tailnet. Do not treat a successful HTTPS page load as proof that audio works, or promise that every client/browser VPN arrangement works without adjustment. If the actual remote device cannot establish a reachable pair, report the candidate/network problem before expanding infrastructure.

The public repository documents generic hostnames, not private peer inventory or concrete tailnet addresses. No Funnel or public exposure is included. A remote-client test requires that device to be online and microphone permission granted.

## Version and upstream baseline

- Python 3.12; `pipecat-ai[webrtc,silero,ollama]==1.15.0`, with a committed `uv.lock` after installation and compatibility validation.
- `@pipecat-ai/client-js==1.16.0` and `@pipecat-ai/small-webrtc-transport==1.11.2` have compatible declared peer dependencies; source inspection is not an integration test.
- Pipecat 1.15.0 source commit `fda1d5e0cde9625c8dcef7b653ed731c2ce63672`.
- Inspected official examples commit `7875d02bc0714d59528b0c8797664308af960a3a`, `p2p-webrtc/voice-agent`.
- The example defaults to Gemini Live with a Google API key and public Google STUN; neither survives in this design. Its unbounded dependency range is replaced with a coherent exact pin.
- Next.js 16.4.5, React/React DOM 19.3.1, and the existing Node 24/pnpm tooling; versions and Next's Node requirement were checked against the package registry. Commit the exact resolved frontend lockfile after installation and build validation.
- `ptts` source tag 0.5.0 resolves to `e851445158f07061f063e3edff0080f9426c0da2`.

## Evidence already obtained, not end-to-end claims

Codex investigators performed read-only inspection and temporary smoke runs. No application dependencies, product code, GitHub project, or Serve changes were installed/created as part of these checks.

- Parakeet: two fresh-process runs of an 11-second public English sample produced the expected transcript in 0.529/0.511 seconds, including approximately 0.25 seconds loading. Sampled short-clip GPU peak was 910 MiB. This is not a bound for 30-second input or noisy real speech.
- Phonon: CPU model load 0.125 seconds, first PCM 0.217 seconds, observed 80 ms chunks, early same-thread closure 0.057 seconds, and a successful subsequent generation. Maximum observed process RSS was about 331 MiB. These are individual smoke measurements, not interruption/latency guarantees.
- Ollama: cold native request took 29.59 seconds, mostly model loading; a subsequent short OpenAI-compatible request returned `Four.` in 0.233 seconds with reasoning disabled. Warmup is therefore part of readiness, and deployment provenance is determined by endpoints/process evidence, not what a model says about itself.
- `ollama ps` reported 8.6 GB runtime allocation at 4,096 context for Qwen despite its 6.6 GB downloaded size. Total GPU usage after the check, including an existing embedding model, was 8,515 MiB with 7,433 MiB free. Concurrent voice-pipeline peak has not been measured.

## Acceptance criteria

1. A clean setup resolves the pinned Python/frontend dependencies, reuses or explicitly acquires only the named models, verifies their formats/sizes, and requires no cloud API key.
2. All three local stages pass real speech/text/PCM checks; a representative network trace shows no hosted inference or public STUN calls for that tested run. Tailscale coordination/transport traffic is distinguished from model uploads.
3. Ten short English question/reply turns complete in the browser. Record end-of-speech-to-first-playback latency, stage timings, failures, and total concurrent GPU/RAM peak. Aim for first playback within five seconds on warm short turns; publish the measurements and any misses, not an unverified real-time claim.
4. Barge-in prevents old replies resuming; rapid Stop/Start, disconnect during transcription/synthesis, silence, microphone denial, and model failure leave no zombie subprocess or stuck session slot.
5. A real remote browser on the tailnet grants microphone access, selects a reachable candidate pair, and delivers two-way audio. Browser playback/echo must be checked with the intended speaker/microphone arrangement; clearly mark this unverified until performed.
6. Next.js build/type checks and meaningful adapter/cancellation tests pass. No recordings, transcripts, credentials, model weights, or machine-specific config enter the repository, and existing OpenClaw and Tailscale routes remain intact.

## Review boundary

Gian explicitly instructed: “Create a local repo under github/gianpaj. Init. Write plan and start implementation. Use codex again.” That instruction authorizes writing the plan and proceeding without another artifact-review pause. Codex implements from an isolated feature worktree. No external GitHub publication is authorized by the local-repository instruction, and no completed-app claim is made by this design.

## Primary references

- https://github.com/pipecat-ai/pipecat-examples/tree/7875d02bc0714d59528b0c8797664308af960a3a/p2p-webrtc/voice-agent
- https://docs.pipecat.ai/client/concepts/choosing-a-transport
- https://docs.pipecat.ai/api-reference/server/services/llm/ollama
- https://github.com/handy-computer/transcribe.cpp/tree/5700f127e38dd53c4735ea23ef743b4b538eacd4
- https://huggingface.co/handy-computer/parakeet-tdt-0.6b-v3-gguf
- https://huggingface.co/gradium/phonon-31m-en
- https://github.com/gradium-ai/xn-ptts/tree/e851445158f07061f063e3edff0080f9426c0da2
- https://docs.ollama.com/api/openai-compatibility
- https://tailscale.com/docs/reference/examples/serve
