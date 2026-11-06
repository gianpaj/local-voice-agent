# Public browser test — 2026-11-06

## Scope and implementation

- User requested a temporary public URL for a handful of friends without Tailscale.
- Keep the existing voice models, one-session admission and five-minute idle limit.
- Run a separate Cloudflare Quick Tunnel to the production Next.js port only;
  preserve the unrelated system tunnel and existing Tailscale Serve routes.
- `WEBRTC_STUN_URL` is opt-in, validated as a credential-free STUN URL, and passed
  to both Python and browser ICE configurations. Empty remains the portable default.
- Tunnel origin is explicitly allowlisted in the ignored local environment.
  Do not commit the local environment, credentials, recordings or raw SDP.

## Evidence and limits

- Backend tests: 59 passed. An existing cancellation test failed once due to its
  fixed-time scheduling assumption; its targeted rerun and full suite rerun passed.
  No resident-STT behavior changed. Frontend typecheck and production build passed.
- Initial public IPv4 STUN-only/hairpin attempt timed out. Retain this as a failed
  attempt, not evidence that all IPv4 clients fail or that a tunnel relays WebRTC.
- Public-candidate filtering must preserve globally routable IPv6 host addresses.
  A host candidate is not necessarily private. Filter by address reachability too.
- Browser test through the public HTTPS origin, with private candidates removed
  in both signaling directions: one utterance and two assistant segments;
  non-silent decoded audio energy 0.22281; successful public IPv6 host candidate;
  VAD-complete to bot-started event 0.429 s; sampled GPU peak 9,384 MiB.
  Second offer rejected with 409; microphone sender replacement and Stop passed.
- The test uses Chromium and synthetic microphone speech on the server host.
  Public IPv6 addressing is verified, but this is not independent remote hardware,
  an IPv4-only internet path, an acoustic timing measurement or every NAT topology.
- STUN is discovery, not a relay. Restrictive NAT/firewalls may need TURN; no TURN
  credentials or relay have been configured. Quick Tunnel hostname is temporary.
- Smoke helper now releases known peer IDs even on test failure, so verification
  cannot leave its own single-session slot occupied.
- User renamed the remote default branch to `main`; local branch/upstream and
  `origin/HEAD` were updated. Connectivity commits stay on a separate feature branch
  for publication; do not push directly to `main` or create a PR without instruction.
