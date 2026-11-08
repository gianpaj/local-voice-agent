# Nanbeige voice model

The installed voice app can use the locally benchmarked **Nanbeige4.3-33B
IQ2_M** checkpoint instead of Qwen. Parakeet, Phonon, the system prompt,
4,096-token context, 128-token replies, and finite model keepalive are unchanged.
This changes the LLM, not its historical Qwen benchmark results.

## Why we changed LLMs — 2026-11-08

**Decision:** switch this installed companion/voice-agent deployment from
Qwen3.5 9B Q4_K_M to Nanbeige4.3-33B IQ2_M, following the user's choice after
reviewing the matched companion/customer-support pilot. This is a reversible
product trial, not a finding that Nanbeige is universally better.

The [audited comparison and reproducible evidence](https://github.com/gianpaj/ideas/blob/e9fb273552d7e6d4fda95e2f34cc512356c26a8c/expressive-voice-models/benchmarks/companion-support-2026-11-07/COMPARISON.md)
record the signal behind that choice:

- **Companion-style conversation:** Nanbeige averaged **5.57/20**, versus
  Qwen's **4.30/20**, across the same ten EQ roleplay/debrief scenarios: a
  **+1.27-point** custom-rubric difference. Companion dialogue is closer to this
  product's purpose than the earlier coding-task evaluation.
- **Customer-support outcomes:** both passed **8/10** retail tasks. This is a
  tie in verified task outcomes, not an improvement in support success; the
  failed cases differ. All failures, including Qwen's reply-cap failure, remain
  in the denominators, and no scored cases were retried.
- **Local feasibility:** the exact cached IQ2_M artifact fit the GPU. After the
  choice, we also verified the installed Ollama integration with resident
  Parakeet, Phonon, and real browser-decoded speech. No cloud inference or
  STT/TTS replacement was needed.

**Limits of the decision:** the EQ numbers are custom rubric means, not pass
rates or official EQ-Bench leaderboard scores. Both absolute scores are low.
The pilot used ten cases, one trial, and one evaluator route
(`gpt-6.1-sol` / `xhigh` through Codex CLI, also the independent retail customer
simulator). It does not establish statistical superiority, better everyday
spoken naturalness, comprehensive policy compliance, or lower voice latency.
The benchmark used 32K context and llama.cpp/Q4_0 KV for Nanbeige; the installed
voice app uses Ollama, 4K context, and 128-token replies. Its browser smoke check
establishes integration, not comparative quality or speed.

The portable `.env.example` still defaults to Qwen and the 8 GB checkpoint
limit. Only the ignored deployment configuration selects Nanbeige and opts into
the larger LLM checkpoint. Historical Qwen timings remain labeled as Qwen;
they must not be presented as Nanbeige performance. Keep Qwen available for
rollback and leave the voice service stopped after this change, as requested.

## Benchmark comparison chart

![Qwen versus Nanbeige: EQ rubric mean 4.30 vs 5.57 out of 20 across ten scenarios each; retail outcomes tied at 8 of 10. Custom text pilots, one trial per case.](assets/qwen-nanbeige-comparison.svg)

Separate zero-based axes preserve the original scales; EQ and retail are not
averaged into an overall score. All failures remain in the denominators.
[PNG](assets/qwen-nanbeige-comparison.png) ·
[Data snapshot and source hashes](assets/qwen-nanbeige-comparison.json) ·
[Canonical data and regeneration script](https://github.com/gianpaj/ideas/blob/docs/voice-agent-evaluations-2026-11-07/expressive-voice-models/benchmarks/companion-support-2026-11-07/plot-comparison.py).

The chart reflects the recorded **benchmark runtimes**, not the installed
Nanbeige/Ollama voice configuration. This is a ten-case, one-trial custom text
pilot; voice latency was not measured and the difference is not a claim of
statistical superiority.

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
