# Nanbeige voice model

The installed voice app can use the locally benchmarked **Nanbeige4.3-33B
IQ2_M** checkpoint instead of Qwen. Parakeet, Phonon, the system prompt,
4,096-token context, 128-token replies, and finite model keepalive are unchanged.
This changes the LLM, not its historical Qwen benchmark results.

## Setup with existing weights

Use the exact [quantizer artifact](https://huggingface.co/bartowski/Nanbeige_Nanbeige4.3-33B-GGUF):
`Nanbeige_Nanbeige4.3-33B-IQ2_M.gguf`, 12,187,167,872 bytes, SHA-256
`efb45fc475fac828741ba993ff2c4da7dd26bc33262f0b5b994164e394554930`.
No weights or populated environment files belong in Git.

1. Copy [the Modelfile](../examples/nanbeige.Modelfile) to private local storage
   and replace its `FROM` path with your existing checkpoint.
2. Import with `ollama create nanbeige4.3-33b-iq2m -f /path/to/Modelfile`.
   This follows [Ollama's local GGUF import flow](https://docs.ollama.com/import);
   it does not download or requantize weights.
3. Set these values in the ignored project `.env`:

   ```dotenv
   OLLAMA_SOURCE_MODEL=nanbeige4.3-33b-iq2m:latest
   OLLAMA_MODEL=local-voice-nanbeige
   LLM_MAX_CHECKPOINT_BYTES=16000000000
   ```

The size limit is project-local and defaults to 8 GB when unset. STT/TTS limits
are unchanged. Checkpoint bytes are not a VRAM estimate; verify actual placement
and readiness on your hardware before use. Unload only the old project alias
after stopping the old app, so the two large LLMs do not contend for GPU memory.

The example translates the checkpoint's text-only ChatML template to Ollama's
template syntax and explicitly prefills an empty thinking block for non-thinking
replies. It is for the current text-only, no-tool voice app; it does not implement
the upstream tool template. Ollama is a different runtime from the benchmark's
llama.cpp server, so the benchmark does not establish identical runtime behavior
or voice latency here.

## Start and stop

Use the existing `pnpm start` production launcher. To stop the installed user
service and immediately release just this project's LLM allocation:

```bash
systemctl --user stop local-voice-agent.service
ollama stop local-voice-nanbeige
```

Do not stop the shared Ollama daemon or unload unrelated models. Without the
explicit model stop, the app's finite keepalive expires within five minutes.
To roll back, restore the original `OLLAMA_SOURCE_MODEL`/`OLLAMA_MODEL` values
and remove the size override before restarting.

## Local verification, 2026-11-08

- Exact benchmark weight hash verified before import; Ollama 0.24.0 loaded
  53/53 layers on CUDA at 4,096 context, while resident Parakeet remained active.
- Production service reported ready and a direct OpenAI-compatible text request
  returned answer text. 60 backend tests and 10 offline report tests passed.
- Real Chromium with a synthetic microphone exercised one utterance, decoded
  nonzero reply audio, rejected a concurrent session, replaced the microphone
  sender, and passed Stop plus disconnect/reconnect checks. The first attempt's
  three-second lead-in captured no utterance; the retained retry used 15 seconds.
  This is a same-host software smoke check, not remote-device audio validation
  or a model-quality/latency benchmark.
- After testing, the voice service was stopped, ports 3000/7860 closed, and the
  project LLM unloaded. The shared Ollama daemon and unrelated models were left
  alone. The ignored deployment configuration selects Nanbeige on the next start.
