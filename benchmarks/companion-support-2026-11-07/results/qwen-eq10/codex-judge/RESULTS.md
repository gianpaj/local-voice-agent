# Codex CLI rubric judging — Qwen EQ-10

**10/10 scored, no grading errors. Mean qualitative rubric: 4.30/20.**

This is a one-judge, ten-scenario pilot using the EQ-Bench 3 rubric, not an official full-suite score, calibrated Elo, or pass percentage. It does not measure audio latency.

## Judge and isolation

- `gpt-6.1-sol`, reasoning effort `xhigh`, Codex CLI 0.167.0.
- Existing Codex CLI ChatGPT login; no API key was extracted, copied or requested. Uses the account's Codex allowance; token usage is recorded, not presented as a $0 API bill.
- One fresh ephemeral session per scenario; candidate identity withheld from the prompt. No judge tool calls occurred.
- Ignore user config/rules and project instructions, disable shell/multi-agent/apps/skill search/web; read-only sandbox and strict JSON Schema.
- Upstream transcript preparation, full visible roleplay sections and debrief; no truncation. Brief final evidence assessments replace the upstream detailed-reasoning output field.

## Scoring

Average the six higher-is-better criteria within each scenario, then average the ten scenario means. Style/personality fields are retained separately and are not added to the quality mean.

| Criterion | Mean /20 |
| --- | ---: |
| demonstrated_empathy | 6.30 |
| pragmatic_ei | 3.50 |
| depth_of_insight | 4.80 |
| social_dexterity | 3.30 |
| emotional_reasoning | 3.40 |
| message_tailoring | 4.50 |

## Interpretation

The judge repeatedly found unsupported motives/details, role confusion, escalation and moralizing instead of calibrated perspective-taking. These are judge assessments tied to the retained response evidence, not new clinical judgments about users. See the individual brief assessments in [scores.csv](scores.csv).

The source prompts ask for long, introspective roleplay and a debrief, not the production companion prompt or concise spoken replies. Do not infer normal voice-call quality solely from this subset, and do not compare 4.30/20 to another judge's scale. A matched Nanbeige comparison must use these same prompts, rubric, judge model/effort and aggregation.

## Artifacts and replay

- [Judge protocol](protocol.json), [summary](summary.json), [scores/brief assessments](scores.csv).
- `scenarios/<id>/prompt.txt`: actual model-blind prompt; `answer.json`: final schema-validated scores and brief assessment; `request.json`/`result.json`: hashes, usage and elapsed time. Raw CLI event/diagnostic logs stay local.
- [Original task states](../scenarios/) preserve the exact collected inputs, including parsed visible sections and debriefs.

```sh
CODEX_HOME="$HOME/.codex" codex login status
mkdir -p runs/qwen-eq10
cp -R results/qwen-eq10/scenarios runs/qwen-eq10/scenarios
# Defaults to the normal CLI login, not an inherited OpenClaw-specific CODEX_HOME.
CODEX_JUDGE_HOME="$HOME/.codex" .venv/bin/python grade_qwen_eq.py
```

The wrapper explicitly selects the requested model/effort. It does not silently substitute a model or fall back to API-key billing. Regrading is stochastic and consumes Codex usage.

Official OpenAI references: [model and supported reasoning effort](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [non-interactive CLI and structured output](https://learn.chatgpt.com/docs/non-interactive-mode). Local `codex exec --help`, login status and the successful calls verified the installed route.

## Retail status

At this publication checkpoint: **10/10 finished, 8 passed**, status **completed**. See [retail results](../../qwen-retail10/summary.json). Unfinished cases are not failures.

Codex also supplies the independent customer simulator and semantic judge in separate fresh calls. The pinned tau2 orchestrator, policy, tools and database replay perform task evaluation; Qwen does not grade itself. This custom provider does not reproduce the default simulator/leaderboard setting.
