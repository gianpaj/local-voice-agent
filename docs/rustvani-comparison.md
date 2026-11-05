# Rustvani: isolated local pipeline experiment

Date: 2026-11-05. **Do not replace the working Pipecat service based on this spike.**

## What was inspected and built

[Allenmylath/rustvani](https://github.com/Allenmylath/rustvani/tree/b134ad055c754efaa690fec72f530d5390fd6180) is BSD-2-Clause. The pinned source identifies itself as **0.4.0-dev.27**, newer than the dev.22 README observed earlier. Its APIs are moving: our Cargo dependency is an exact Git revision with a lockfile, not an unqualified stable `cargo add rustvani`.

`experiments/rustvani` disables every default feature. This avoids the default hosted STT/TTS providers, ONNX Runtime, WebSocket services and Postgres. The core pipeline still includes native audio/VAD-related dependencies; disabling optional providers does not make the dependency tree empty. Its build script copies bundled VAD/SmartTurn assets into an ignored project cache even though this headless experiment does not use them. Runtime makes no request to those providers and does not enable any cloud fallback.

The experiment uses actual Rustvani `PipelineTask`, queued `FrameProcessor`s, typed transcription/LLM/audio frames, error propagation and lifecycle. It is not a standalone Rust script that bypasses the framework.

## Same models; explicitly different integration scope

A single resident, stdin/stdout-only Python model bridge reuses:

- The existing Parakeet Q8_0 checkpoint, CUDA `transcribe.cpp` CLI and its unchanged 16 kHz int16 input, four-thread/no-timestamp settings. It still reloads per utterance; no resident-STT speedup is claimed here.
- The existing project Ollama Qwen alias: identical system prompt, 4,096-token alias context, temperature 0.5, 128 output-token limit, `reasoning_effort: none`. No model pulls, alias updates or global Ollama changes.
- The existing `ptts==0.5.0` Rust-backed Phonon English voice on CPU, Q8_0, four threads, streamed 24 kHz int16 PCM. Its engine is loaded/warmed outside the timed turn and reused inside the process.

The bridge receives transient conversation text and audio over bounded private IPC, but the report contains only timings, configuration labels, outcome, sample count and energy. No recording, transcript, prompt, model path or response text is saved. Benchmark files belong in ignored `.local/`, not Git.

**This is a one-completed-utterance headless test.** It does not run VAD, browser WebRTC, acoustic playback, interruption/reconnect or conversation history. It buffers the complete short LLM reply before starting Phonon; the production Pipecat pipeline uses sentence aggregation/streaming. Therefore its utterance-injected → first-PCM-sink time is **not** production end-of-speech TTFA and cannot establish a framework win/loss. `tts_buffering: full_reply` and `vad_included: false` are retained in every report.

## Run without changing the app

Requirements: Rust **1.99.1** (the tested isolated toolchain), existing `apps/agent/.venv`, populated ignored project `.env`, the already installed model files, and an already warm project Ollama alias. This experiment does not start, stop or reconfigure the production service. Use an exclusive inference window for any performance interpretation.

```bash
# Run from the repository root. Build artifacts stay ignored.
export CARGO_BUILD_JOBS=2
export CARGO_TARGET_DIR="$PWD/.local/rustvani-target"
export RUSTVANI_CACHE_DIR="$PWD/.local/rustvani-model-cache"
cargo build --locked --release --manifest-path experiments/rustvani/Cargo.toml
python3 -m unittest discover -s experiments/rustvani -p 'test_*.py' -v

# Input must be public/labeled, <=30 s, 16 kHz mono signed-int16 WAV.
mkdir -p .local/benchmarks
experiments/rustvani/run /path/to/public-fixture.wav \
  > .local/benchmarks/rustvani.json
```

For a parent-managed worktree, set `RUSTVANI_PROJECT_ENV` to its authorized local environment and `RUSTVANI_PYTHON` to the existing installed agent interpreter. Do not commit those machine-specific paths. A missing executable/config/model, malformed/oversized IPC line, model exception, wrong audio format or silence/no audio is an error, not zero latency. The harness bounds startup to 60 seconds, turn processing to 90 seconds and shutdown; it records forced shutdown separately. This does not prove realtime barge-in cancellation parity.

## What the numbers mean

The Rust process uses one monotonic clock for all frame boundaries; Python reports same-process per-adapter durations on its own monotonic clock. Never subtract timestamps between those processes.

- `stt_model_adapter_ms`: existing CLI adapter wall time, including subprocess/model loading and temporary I/O; not encoder-only native time.
- `llm_model_first_answer_token_ms`: local HTTP request → first nonempty answer token, not private reasoning. Total LLM adapter wall time is separate.
- `tts_model_first_pcm_ms`: Phonon stream request → first generated PCM, not browser output or acoustic sound.
- `stt_to_llm_framework_handoff_ms`, `llm_to_tts_framework_handoff_ms`: end of one Rust handler → start of the next. These are measured queue/dispatch gaps, not all possible framework CPU cost.
- `injected_utterance_to_first_pcm_sink_ms`: full completed input frame injection → first PCM at the headless sink, excludes VAD/browser and uses full-reply TTS buffering.
- Rust handler boundaries additionally include bridge serialization/IPC; Python durations do not. Differences include measurement/code overhead, not a clean microbenchmark of only Rust versus Python.
- Missing observations stay null. Errors/timeouts retain outcome. Do not add p95s or infer model quality from sample energy.

## Browser migration gap

Rustvani has real [P2P WebRTC](https://github.com/Allenmylath/rustvani/blob/b134ad055c754efaa690fec72f530d5390fd6180/doc/vaniwebrtc.md), with an opt-in `vaniwebrtc` feature. It requires a larger `webrtc-rs`/Opus/C toolchain and uses WebSocket signaling plus RAVI control messages. Our frontend uses Pipecat SmallWebRTC HTTP offer/PATCH/DELETE signaling and RTVI. These are **not tested protocol-compatible**. Existing Next.js React styling could be reused, but browser connection/client, cancellation, device controls, decoded audio and Tailscale host ICE candidates would require another tested adapter.

The upstream WebRTC default includes public Google STUN; a private experiment must explicitly set `ice_servers: []`, no TURN, bind signaling to loopback, advertise a reachable tailnet interface and preserve existing Tailscale routes. None of that browser migration is enabled by this headless spike.

## Tool calling readiness, not an implementation

The pinned [OpenAI handler](https://github.com/Allenmylath/rustvani/blob/b134ad055c754efaa690fec72f530d5390fd6180/src/services/llm/openai.rs) supports streamed native tool calls, named registry lookup, linked IDs/results, bounded recursive rounds and staged tool-call/result context commits. This is a useful foundation, not correctness proof for our local model. In this source revision:

- `OpenAILLMConfig`/its request struct has no generic extra-body or `reasoning_effort` setting. Our baseline-compatible local bridge therefore does **not** exercise that native tool loop.
- Handlers receive raw argument strings; registration alone does not validate our future tool schema or business policy.
- The tool batch is awaited sequentially in `run_inference`; independent reads are not automatically parallelized there.
- No per-tool deadline is applied around those registry calls in that loop. Future integrations must supply deadlines, validation, linkage, cancellation policy and side-effect safeguards.
- Default info logs include transcript text in the user aggregator. Production telemetry would need our metadata-only policy rather than enabling unrestricted framework logs.

Before adoption, run the same tool-selection/argument/task-success cases, preserve whole tool-call/result groups when trimming context, and separate acknowledgement audio from result-grounded answer latency. No actual external tools/actions were added.

## Evidence and decision

Build, model-free fault/privacy tests and same-model runtime evidence are recorded below after execution. A small successful roundtrip can establish feasibility; it cannot establish acoustic quality parity, robust interruption handling, cross-device networking or a speedup over the complete Pipecat app.
