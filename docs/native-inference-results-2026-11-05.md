# Same-checkpoint native STT comparison

Measured on 2026-11-05 on the current Ryzen 7600X / RTX 4060 Ti 16 GB host. This is a small controlled regression probe, not a claim about every machine or general speech accuracy.

## Conditions

- Identical existing Parakeet TDT 0.6B v3 Q8_0 checkpoint and exact public/synthetic PCM hashes; no precision, model, timestamp, normalization, language or decoding-setting change.
- Existing static CLI versus matching 0.4.1/revision `5700f127e38dd53c4735ea23ef743b4b538eacd4` shared library and binding, CUDA architecture 89, Release/native CPU tuning, four STT threads, **no BLAS in either candidate**.
- Seven fixtures: three labeled public LibriSpeech clips, public JFK sample, two synthetic English number/entity controls and digital silence. Five repeats each, alternating candidate order; 70 total attempts, 30 speech attempts per candidate. Shared references/scoring are the same as the independent Whistle comparison.
- Qwen remained fully GPU-resident, with its unchanged 4,096-token context and 8,649,499,264-byte runtime allocation before/after. An unrelated embedding model was also resident; it was not unloaded or changed. Compilation and other benchmark owners were quiescent during this window.
- CLI starts a process/loads its model per non-silent utterance. Resident startup is separated from its repeated inference. Silence uses the common adapter fast path; this is not raw decoder silence testing.

## Observed results

| Metric | Existing CLI | Resident model |
| --- | ---: | ---: |
| Attempts successful | 35/35 | 35/35 |
| Speech wall p50 | 487.6 ms | 52.3 ms |
| Speech wall p95 | 538.5 ms | 113.4 ms |
| WER / CER on successful speech | 0% / 0% | 0% / 0% |
| Number/entity probes | 100% | 100% |
| Adapter silence false positives | 0 | 0 |

All 35 paired outputs were **exactly text-equal**, as well as equal after normalization. The observed median STT wall improvement is about 9.3× on this fixture mix; this is principally removing repeated initialization/model loading, not a 9.3× faster encoder. No claim about an equal end-to-end voice-agent speedup follows from this table.

Resident worker startup: **341.6 ms**, including **243.5 ms** measured model load. Its first inference was **158.9 ms**; 29 subsequent warm speech inferences had p50 **48.7 ms** and p95 **112.6 ms**. Startup plus first inference is not free and must not be represented as warmed latency.

Native component p50s across the 30 resident speech calls: mel **1.47 ms**, encoder **31.40 ms**, decoder **18.16 ms**. These are separate marginal distributions; their p50s/p95s must not be added to synthesize an end-to-end quantile. Missing/zero native timing counters remain null where unreported.

Sampled peak device-wide VRAM was **10,299 MiB**, with both the resident worker and temporary CLI candidate occasionally present during the comparison. Peak sampled resident-worker RSS was **536,350,720 bytes**. These are approximately half-second samples, not guaranteed instantaneous peaks or process-exclusive GPU memory; CPU utilization/full-system RAM peak were not measured. There was no observed Qwen offload or inference failure.

## Native lifecycle evidence

- Five cancellations during native inference on 30 seconds of public PCM returned cancellation, retained the same model-owning worker and produced the identical reference transcript on the next request. Cancellation-plus-follow-up transcription took approximately **283–295 ms**; this combined measurement is **not pure abort latency**.
- A 1 ms request deadline triggered native abort and recovered with the same worker/identical subsequent reference transcript. Its deadline-plus-follow-up measurement was **293.6 ms**, not a 1 ms total cleanup guarantee.
- Shutdown during an active native request reaped its owned child in **66.3 ms** in the observed check. Existing CLI cancellation cleaned up in **3.3 ms**.
- Deterministic model-free actual-child tests additionally cover a TERM-resistant stuck call, forced-kill escalation, repeated cancellation during cleanup, startup-before-child-handle shutdown, bounded admission, corrupt framing bounds, crash/reload and one-attempt readiness recovery. **40 backend tests passed** at the implementation checkpoint, including the parent's safe configuration-metadata tests.
- Independent read-only review found no outstanding blocking lifecycle/ABI/protocol issues after those fixes. Actual browser TTFA, barge-in, Stop, reconnect and deployed metadata checks remain the parent's integration acceptance gates; this document does not claim they have passed yet.

## Adoption boundaries and remaining experiments

The implementation remains optional with CLI as the committed default. Parent may enable resident mode only after full-pipeline/browser verification, keeping explicit CLI rollback available. Phonon model/voice/threading, LLM checkpoint/context/sampling/reasoning and VAD settings were **not changed**.

OpenBLAS was not installed/built/adopted in this comparison: development headers were absent, and combining a kernel change with model residency would confound the primary result. A separately linked local BLAS candidate remains a later measured experiment, now guided by the approximately 18 ms median decoder time rather than an assumed whole-pipeline multiplier. No TTS-thread change, GPU-TTS migration or speculative-decoding/runtime migration was adopted without comparative evidence.

## Parent integration acceptance

The integrated optional Whistle/resident-Parakeet configuration passes **50
backend tests**, plus **10 offline analysis/report tests**, TypeScript checking
and a production Next.js build. Selecting Whistle does not load or require the
unused resident Parakeet runtime.

The exact measured shared library and matching GGML/bindings were installed in
a stable local runtime bundle, not left dependent on a disposable worktree.
The core SHA-256 remains unchanged; `/proc` mapping inspection verified all
transcribe/GGML libraries came from the stable bundle. No global linker,
OpenClaw or Ollama changes were needed.

Real Chromium through the private HTTPS origin passed **10/10 English turns**
(10 STT, 10 LLM and 20 sentence-level TTS requests, all successful), busy-client
409 handling, microphone sender replacement, Stop cleanup and server-side
disconnect/reconnect. A separate two-turn story interruption discarded the old
reply and reconnected successfully. All 12 turns had timing records; the
interrupted turn was retained as interrupted, not erased or called successful.
Telemetry shutdown flushed valid JSONL with zero dropped records/write errors.

For the ten-turn acceptance session, server-monotonic VAD-stop → first generated
PCM had median **472.3 ms**. STT request median was **84.7 ms**, LLM first-answer
median **227.0 ms**, and TTS first-PCM median **16.4 ms**. These marginal medians
must not be added. Sampled total GPU memory reached **9,398 MiB**. Browser receipt
event intervals are a different boundary and were not substituted for these
server measurements. The 600 ms configured endpointing silence, browser
playout, network and acoustic onset are excluded from generated-audio TTFA.

Decision: **enable resident Parakeet on this tested host**, keep CLI as the
portable source default and one-setting rollback, keep Whistle optional, and
keep Pipecat rather than adopting the Rustvani prototype. No model/voice,
quantization, LLM context, reasoning policy, sampling or VAD change was made.
This was a browser on the GPU host through private HTTPS; separate physical
remote-device audio/networking remains unverified.

The standalone HTML report was exercised in Chromium: component/group/search
filters, public-fixture expansion, mixed-boundary chart blocking, numeric/null
display, JSON/CSV downloads, desktop/mobile layout and zero network requests
when opened offline. The private app's `/performance` route returns that same
generated artifact; reviewed metadata is also retained in the repository.

## Retained evidence and reproduction

Use [the native runtime guide](native-inference.md) and the pinned alongside-build helper. Run `scripts/benchmark-native-stt.py` with the shared seven-fixture manifest and five repeats. Metadata-only `summary.json`/`attempts.jsonl`, checkpoint/library/PCM hashes, sampled resources and `lifecycle.json` are retained in ignored `.local/benchmarks/native-parakeet/` on the implementation worktree. They contain no dialogue, recordings or transcript text. Parent can copy sanitized report data into its requested analysis artifact; do not commit private corpus files or local model paths.
