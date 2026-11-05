# Local model/runtime acquisition

Setup deliberately does not fetch weights. Populate ignored `.env` using portable `.env.example` placeholders.

## Parakeet

- [Handy export](https://huggingface.co/handy-computer/parakeet-tdt-0.6b-v3-gguf): `parakeet-tdt-0.6b-v3-Q8_0.gguf`, 739,508,576 bytes.
- [Matching runner](https://github.com/handy-computer/transcribe.cpp/tree/5700f127e38dd53c4735ea23ef743b4b538eacd4), pin commit `5700f127e38dd53c4735ea23ef743b4b538eacd4`, follow its CUDA build instructions. Existing CLI installations can be reused; no shared-library build.
- Tested with CUDA 12.4, GCC/G++ 13, `-DTRANSCRIBE_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89`. CUDA 12.4 rejected the host's default newer GCC. Compiler/architecture choices are machine-specific.
- Tested weight SHA-256: `5859f77944efcd8eafa23a6350731960b2b55b2203df51f319665c807d802cc7`.
- Upstream NVIDIA model terms, export notices and runner license apply separately. Do not infer model license from file format.

The adapter uses VAD-completed 16 kHz mono int16 PCM and transcript-only output files. No NeMo/ONNX substitution or Whisper dependency. This application remains English-only even though v3 supports other languages.

For an explicit smaller CPU ASR experiment, see [optional Needle/Whistle](whistle.md). This leaves Parakeet as default and does not replace the conversational Qwen model with Needle's text/tool-routing model.

## Phonon

- [gradium/phonon-31m-en](https://huggingface.co/gradium/phonon-31m-en), revision `92741c5b07d085ff840e26fea8ce43110b87852f`.
- [ptts 0.5.0](https://github.com/gradium-ai/xn-ptts/tree/e851445158f07061f063e3edff0080f9426c0da2), installed by Python lockfile; this is not Ollama Phonon.
- Set `PHONON_MODEL` to the complete selected local English Q8 snapshot, including config/tokenizer/voice data. Selected snapshot 65,051,089 bytes; Q8 weights 64,512,352 bytes.
- English 31M is Apache-2.0. Other Phonon releases can have different terms and are not substituted.

Acquire explicitly with the official `ptts --model hf://gradium/phonon-31m-en --lang en --device cpu --quant q8_0 ...` CLI or Hugging Face snapshot tools. Runtime uses a local directory, not a hub URL.

## Qwen/Ollama

Reuse installed `qwen3.5:9b` Q4_K_M, 6,594,474,711 bytes, digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`. Install/acquire deliberately if missing; the app does not pull. Check the selected upstream model's terms before redistribution.

Startup creates only alias `local-voice-qwen`, reusing blobs with 4,096 context/128 output tokens. The tested server is 0.24.0; `/v1/chat/completions` supports `reasoning_effort: none`. Normal final text alone reaches TTS, and a reply has a 30-second deadline.

Documented sizes are for tested artifacts; preflight checks actual files/local metadata against 8,000,000,000 bytes per model. KV cache, runtime tensors and RAM/VRAM can exceed download size. Pin named revisions to reproduce quality; a different same-named file is not automatically equivalent.
