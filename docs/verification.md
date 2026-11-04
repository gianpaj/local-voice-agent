# Verification — 2026-11-04

These are actual local-worktree checks, not a deployment/remote-device completion claim.

## Environment

Linux, Ryzen 5 7600X, about 30 GiB RAM, RTX 4060 Ti 16,380 MiB. Node 24.22.1, pnpm 11.16.0, Python 3.12.13, Pipecat 1.15.0, ptts 0.5.0, Next 16.4.5, React 19.3.1, client-js 1.16.0, SmallWebRTC 1.11.2. Existing Ollama server 0.24.0 was reused, not upgraded.

## Reproducible tooling and unit checks

`pnpm setup`, `pnpm preflight`, `pnpm test`, `pnpm build`, and `pnpm typecheck` pass. The setup was rerun against the committed lockfiles and preserved `.env`.

Eleven Python checks cover transcript-only output rather than stdout banners, silence bypass, invalid/overlong PCM, subprocess timeout and cancellation/reaping, loopback/model validation, PCM clipping/format, same-owner native-stream closure and restart, bounded utterance capture, signaling readiness/origin/size/busy/invalid-body behavior, and project-only finite Ollama keepalive/readiness recovery/cancellation. Two upstream deprecation warnings remain (`audioop` and a Pipecat compatibility class); Python is intentionally pinned below 3.13.

## Individual real models

Command: `uv run --project apps/agent python -m local_voice.check --smoke`.

- Real Phonon speech (“Hello. What is two plus two?”) generated 134,400 bytes of valid 24 kHz mono int16 PCM. Observed first chunk 0.046 s and total generation 0.180 s in that short warm run.
- That speech was resampled to 16 kHz mono, passed to the actual CUDA Parakeet runner, and transcribed as “Hello, what is two plus two?” in 1.238 s on the first check. Subsequent browser utterances were about 0.45–0.60 s including CLI/model startup.
- Local Ollama alias warmed successfully; `/api/ps` confirmed 4,096 context. Warmup in the observed repeated checks was 0.20–0.28 s. Prior cold-load measurement was about 29 s, so startup readiness explicitly waits for it.
- Final startup readiness also synthesized/transcribed “Ready” through the actual local TTS/GPU STT path; `/health` reported `stt_preflight_seconds: 0.469` before enabling the browser.

These measurements are for short synthetic/public English speech, not a noisy-speech quality benchmark.

## Assembled browser voice pipeline

Actual headless Chromium 1248, public English questions supplied as a fake microphone WAV, HTTP loopback page, genuine browser ↔ aiortc WebRTC, real Parakeet → Qwen → Phonon. No mocked model responses.

Ten questions and replies completed: arithmetic, sky color, fruit, Paris, animal, Earth, multiplication, ocean, and goodbye. Replies matched the questions. RTVI segment updates were deduplicated in the UI. The stopped session cleared the transcript.

- Selected host candidate pair succeeded; browser ICE-server configuration was `[]`.
- After fixing remote-track handling, the browser received 653,471 bytes of RTP audio with decoded nonzero audio energy (2.460) and zero discarded audio packets. This establishes decoded software playback, not a human hearing a physical speaker.
- VAD-complete/transcribing event → bot-started-speaking: **0.847–1.114 s**, median **0.895 s**, across the ten-turn run. Add the configured approximately 600 ms end-of-speech silence. This is software-event latency on localhost, not a measured acoustic end-to-end/tailnet guarantee.
- Sampled machine-total GPU usage peaked at **8,703 MiB** in that ten-turn run (includes other already-loaded GPU services). A separate barge-in run peaked at **8,741 MiB**. Sampling was once per second, so this is an observed peak, not a hard allocation bound. Full concurrent RAM peak was not measured.
- Browser HTTP destination set was only the app's localhost origin. No hosted inference or public STUN was configured. This is a representative browser-request trace plus endpoint/source checks, **not a host-wide packet capture**.

The first integration attempt caught a real playback bug: SmallWebRTC remote-track callbacks omit participant metadata; a strict participant-present check discarded them. The corrected handler attaches only nonlocal audio. It also caught the default SDK device manager fetching `c.daily.co` assets. The app now supplies a small native browser media manager instead; no Daily constructor/room or remote asset fetch was observed in the repeated run. The pinned transport's runtime `getAudioTransceiver` method is used in one typed boundary for microphone sender replacement because its published declaration omits that method; microphone replacement was browser-tested.

## Interruption and lifecycle

- A real spoken dragon-story reply was interrupted by a second utterance, “Stop, say only okay.” After the second interruption, only “Okay”/“Okay.” appeared as new spoken output; the old story did not resume. Native generation IDs/worker cancellation and Pipecat output flushing were exercised.
- The committed browser smoke harness passed the same two-turn barge-in check with real decoded audio and no external HTTP destinations.
- A second offer received **409** while the first browser was fully connected, preserving the original peer.
- Mute disabled the actual sender track. Microphone replacement succeeded. Stop closed the peer, cleared the transcript and released the session slot; a subsequent Start/Stop succeeded.
- The final production-build smoke additionally asserted that selecting another fake microphone changed the actual RTP sender track ID, rather than only updating a UI selection. It passed with decoded audio energy, localhost-only HTTP traffic and 409 second-session rejection.
- A simulated browser `NotAllowedError` displayed “Microphone permission denied” instead of leaving a stuck connection.
- Native STT timeout/cancellation tests prove the child process is reaped; Phonon tests prove stream closure occurs on its owning worker and another generation can start.
- Independent review caught a terminal-disconnect bug: the React callback left the old client reference populated, causing the next Start to return immediately. It now calls the ownership-guarded unified Stop cleanup. A production-build browser regression completed a real spoken turn, ended the actual server peer via DELETE, asserted all captured microphone tracks ended/all browser AudioContexts closed/playback detached/transcript cleared, then successfully connected a new peer without reloading. This run also passed decoded non-silent audio, second-offer rejection and actual microphone sender replacement.
- Independent review also confirmed that ordinary Ollama requests leave the alias eligible for idle eviction while the original readiness flag remained true. Maintenance now refreshes only the project alias every 60 seconds with finite five-minute retention. Focused HTTP regressions exercise eviction → not ready → rewarm → ready, refresh failure/recovery, and task cancellation without any model unload request.
- A real restarted backend reached readiness at 22:51:35 UTC; after its first idle maintenance cycle, Ollama `/api/ps` showed the project alias's expiration renewed to 22:57:35 UTC with the same 4,096 context. The unrelated embedding model remained loaded. This verifies the actual server retention API, in addition to mocked failure/recovery coverage.

Reproduce the software browser checks while the app is running:

```bash
# Optional test dependency group; no browser binaries are downloaded by normal setup.
uv run --project apps/agent --group browser playwright install chromium
uv run --project apps/agent --group browser python scripts/browser-smoke.py --turns 10
uv run --project apps/agent --group browser python scripts/browser-smoke.py --barge-in
uv run --project apps/agent --group browser python scripts/browser-smoke.py --disconnect-reconnect
```

An existing Chrome binary can be selected with `--executable /path/to/chrome`. The harness creates only public test speech in a temporary directory and removes it afterward.

## Still unverified / deliberately not done

- Real remote-browser Tailscale ICE/media reachability and hardware microphone/speaker/echo quality require the intended remote device. No Serve route was changed by the implementation worker.
- No acoustic latency or full RAM peak measurement; no host-wide network capture. Do not label the above browser-event timing as acoustic latency.
- No real microphone unplug/permission-revocation test, browser crash-during-native-call injection, or forced native-library hang. Simulated/model-adapter failure paths are covered, not every OS/device failure.
- Phonon cancellation is cooperative between native chunks, not an instantaneous hard kill of a Rust worker thread.
- No GitHub remote, push, PR, model upgrade, public exposure, or OpenClaw transcription changes. The only persistent inference change was the project-owned `local-voice-qwen` alias reusing existing Ollama blobs.
