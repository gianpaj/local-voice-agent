# What this pilot does—and does not—measure

## Companion / EQ side

The test asks each model to roleplay social conflicts using three long visible
sections, followed by an out-of-character debrief. The same ten cases and all
38 upstream candidate-generation calls were selected before seeing answers.
This is not the product's normal companion prompt or short spoken response.

Qwen's full-history check passed on all 38 calls: the GGUF-vocabulary rendered
prompt count equaled Ollama's actual `prompt_eval_count`. No input truncation,
reply-cap finish, or missing collection response occurred. The alias has no
built-in system prompt; it uses Ollama's native `qwen3.5` renderer/parser. Its
raw template placeholder is not evidence that history was dropped.

Each whole scenario plus debrief is then scored by independent Codex CLI
`gpt-6.1-sol` / `xhigh` in a fresh tool-disabled session. The candidate's model
metadata is not given to that judge. All 18 rubric fields are preserved; the
quality mean uses only six higher-is-better fields, first within a scenario,
then across the ten scenarios. Brief final evidence assessments—not private
deliberation—explain the ratings.

**Qwen's result is 4.30/20 on this custom rubric subset.** It is not 21.5%
task success, official EQ-Bench Elo, a published-model leaderboard result, or
proof about normal spoken companionship. One judge, one pass and selected
scenarios do not establish statistical superiority. A matched second model is
more informative than comparing this number with another judge's scale.

## Customer-support / retail side

The fixed retail tasks use an independent Codex CLI customer who sees customer
instructions and the conversation—not the expected tool actions or target
database state. Qwen sees the supplied service policy, tools and conversation.
The pinned upstream tau2 engine evaluates database state and applicable task
criteria. Codex supplies separate semantic checks only where required.

Reward 1 means the configured outcome checks passed. It does not mean every
turn followed every policy nuance, sounded natural, or was short enough for
voice. Action checks and component rewards remain visible in the trajectories.

The deployment-oriented reply cap is 1,024 tokens. Qwen task 19 reached that
cap and stopped incomplete; it is retained as a candidate budget failure with
reward zero, **not retried**. Its partial raw request/stream evidence and the
original controller error are preserved. Only the final previously unattempted
task was run afterward. Normal completed trajectory results and this explicit
budget failure have different provenance; reports identify both.

The same failure handling applies to Nanbeige. Provider outages and harness
errors must not silently become model failures or trigger a clean rerun.

## What is matched

- Actual EQ stage sampling differs: dialogue temperature0.7/min_p0.1;
  debrief temperature0.5/min_p0.0. Qwen's raw debrief request used null min_p,
  retaining the pinned Ollama zero default. The original manifest described
  the dialogue profile, not this upstream debrief override. The sampling audit
  preserves the actual 28-dialogue/10-debrief evidence.
- Exact frozen EQ and retail IDs, prompts, policy/tools, language bindings,
  judge/rubric and aggregation; one candidate attempt per case.
- Per-stage sampling, output/context/time budgets, sequential candidate use,
  STT/embedding residency, and supported non-thinking mode.
- Codex model and effort, independent role contexts and recorded CLI version.

## What is deliberately different

These are the actual deployment artifacts: Qwen Q4_K_M and Nanbeige IQ2_M,
not equal-bit or full-precision models. Qwen uses installed Ollama; Nanbeige
uses CUDA llama.cpp b12064 with Q4_0 K/V cache to fit the 16GB card. Each needs
its own native chat template/tokenizer, so token counts and cache costs differ.
Backend/cache/quantization effects are therefore part of the measured package,
not isolated architectural effects.

The initial Nanbeige adapter incorrectly asserted that every EQ call used the
dialogue sampling tuple. This blocked debriefs **before model inference**, while
leaving complete dialogue responses intact. A guarded recovery invokes only
those previously uncalled debriefs before grading and archives the original
adapter-error states. No candidate answer is regenerated. The recorded run
therefore batches those debriefs after dialogue collection rather than
interleaving them; cache/prefill latency is not a matched comparison.

## What remains a separate experiment

Neither result measures speech recognition, speech synthesis, browser audio,
interruptions, or time to a useful spoken phrase. Those require fixed caller
audio and the full STT → LLM → TTS path. No animal-sound TTS control or training
claim follows from either LLM test.

See [Qwen grading](results/qwen-eq10/codex-judge/RESULTS.md),
[retail outcomes](results/qwen-retail10/RESULTS.md), and
[matched Nanbeige progress](results/nanbeige-iq2m/RESULTS.md).
