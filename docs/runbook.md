# Runbook

## Services and start/stop

- Next.js: `127.0.0.1:3000`.
- Python HTTP/signaling: `127.0.0.1:7860`.
- Existing Ollama: `127.0.0.1:11434`.
- WebRTC: direct encrypted browser ↔ Python peer UDP. Both ICE-server lists are empty.

`pnpm dev` starts only the first two, in separate process groups; Ctrl+C or either child's exit terminates both. Occupied ports cause an explicit error, not a kill of an unrelated process. Ollama and OpenClaw are never stopped or reconfigured.

`GET /health` (browser proxy `/api/health`) reports initializing/ready/error and busy. Startup validates paths, loads local CPU Phonon, creates only the project alias, warms Qwen, and verifies its actual 4,096 context. A prior cold load took about 29 seconds; Start remains disabled until this completes.

## Private Tailscale access

The project does not change Serve automatically. Preserve existing routes:

```bash
tailscale serve status
# Only if HTTPS 8443 is unused:
tailscale serve --bg --https=8443 http://127.0.0.1:3000
```

Add the exact resulting origin to `.env`, then restart the agent:

```dotenv
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://gpu-host.example-tailnet.ts.net:8443
```

Use the HTTPS page on the remote Tailscale device and grant its browser microphone permission. No Funnel/public exposure is needed. Tailnet ACLs control peer access.

Serve carries the page and same-origin POST/PATCH signaling, **not WebRTC UDP audio**. Inspect the selected pair in `chrome://webrtc-internals` or WebRTC stats and test bidirectional media. If the page loads but audio fails, check candidate reachability, VPN/browser behavior and firewall. Do not silently introduce public STUN/TURN. Never use `tailscale serve reset` to remove just this app.

## Troubleshooting

- Microphone inaccessible: use HTTPS or localhost, allow permission, select an input. Test actual echo with the intended speaker/microphone; headphones help diagnose barge-in.
- Origin rejected: add the exact scheme/host/port to `ALLOWED_ORIGINS` and restart. Inference endpoints stay loopback.
- Agent in use: stop the existing browser session; it is never replaced. ICE/pipeline teardown releases the slot; idle sessions time out after five minutes.
- Missing model/invalid format: correct `.env`; there is no automatic pull or fallback. The Handy GGUF requires the matching Handy runner.
- GPU OOM: stop unrelated GPU work, or explicitly select a smaller existing local source model and remeasure. Do not alter global Ollama defaults.
- Long utterance: pause between thoughts; exceeding 30 seconds rejects that utterance rather than pretending it was complete.
- Playback blocked: click Enable sound. Browser autoplay policy varies despite the initial Start gesture.

Default logs filter library DEBUG because it can include transcripts/SDP. STT subprocess groups are terminated/reaped on timeout/cancel, temporary files are removed, and Phonon closes its native iterator in the same worker thread that owns it. Setup/model acquisition and Tailscale coordination traffic are not model inference uploads.
