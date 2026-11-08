# Matched Nanbeige companion/support pilot

Current phase: **completed**. EQ: 10/10 complete response sets; retail: 10/10 cases finished, 8 passed. Do not treat unfinished cases as failures.

## Final result

**EQ 5.57/20 (10/10 scored), retail 8/10.** Qwen: EQ 4.30/20, retail 8/10.
See the [final matched comparison](../../COMPARISON.md) for per-case scores, retained failures, branch sensitivity, coverage and independently verified voice restoration.

## Matching contract

- Same immutable ten EQ scenarios, upstream prompts, debriefs, 12,000-token output cap and 600-second call budget.
- Same ten English retail tasks, policy/tools/environment evaluation, 1,024-token reply cap, 180-second candidate-call budget, 200 steps and 1,800 seconds per task.
- Candidate output-cap/context/format failures are retained at reward zero without retry. Task 19 hit the Qwen cap; only its last unattempted task was subsequently run.
- Same actual per-stage sampling: EQ dialogue temperature0.7/min_p0.1; EQ debrief temperature0.5/min_p0.0 (Qwen sent null, retaining Ollama default zero); retail temperature0.5/min_p0.1. Shared top_p0.95/top_k20/presence0/repeat1/seed42. Both candidates use supported non-thinking mode.
- Same independent Codex CLI `gpt-6.1-sol` / `xhigh` rubric judge, customer simulator and applicable semantic checks; fresh isolated calls and unchanged aggregate criteria.
- Expected actions are not given to the customer; the benchmark engine evaluates database state and applicable criteria.

## Deliberate differences and interpretation

Qwen uses its installed Ollama Q4_K_M artifact. Nanbeige uses the cached bartowski IQ2_M artifact in CUDA llama.cpp b12064, all layers offloaded, 32K context and Q4_0 K/V cache to fit the 16GB GPU with STT/embeddings resident. Each uses its native chat template/tokenizer. These are actual deployable configurations, not an isolated comparison of unquantized architectures or equal cache precision.

Nanbeige’s official card supports disabling thinking; this pilot does so to match the low-latency Qwen profile. It does not reproduce the publisher's highest-thinking benchmark setup or recommended unrestricted token budgets. See [official model controls](https://huggingface.co/Nanbeige/Nanbeige4.3-33B#3-quickstart).

EQ measures judged quality of long social roleplays and debriefs. Retail measures fixed-task outcomes; it does not establish perfect policy compliance or natural spoken style. Neither measures STT/TTS/audio latency. A single custom judge/subset is not an official leaderboard score.

## Execution and recovery

The controller waits for the Qwen baseline, verifies its recorded outcomes and voice recovery, verifies the cached weight hash, freezes voice processing, unloads Qwen, starts loopback-only Nanbeige, checks CUDA placement/text/tool calls, then performs EQ → Codex grading → retail. It stops only its own Nanbeige server and reloads/thaws the ordinary Qwen voice profile in `finally`.

Pre-inference process-wait compatibility and startup logging/placement-observability failures were preserved locally. They were fixed before smoke or scored inference, without retrying a scored case. Runtime/model failures and unscored EQ coverage remain explicit in the artifacts.

An initial adapter assertion incorrectly allowed only the dialogue sampling tuple and blocked debriefs before any debrief inference. Completed dialogue responses are preserved. The guarded recovery runs only those previously uncalled debriefs with the correct upstream temperature0.5/min_p0.0, before grading; original error states are archived. This batches missing debriefs after dialogue collection instead of interleaving them, so cache/prefill timing is not matched. It does not regenerate or replace candidate answers. See `eq10/adapter-debrief-recovery.json` and recovery progress for the recorded repair.

## Replay

From the benchmark directory, first reproduce the Qwen baseline and its Codex grading as documented. Supply the existing exact GGUF and matching CUDA runtime; weights are not committed or downloaded by this controller.

```sh
export LLAMA_RUNTIME=/path/to/llama-b12064
export LLAMA_CUDA_LIB=/path/to/matching/cuda-libraries
export NANBEIGE_GGUF=/path/to/Nanbeige_Nanbeige4.3-33B-IQ2_M.gguf
CODEX_JUDGE_HOME="$HOME/.codex" .venv/bin/python -u run_nanbeige_companion.py
```

The wrapper consumes existing Codex usage, not a new API-key route. Do not launch a duplicate controller or overwrite existing run evidence. Final independent voice recovery must be verified separately from an earlier pilot checkpoint. Raw model streams, CLI event diagnostics and machine-specific startup commands remain local.
