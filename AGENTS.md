# Local Voice Agent

## Scope
- English only. Parakeet STT, Phonon31M CPU TTS, local Ollama Qwen. No Whisper or hosted inference fallback.
- Next.js frontend; Python Pipecat backend; direct SmallWebRTC. No LiveKit/Daily rooms/cloud service.
- Browser mic/speaker over private Tailscale HTTPS is the active audio mode.
- Follow docs/superpowers/specs and the implementation plan. User explicitly authorized writing the plan and starting implementation, without another approval pause.

## Git workflow
- The user authorized private publication to `gianpaj/local-voice-agent` on 2026-11-05. Use the verified `origin` remote for that repository; do not change its visibility or publish elsewhere without authorization.
- Never commit to main or push main; use feature branches and conventional commits. No PRs.
- Editing agents use parent-prepared isolated worktrees and verify starting HEAD before edits. Parent integrates finished commits.
- Preserve user changes and avoid destructive cleanup.

## Models and services
- Keep all weights, recordings, transcripts, secrets, .env and machine-specific paths out of Git. Use ignored local config/env and portable examples.
- Reuse existing installed model exports with their matching runtime. Do not change OpenClaw transcription or global Ollama settings.
- Do not modify existing Tailscale Serve routes. Parent owns any final new route/startup.
- Bind web/signaling/model HTTP endpoints to loopback. Keep model inference local; no cloud API keys.
- Test real PCM/STT/LLM/TTS behavior, cancellation cleanup and the browser connection. Label remote-device audio unverified until exercised.
- Do not require new shared Parakeet library build for the first MVP: use tested per-utterance CLI.

## Delivery
- Report work/test evidence through parent; no direct Telegram/external messages from workers.
- Commit implementation on your assigned feature branch. Push only within the user's authorized scope; never push main or create a PR automatically.
