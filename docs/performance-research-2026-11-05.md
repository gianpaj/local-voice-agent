# Performance research: preserve the models, remove avoidable work

Research date: 2026-11-05. This is a ranked experiment backlog, **not a claim that the proposed changes have been benchmarked on this machine**. No inference runtime/model replacement is part of the observability change.

## Verdict

Keep Pipecat for now. Most expensive computation is already native: Parakeet uses C++/ggml/CUDA, Phonon uses Rust through `ptts`, and Ollama uses native inference backends. Moving the orchestration language to Rust does not automatically accelerate those kernels. Measure the gaps between stages before replacing the framework.

Rust alternatives exist: [Flowcat](https://github.com/AreevAI/flowcat) is a pre-1.0 native pipeline runtime inspired by Pipecat, and [Vox](https://github.com/mrtozner/vox) is a local Rust voice framework. Neither is a verified drop-in replacement for our exact Parakeet GGUF + Phonon + Qwen + browser pipeline. These are research options, not recommended migrations.

## What the current installation actually has

- Pipecat **1.15.0**, Python 3.12, one browser at a time; framework orchestration is Python, not model tensor inference.
- Parakeet: `transcribe.cpp` **0.4.1**, commit `5700f127e38dd53c4735ea23ef743b4b538eacd4`, existing Q8_0 artifact. The installed CMake cache confirms **Release, `-O3`, native CPU tuning, CUDA enabled and architecture 89**. `ldd` confirms cuBLAS linkage. However, **system BLAS was not found**: OpenBLAS is not linked into the host decoder. The CLI reloads the model on every utterance.
- Phonon: **ptts 0.5.0**, existing English 31M Q8_0 snapshot, resident CPU model, four threads, streamed 24 kHz PCM. The installed wheel identifies itself as Rust and targets AVX2/FMA/F16C on Linux x86-64. The Rust optimization is already present, not a future rewrite.
- Ollama: existing Qwen3.5 9B quantized model and project alias, warmup/finite keepalive, bounded 4,096-token context and 128 output tokens. A smaller quantization, model, or context is **not** a no-quality-loss optimization.

Local source/cache inspection supports these installation observations. Upstream docs describe capabilities, not our measured speed.

## Ranked next experiments

### 1. Keep Parakeet loaded; feed PCM directly

**Likely best architectural win; medium implementation effort.** The official [Python binding at our exact native revision](https://github.com/handy-computer/transcribe.cpp/blob/5700f127e38dd53c4735ea23ef743b4b538eacd4/bindings/python/README.md) supports a reusable `Model`/session and 16 kHz float PCM. This avoids repeated process/CUDA initialization, model loading, and temporary WAV/text files while retaining the same checkpoint and decode settings.

Build a matching shared library separately; do not mix arbitrary bindings with the installed ABI. Prefer one supervised persistent inference worker with bounded messages, native cancellation and restart-on-stall, preserving today's ability to kill a stuck process. The native header permits only **one compute operation per model at a time**; serializing requests is appropriate for this single-session app. More resident GPU memory is the trade-off. Measure load/mel/encode/decode timings using the native timing API, plus application queue time. A non-reporting native zero timing means unknown, not a measured zero.

### 2. Benchmark an OpenBLAS-enabled Parakeet build

**Small build experiment; no checkpoint/precision change.** Upstream [build instructions](https://github.com/handy-computer/transcribe.cpp/blob/5700f127e38dd53c4735ea23ef743b4b538eacd4/README.md) recommend BLAS for host-side work. Our CUDA encoder does not imply the CPU decoder is accelerated. Build alongside the working binary, confirm linkage, cap BLAS threads to avoid competing with Phonon/VAD, and compare identical public audio/transcripts.

Do not apply a decoder microbenchmark speedup to total transcription or end-to-end TTFA: only the time spent in that component can be saved. Floating-point reduction order can differ, so transcript parity and WER checks remain necessary even with unchanged weights.

### 3. Preserve warm models and reuse the LLM prefix

Startup warmup and keepalive are already implemented. Verify full GPU residency under simultaneous STT/LLM use rather than inferring it from checkpoint size. Keep stable system instructions and future tool schemas in a stable prefix; cache reuse should avoid recomputation, not remove information. Investigate cache hit behavior before claiming a speedup. [Ollama FAQ](https://docs.ollama.com/faq) documents residency inspection, preloading and keepalive.

Current Ollama docs say Flash Attention is selected automatically where supported; the [pinned 0.24 source](https://github.com/ollama/ollama/blob/v0.24.0/fs/ggml/ggml.go) explicitly includes `qwen35` in its default list. First inspect the actual selected backend; blindly setting a flag may change nothing. Flash Attention is intended to preserve the attention computation, but different kernels can change floating-point rounding. Quantized KV caches are a **separate lossy choice**, not a free equivalent of Flash Attention. Keep the current model and cache precision for the first comparison. The [runner cache implementation](https://github.com/ollama/ollama/blob/v0.24.0/runner/ollamarunner/cache.go) is the source for prefix reuse, rather than a promise of a cache hit on every request.

### 4. Tune Phonon scheduling, not its voice

The [installed-version Python guide](https://github.com/gradium-ai/xn-ptts/blob/v0.5.0/ptts-pyo3/README.md) recommends model reuse and streaming, which we already do. Compare one, two and four CPU threads while the full pipeline runs; more threads can increase contention. Retain voice, model, sample rate, text normalization and synthesis settings. Avoid tiny text fragments merely to improve the first chunk: they may harm prosody and cause underruns.

The [0.6.0 release notes](https://github.com/gradium-ai/xn-ptts/releases/tag/v0.6.0) include reduced Metal/CUDA synchronization, but our TTS is on CPU: those changes do not establish a CPU speedup. GPU TTS could compete with the LLM/STT. Upstream's Apple/KleidiAI and Raspberry Pi results show edge feasibility, not expected performance for this Ryzen host. See [Phonon's runtime](https://github.com/gradium-ai/xn-ptts).

### 5. Measure endpointing and sentence buffering before reducing them

Our configured VAD stop delay is 600 ms. That is a distinct wait from inference. [Pipecat's latency tuning guide](https://docs.pipecat.ai/pipecat/fundamentals/stt-latency-tuning) recommends measuring the deployed recognizer and matching turn-stop expectations to it. Lower delays can cut apparent TTFA but cut off hesitations/unfinished speech; that is a quality trade-off, not an unconditional quick win. Likewise, first-sentence buffering trades startup delay against natural TTS context.

## NVIDIA/ONNX and lower-powered targets

[NeMo's official inference documentation](https://docs.nvidia.com/nemo/speech/nightly/asr/inference.html) and [featured-model notes](https://docs.nvidia.com/nemo/speech/nightly/asr/featured_models.html) describe optimized decoding and cache-aware streaming/CUDA graphs for applicable models. These are real low-level approaches, but not switches applicable to every GGUF export. Moving to NeMo/ONNX/TensorRT requires the matching export/checkpoint, validation, compatible CUDA stack and additional deployment work; keep it behind a benchmark spike rather than replacing the working runtime on faith.

[sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/index.html) offers native local inference across desktop/embedded platforms using ONNX Runtime. `transcribe.cpp` also offers CPU/Metal/Vulkan backends. For weaker machines, first test the **same** artifacts/backend where supported, CPU Phonon and appropriate thread counts. A 6.6 GB model file does not guarantee comfortable operation on an 8 GB GPU: working buffers, cache and STT need memory too. There is no guarantee of unchanged capability *and* interactive speed on every weaker device.

## Quality gate and measurement recipe

Hold checkpoint hashes, voice, input PCM, prompt/tool schemas, context contents, decoding and sampling settings constant. Change one runtime/scheduling variable at a time. Separate startup/cold loading from steady-state results. Record p50/p95 and outcome counts for STT, first answer token, first PCM, server output, queue/aggregation waits, total turn, CPU/RAM/VRAM and audio real-time factor. Do not add stage p95s to invent end-to-end p95.

For STT, compare references/WER plus exact numbers/names and silence errors. For TTS, listen for pronunciation/prosody changes, clipping, gaps and underruns; identical text alone is not audio quality parity. For LLM, compare answer/task correctness and eventual tool-selection/argument correctness, not only tokens/second. Floating-point optimization is not a mathematical guarantee of identical generated tokens/audio.

Do not count canned filler such as “Let me check” as the substantive answer. Keep acknowledgement TTFA and result-grounded answer latency separate once tools are introduced.

## LLM-specific advanced options

The exact [Qwen3.5-9B configuration](https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/config.json) is hybrid: 24 linear-attention layers and eight full-attention layers. KV compression therefore does not halve total model memory or recurrent state. The [official model card](https://huggingface.co/Qwen/Qwen3.5-9B#deployment) includes vLLM/SGLang multi-token-prediction recipes, but those are not drop-in instructions for our existing Q4 GGUF. Roughly 18 GB of BF16 weights alone exceeds our GPU and checkpoint-size constraints.

[Correct speculative decoding](https://github.com/vllm-project/vllm/blob/main/docs/features/speculative_decoding/README.md) can preserve the target distribution, but draft verification, memory, runtime/model compatibility and short replies determine whether it helps. Consider only after the simpler changes above. [Direct llama.cpp](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) offers explicit controls, not an automatic speedup over Ollama. Check the [TensorRT-LLM model matrix](https://nvidia.github.io/TensorRT-LLM/models/supported-models.html) rather than assuming any optimized engine supports this exact hybrid model/export/tools combination.

## Tool calling: preserve correctness while measuring extra round trips

Use native structured `tool_calls` and schemas, not text/regex pretending to be tool execution. [Ollama's guide](https://docs.ollama.com/capabilities/tool-calling) covers the model-call/tool-result/follow-up loop. [Pipecat's function guide](https://docs.pipecat.ai/pipecat/learn/function-calling) covers handler integration, grouped parallel results and interruption/timeout behavior.

Two current MVP limits must be revisited **before** implementing tools:

- `messages[-11:]` may split an assistant tool-call message from its results. Prune complete conversation/tool groups while preserving needed state, not arbitrary individual messages.
- The 128-token generation cap may truncate tool arguments. Give complete tool-call output an adequate bounded budget, independently of the desired brevity of spoken answers. Reasoning is currently disabled; that is baseline behavior, not a new lossless optimization.

Keep stable, concise tool definitions/order for cache reuse. Execute independent reads concurrently with deadlines and group their results to avoid unnecessary LLM round trips. Do not arbitrarily parallelize dependent or state-changing actions; cancellation does not undo an external effect. Validate schema/allowed function names and preserve result linkage.

Extend telemetry with time to complete **valid** tool arguments, per-tool wait/execution/outcome, post-result LLM first answer token, and grounded-answer TTFA. Record tool IDs/status/duration, not raw arguments or results. Add tool-selection, argument validity and task-success checks to the speed benchmark; no tool implementation or external action was added by this research.
