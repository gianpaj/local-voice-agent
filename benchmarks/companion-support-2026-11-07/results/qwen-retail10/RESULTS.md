# Qwen retail-10 pilot

**10/10 tasks finished; 8 passed. Status: completed.**

This is a frozen English task subset using an independently simulated customer, not an official full-suite score. Unfinished tasks are not failures; no model failure is silently retried.

## Outcomes

| Task | Reward | Termination | Messages |
| --- | ---: | --- | ---: |
| 11 | 1.0 | user_stop | 24 |
| 13 | 1.0 | user_stop | 28 |
| 14 | 0.0 | user_stop | 24 |
| 16 | 1.0 | user_stop | 28 |
| 17 | 1.0 | user_stop | 16 |
| 18 | 1.0 | user_stop | 26 |
| 19 | 0.0 | candidate_output_cap | None |
| 21 | 1.0 | user_stop | 34 |
| 6 | 1.0 | user_stop | 28 |
| 8 | 1.0 | user_stop | 24 |

## Method and limits

Qwen3.5 9B Q4_K_M runs on local Ollama at 32K context, thinking off, temperature 0.5, 1,024 output tokens/call. The pinned upstream policy, tools, English language binding, orchestrator and outcome evaluators are unchanged. See [protocol.json](protocol.json) for the complete run configuration.

The independent customer uses Codex CLI `gpt-6.1-sol` / `xhigh` through the existing ChatGPT login, one fresh structured session per turn with no actual tools. Semantic judges are configured through the same route in separate contexts, and only called if the task criteria require them. The simulator sees customer instructions and conversation history, not expected actions or the target database state.

Database replay and applicable communication/assertion checks determine the reward. State success does not establish that every spoken turn was concise, polite or policy-perfect; inspect the transcripts and component rewards. The retail slice does not measure speech recognition, synthesis or end-to-end audio latency.

Candidate generation calls attempted: 116; completed timing records: 115. The capped call remains in raw streams but has no completed-call timing row; timing medians describe the completed records only, not all attempts. Peak aggregate GPU: 10655 MiB. Monitoring includes archived pilot and resumed runs.

Full completed trajectories are `<task-id>.json`. Per-task output includes component rewards, actions, messages, seed and language provenance. `recovery-checkpoints/` preserves the integration pilot; the final `restore.json` must be checked against independent service verification after the complete run. Raw per-token and CLI diagnostic logs remain local.

## Replay

From the benchmark directory, follow its [setup and runner instructions](../../README.md). First run `run_qwen_retail.py --preflight`, then run it without that flag. `--resume` retains already recorded task outcomes rather than restarting them.
