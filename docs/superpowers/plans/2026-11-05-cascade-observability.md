# Persistent cascade observability

User-authorized bounded extension of the English-only local voice agent. Reuse Pipecat 1.15 tracing and service metrics, with a local exporter instead of a cloud collector.

## Deliverables

1. Add schema-versioned metadata-only local JSONL, a bounded background writer with rotation, health counters, flush/close, and a content-allowlisted OpenTelemetry exporter.
2. Correlate session, turn, and stage request IDs. Instrument Parakeet and Phonon adapters and enable Pipecat tracing/usage metrics for Ollama. Record failures, cancellations, empty results, and missing measurements explicitly.
3. Keep request measurements separate from per-turn latency. Define generated first PCM, server speaking event, browser playback, and acoustic onset as different boundaries. Do not add overlapping stage durations or subtract clocks on different devices.
4. Provide offline distributions and CSV/JSON export without loading models or starting a collector. Break down outcomes so failures do not disappear from averages.
5. Test privacy, queue/rotation/shutdown, missing values and correlation, then verify persisted records against real synthetic multi-turn browser audio and interruption. Retain evidence locally, not in Git.
6. Integrate to the canonical checkout, restart the private deployment, push the feature branch, and verify the remote SHA. Do not change existing Tailscale routes, global inference settings, model weights, or repository visibility.

## Boundaries

No new dashboard/database/cloud service, no transcript/audio retention. Browser-decoded/acoustic TTFA remains unmeasured rather than relabeled from a server event. Warmup and conversational inference are distinct. Each TTS segment is one request, not an independent conversational turn.

Implementation ownership: Codex worker owns backend and dependency lock; parent owns analysis, documentation, independent review, deployment and remote verification.
