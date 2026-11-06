# Runbook

## Services and start/stop

- Next.js: `127.0.0.1:3000`.
- Python HTTP/signaling: `127.0.0.1:7860`.
- Existing Ollama: `127.0.0.1:11434`.
- WebRTC: direct encrypted browser ↔ Python peer UDP. ICE servers are empty by default; optional public STUN is configured identically on both peers.

`pnpm dev` starts only the first two, in separate process groups, with Next.js development/HMR enabled. For the shared Tailscale HTTPS handoff, run `pnpm build` once and then `pnpm start`; the same supervisor starts the Python agent and production Next.js without a development WebSocket. Rebuild after frontend changes. Ctrl+C or either child's exit terminates both. Occupied ports cause an explicit error, not a kill of an unrelated process. Ollama and OpenClaw are never stopped or reconfigured.

`GET /health` (browser proxy `/api/health`) reports initializing/ready/error and busy. Startup validates paths, loads local CPU Phonon, creates only the project alias, warms Qwen, verifies its actual 4,096 context, then synthesizes/transcribes a public “Ready” phrase through the actual GPU Parakeet runner. A prior cold load took about 29 seconds; Start remains disabled until these checks complete.

While the app is running, a lifespan-owned task refreshes only the project Ollama alias every 60 seconds using an empty generation and finite five-minute retention. If the alias was evicted, readiness changes to `warming` until it is loaded again; refresh errors disable Start and are retried on the next cycle. Shutdown cancels this task; the model then expires normally instead of being pinned indefinitely or forcibly unloading other Ollama users' models. No global Ollama configuration is changed.

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

Use the production launcher behind this HTTPS proxy:

```bash
pnpm build
pnpm start
```

The development server's HMR/cross-origin checks are not part of the shared app; do not widen development origins to make the handoff work.

Serve carries the page and same-origin POST/PATCH signaling, **not WebRTC UDP audio**. Inspect the selected pair in `chrome://webrtc-internals` or WebRTC stats and test bidirectional media. If the page loads but audio fails, check candidate reachability, VPN/browser behavior and firewall. Do not silently introduce public STUN/TURN. Never use `tailscale serve reset` to remove just this app.

## Temporary public testing with Cloudflare

A Cloudflare Tunnel exposes the HTTPS page and signaling, **not WebRTC UDP media**.
For a public test, opt in to public address discovery on both peers:

```dotenv
WEBRTC_STUN_URL=stun:stun.cloudflare.com:3478
```

The browser receives this non-secret setting from `/api/health`. It does not change
local STT, LLM or TTS inference. STUN discovers public addresses; it is not a relay.
Restrictive/symmetric NATs and networks blocking UDP may still require TURN, which
this prototype does not configure. A page that loads is not proof that audio works.

Build the production frontend, then run a separate tunnel (preserve any existing
Cloudflare service and all Tailscale routes):

```bash
pnpm build
systemd-run --user --unit=local-voice-cloudflare --collect \
  cloudflared tunnel --no-autoupdate --url http://127.0.0.1:3000
journalctl --user -u local-voice-cloudflare --no-pager
```

Add the **exact** resulting `https://...trycloudflare.com` origin to
`ALLOWED_ORIGINS`, retaining the existing local/Tailscale origins, then restart
only the voice app. Keep the agent and Ollama ports off the tunnel. The random URL
is temporary and changes if the tunnel is recreated; it is not a permanent deployment.
Anyone with this public URL can reach the demo. The existing single-session lock
and five-minute inactivity timeout still apply: friends should test one at a time.

Validate the actual public candidate/audio path before sharing:

```bash
uv run --project apps/agent --group browser python scripts/browser-smoke.py \
  --url https://YOUR-HOST.trycloudflare.com --public-only-ice --turns 1
```

This strips private host candidates in both signaling directions and checks a
successful public candidate address plus non-silent decoded response audio.
Globally routable IPv6 host candidates are retained alongside STUN candidates. It still runs on the
same host, so it is not an independent remote-device or all-networks guarantee.
With STUN enabled, a normal/private smoke test can use `--lead-in-seconds 15` to
allow address discovery before the fake microphone starts speaking.

Stop only this tunnel with `systemctl --user stop local-voice-cloudflare`.
The voice app and Tailscale URL remain independent.

## Troubleshooting

- Microphone inaccessible: use HTTPS or localhost, allow permission, select an input. Test actual echo with the intended speaker/microphone; headphones help diagnose barge-in.
- Origin rejected: add the exact scheme/host/port to `ALLOWED_ORIGINS` and restart. Inference endpoints stay loopback.
- Agent in use: stop the existing browser session; it is never replaced. ICE/pipeline teardown releases the slot; idle sessions time out after five minutes.
- Lost connection: a terminal SDK disconnect uses the same cleanup as Stop, releases microphone/audio resources and clears the transcript. Start can reconnect without a page reload once the server slot is released.
- Missing model/invalid format: correct `.env`; there is no automatic pull or fallback. The Handy GGUF requires the matching Handy runner.
- GPU OOM: stop unrelated GPU work, or explicitly select a smaller existing local source model and remeasure. Do not alter global Ollama defaults.
- Long utterance: pause between thoughts; exceeding 30 seconds rejects that utterance rather than pretending it was complete.
- Playback blocked: click Enable sound. Browser autoplay policy varies despite the initial Start gesture.

Default logs filter library DEBUG because it can include transcripts/SDP. STT subprocess groups are terminated/reaped on timeout/cancel, temporary files are removed, and Phonon closes its native iterator in the same worker thread that owns it. Setup/model acquisition and Tailscale coordination traffic are not model inference uploads.

## Managed test instance

The handoff instance runs as the transient user service `local-voice-agent.service`, with the canonical repository as its working directory. This keeps the two project processes alive independently of the chat turn. It does not change boot/login startup and does not own Ollama.

```bash
systemctl --user status local-voice-agent
systemctl --user stop local-voice-agent
```

Stop that instance before running `pnpm dev` or `pnpm start` manually, because both use ports 3000/7860. A transient service is collected after it stops; run `pnpm build` then `pnpm start` to start the HTTPS handoff manually again (`pnpm dev` remains available for local development). The added tailnet-only HTTPS 8443 proxy is separate and can be removed with `tailscale serve --https=8443 off`, without resetting any other route.
