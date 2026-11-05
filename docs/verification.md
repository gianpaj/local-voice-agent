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

## Canonical repository and private HTTPS handoff

The parent fast-forwarded the completed implementation/review fixes into the requested canonical repository on `feat/pipecat-local-voice`. Setup and preflight succeeded from that directory. The independently reviewed fix had eleven passing Python checks and a successful production build/typecheck; the canonical production build also passed.

The first private HTTPS browser attempt exposed blocked Next development HMR: the server page was available, but Start remained disabled. The shared instance was changed to the supervised `pnpm start` production mode after `pnpm build`, without broadening allowed development origins. Production HTTPS showed a secure browser context with no page or console errors.

A new tailnet-only HTTPS 8443 proxy was added for the app; before/after checks confirmed the existing background routes were preserved. The reviewed app runs from the canonical directory under the transient user unit `local-voice-agent.service`; it is not enabled for boot/login.

Actual Chromium on the GPU host then exercised the private HTTPS origin with a synthetic public microphone question through genuine WebRTC and local Parakeet/Qwen/Phonon. One complete English turn passed, with two assistant text segments, nonzero decoded audio energy (0.16648), 409 second-client rejection, actual microphone-sender replacement, and Stop clearing/closing the session. HTTP destinations were limited to that private origin. The observed transcribing-event-to-speaking interval was 0.805 seconds (plus VAD silence); sampled machine-total GPU usage peaked at 9,348 MiB. These remain software/event measurements, not acoustic latency or an allocation bound.

After the test the HTTPS health endpoint returned ready=true/busy=false and the service was active. This validates the deployed origin from the host, not the user's distinct remote device, physical microphone, speaker or echo behavior; those still require the intended device.

## 2026-11-05 — retained cascade timings

The observability extension has **22 passing backend tests plus three offline-analysis tests**. Coverage includes content filtering (span names/attributes/events/exceptions/status descriptions), rotation, bounded-queue drops, write failures, empty/timeout/cancel outcomes, delayed-observer ownership, and speaking-event timestamp/owner preservation. Initial real-audio testing exposed an extra spurious turn; ownership was moved inline and the regression is covered. Canceled work is no longer exported as a successful application span/turn.

Actual Chromium through the private production HTTPS origin used temporary public Phonon microphone fixtures and genuine Parakeet/Qwen/Phonon inference:

- Three spoken turns produced exactly three turn IDs and ten stage records (three STT, three LLM, four TTS). Reconnection created a fresh session, with no invented user turn. Decoded assistant audio was non-silent; second-client rejection, sender replacement, Stop cleanup and server-initiated disconnect/reconnect passed.
- A separate two-turn interruption/reconnect run after the final timestamp fix produced exactly two turn IDs and seven stage records. The first turn was interrupted and the second completed; the old reply did not resume. All stage records retained their original turn ID.
- Saved records contained STT invocation/total time and audio duration, LLM first answer/native response timings and token usage, TTS first PCM/generation time/audio duration/RTF, and distinct generated-PCM/server-speaking turn latencies. Both browser-playback and acoustic latency remained null.
- The three-turn run's generated-PCM TTFA observations were approximately 907, 938 and 856 ms; the explicit barge-in run observed approximately 1,337 and 737 ms. These are small-sample software checks, **not** a stable performance baseline, quality score or SLO. Some validation overlapped local setup/build work. Endpointing silence is additional to this VAD-notification-to-PCM interval.
- The metadata log was inspected for test speech/sentinel content and correlation consistency. Sanitized trace rows were present; stage/turn summaries are not double-counted by the analyzer. Runtime artifacts remain ignored under `.local/`, not in Git.

Physical/remote-device audio, acoustic onset and broad speech quality remain outside these checks. Subsequent native-inference/Whistle/Rustvani experiments must preserve this verified baseline and report their own evidence separately. Private GitHub publication is authorized and already exists; earlier “not published” notes above describe the initial implementation stage, not current repository status.
