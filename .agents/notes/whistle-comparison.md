# Whistle optional STT comparison

## 2026-11-05 — scope and installed artifacts

- Parent-prepared branch `feat/whistle-stt-comparison`, starting clean HEAD `d28fcd89e749e50df96276cd56898644ffe06ef5` verified before edits.
- User requested trying Needle/Whistle alongside the native-inference optimization. This is a **model comparison**, not a promised quality-preserving optimization. Keep Parakeet default and leave Qwen/Phonon unchanged.
- Reused installed native Needle binary and Whistle export; no package/model downloads or OpenClaw configuration changes. Installed provenance labels `cactus-needle` 3.2.1 and native engine 3.3.1; CLI help is the actual contract, not today's Python management CLI.
- Whistle export: 16,919,407 bytes; SHA-256 `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb`. Native executable SHA-256 `77cd35ee75c53df8532b397e11ed7cf94a45d89a9f14f50881fd9c68ba54a2d0` (recorded installed provenance; confirm before inference).
- Current [upstream README](https://github.com/cactus-compute/needle#whistle) and [Whistle model card](https://huggingface.co/Cactus-Compute/whistle) document native `needle --model whistle.cact --audio clip.wav`, explicit English language and thread flags. No `--audio-depth` flag is supplied: default full decoder, no shallower quality/speed tradeoff. ASR only; Needle text/tool model is not loaded.
- Native engine is C++ CPU inference. Existing Python package source confirms `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1`; native-card documentation says native behavior is compiled defaults/explicit flags. Adapter sets those plus Hugging Face offline/no-telemetry environment and uses local files only.
- Preserve the shared bounded Pipecat VAD/turn-ownership path, selected STT identity/device in retained metadata, process-group cancellation/deadline/reap and private temporary audio cleanup. No shell interpolation or transcripts in metrics.

## Execution plan

1. Add optional backend/configuration seam and deterministic adapter/fault tests; logical implementation commit.
2. Parent grants an exclusive inference window; compare both runners on the **same public/synthetic English PCM**, including names/numbers/silence. Keep audio/references/raw transcripts in ignored `.local/` artifacts.
3. Retain corpus hashes, paired process-wall latency, failures, reference WER/CER and exact numbers/names results; separate synthetic checks from natural-speech evidence. No Whisper execution and no unsupported parity claim.
4. Document measured decision and rollback; leave Whistle opt-in unless parent explicitly selects it after reviewing evidence.

## Coordination

Reading, implementation and fake tests are allowed now. No heavy inference/build until the parent grants the shared CPU/GPU benchmark window. Parent owns canonical deployment/service/routes, final integration and pushes.
