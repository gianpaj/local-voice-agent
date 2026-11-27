# Lessons for the next local-model coding benchmark

**Status:** retrospective and proposed next-run protocol. No new inference was
performed for this document. Results are a local pilot, not official Aider or
Terminal-Bench scores, and not evidence of voice-agent quality.

## What this run actually established

We ran the same ten Aider Polyglot Python tasks through Harbor 0.25.0 and
mini-SWE-agent 2.4.6, once per configuration, on an RTX 4060 Ti 16 GB machine.
Each task had 180 seconds, 12 agent steps and an 8,192-token per-response cap;
the request timeout was 120 seconds. All servers were configured for 131,072
context tokens. Reasoning effort `medium` was requested, but the runtimes do not
necessarily give it the same meaning.

| Configuration | Saved-code verifier passes | Task timeouts | Clean passing runs | Median successful-response decode |
| --- | ---: | ---: | ---: | ---: |
| Inkling 2 Mini SQ-10, FP8 KV | 2/10 | 7/10 | 0/10 | 74.2 tok/s |
| Qwen3.8-27B UD-IQ3_XXS, Q8 K / Q4 V | 3/10 | 10/10 | 0/10 | 21.8 tok/s |
| Inkling 2 Mini SQ-15, FP8 KV | 0/10 | 9/10 | 0/10 | 36.0 tok/s |

Every passing attempt timed out; the verifier accepted the saved code afterward.
Do **not** relabel those outcomes as successful autonomous task completions.
Decode medians include reasoning and exclude failed requests. They are not task
latency, visible-answer speed or perfectly matched cross-tokenizer throughput.

All three passed a separate three-marker retrieval probe with approximately
120,000 input tokens: SQ-10 in 166 seconds, Qwen in 236 seconds and SQ-15 in 205
seconds. This was neither coding at 128K nor a test at the full context limit.

**Assessment:** SQ-10 demonstrated the best speed/headroom trade-off here.
Qwen's one additional verifier pass does not establish a quality advantage.
SQ-15 demonstrated no benefit in this setup. None demonstrated a dependable
coding agent under these limits.

## What to improve, in priority order

### 1. Fix the task environment before comparing models

Every trajectory mentioned the `.oracle` artifacts. Spot checks showed time
spent inspecting encrypted references, testing repeatedly and reasoning instead
of finishing the requested edit. This resembles the historical scaffold detour,
but it does not prove the sole cause of failure.

For the **next, separately versioned suite**:

- Use transparent, small repository tasks with a clear requested change, runnable
  public tests and separately held-out verification tests.
- Keep reference solutions, encrypted oracle files, verifier code and answer
  keys outside the agent's mounted workspace. Do not merely tell the agent not
  to inspect reachable answers.
- Verify the reference patch passes, the initial failing test really fails and
  the task has no network/resource dependency. Check the container image,
  dependency locks, commands and mounted files before inference.
- Test the agent's command/tool protocol and normal finish/submission path in an
  **unscored** canary. Confirm it can edit, run a test and submit, not just produce
  valid JSON or one tool call.
- Audit a small unscored canary set before freezing the suite. Reject broken
  tasks or scaffolds before comparing candidates, with recorded reasons.

Keep this run's evidence immutable. Fixing its environment creates a new suite;
it does not authorize recomputing the old score or dropping old failures.

### 2. Separate a usability screen from a quality test

A three-minute limit is a useful responsiveness test, but these results were
heavily censored by timeouts. It is not enough to infer coding competence.

Report distinct outcomes:

1. **Clean success:** correct patch, valid tool use and normal completion within
   budget, with all required tests passing.
2. **Correct artifact at timeout:** saved code passes, but the agent did not
   complete normally. Keep this useful result, separately labeled.
3. **Incorrect artifact / step limit / task timeout / request timeout.**
4. **Infrastructure or setup error:** distinct from capability failure, still
   retained in the attempted-outcome inventory.

Pre-register both a short usability tier and a longer quality tier. Calibrate
request deadlines on unscored prompts against the slowest admitted model:
`max generation tokens / measured generation rate + prefill + safety margin`.
An 8,192-token cap and 120-second deadline can conflict on a ~22 tok/s model.
Count hidden reasoning against the same output and wall-time budgets.

Do not increase a budget after seeing a scored failure. Apply any revised budget
to a new complete comparison and preserve the previous round.

### 3. Measure actual runtime state, not launch intentions

SQ-15 started with `--draft dspark`, but cache growth from 81,920 to 90,112 tokens
caused a logged VRAM fallback that disabled DSpark. The coding run then recorded
zero draft tokens, even though the process command still advertised drafting.
It also used 221 host-resident experts, a 12.28 GB pageable host-expert arena and
host-mapped global KV. SQ-10 kept all 3,072 experts on the GPU.

Before **and after** long-context admission, retain:

- Runtime/build, model hash, tokenizer and chat template; effective context,
  cache format, reasoning behavior, sampling and placement.
- Actual draft proposals, acceptance, draft overhead and runtime fallback events.
  A launch flag or the launcher's tiny automatic probe is not a coding A/B.
- Peak GPU allocation, free-VRAM margin, host RSS/PSS, pinned/pageable arenas,
  swap and per-process I/O. Weights alone are not the memory budget.
- Clear admission criteria for host offload, paging and minimum headroom,
  chosen before the scored run. Do not make a universal memory-fit claim from
  an idle snapshot.

Isolate cold, warm, fresh-server and sustained-session tests. Our long-input probe
changed SQ-15's later runtime state; that is a real sustained-session finding,
not a clean fresh-state quantization comparison. For a fresh-state tier, restart
all candidates consistently before scoring and verify the reset. Keep a separate
aged-session tier to expose degradation; do not silently reset away failures.

### 4. Test long-context coding, not just marker retrieval

Use the same versioned repository material at short, medium and near-target
context sizes, with space reserved for output and tool history. Record actual
rendered prompt tokens, not just a configured maximum or raw tokenizer count.

Include a task requiring an edit based on evidence in distant files, plus
follow-up tool calls and tests after the initial long prefill. Verify the patch
and retrieval of relevant details across the start, middle and end. Keep simple
needle retrieval as a diagnostic, not as the coding result.

Measure fresh prefill and valid prefix-cache reuse separately. Report time to
first visible answer/tool action, full request time, time to first correct patch
and time to passing tests, including tool overhead. Distinguish hidden-reasoning
latency from user-visible latency.

### 5. Make small-sample results less fragile

Keep task IDs, source hashes, agent/container versions, seeds and all limits
frozen. Use paired trials on the same tasks with a small predeclared seed list;
retain all attempts, rather than retrying until something passes. Balance model
and task order where practical and prevent simultaneous GPU competition.

Show per-task outcomes and uncertainty, not just a single percentage. Ten tasks
and one seed cannot establish a one-task quality win. Do not present a p95 from
a handful of calls as stable; show the sample size, range and timeout counts.

For speed, publish successful-call decode **and** complete-call latency,
throughput denominators, request failures and censored/time-limited calls.
Different tokenizers need a fixed-text or characters-per-second companion metric;
matching a token count alone does not match the work. Do not compare our subset
percentage against unrelated official benchmark versions or base-model scores.

### 6. Own downloads and recovery as carefully as inference

The SQ-15 transfer disappeared around 64%, without a retained curl exit reason;
disk space was sufficient. Execution-session interruption was a hypothesis,
not a proven root cause. The scoring worker continued waiting for verification,
so a missing dependency caused a long silent delay.

Use one persistent phase owner for download, checksum, startup, canary, scoring
and cleanup. Persist child PIDs, exit codes, byte progress, last activity,
heartbeat and stage transitions. Detect a dead download promptly instead of
waiting only for a completion file. Resume partial bytes only after verifying
no old transfer remains; verify the pinned SHA256 before loading.

Deliver requested progress every ten minutes during long work, including
completed/expected tasks, active stage, elapsed time and blockers. A live parent
process alone is not evidence of useful progress. Tie completion to an automatic
result-delivery path and stop the reporting timer afterward.

Cleanup must match the owned PID, command and checkpoint. Independently verify
ports close, GPU allocation returns and pre-existing services stay in their
original state. A controller exit code or an early cleanup snapshot is not proof;
Qwen required outer-phase cleanup after the first stop grace period.

### 7. Make evidence easy to consume and safe to retain

Retain report, compact aggregate JSON/CSV, hashes, configs, timestamps, raw local
traces, verifier output and resource samples together. Check every archive member
against a manifest and test actual extraction, not just archive creation.

Publish only sanitized synthetic evidence in this repository. Keep model
weights, raw token streams, authentication, recordings and machine-specific
paths outside Git, per repository policy. A local full archive and a portable
repository summary are complementary, not interchangeable.

Provide a standalone phone-friendly HTML report and plain-text fallback;
archive integrity on Linux does not establish macOS extraction or Telegram
Android rendering. Verify the actual delivery route. LAN hosting, when requested,
should expose only the report—not a directory of raw artifacts—and its network
reachability should be checked from the intended device.

## Proposed next run: freeze before execution

1. **Canary:** validate tool formatting, edits, public tests and normal submission
   on unscored tasks. Fix the environment first; do not tune on scored answers.
2. **Suites:** retain the old ten-task pilot as history; define a new six-task
   small-repository quality suite with isolated hidden verification, then choose
   a smaller representative long-context subset.
3. **Budgets:** candidate quality tier of ten minutes and 24 agent steps per task,
   with a 2,048-token per-call cap (reasoning included). Calibrate and freeze
   request deadlines before scoring. Keep the three-minute screen separate.
   These are proposed limits, not results or an already-started run.
4. **Repeats:** a predeclared three-seed paired comparison; no success-seeking
   retries. Freeze task/context/order policies and all effective settings.
5. **Modes:** fresh-state baseline versus a separate sustained-session test;
   only call drafting an A/B when both modes are actually tested and verified.
6. **Acceptance:** choose on correct **completed** tasks, latency and stable
   memory behavior, not raw tok/s alone. Report all failures and resource
   fallbacks before recommending a daily coding model.

Start with the retained SQ-10 as a candidate, not as a preselected quality winner.
Any additional model downloads, inference runs or deployment changes need their
own execution request. Nothing in this retrospective starts a new benchmark.

## Evidence retained with this retrospective

- [Compact audited metrics](results-summary.json)
- [Per-task outcomes without local paths](task-results.csv)
- [Pinned model artifact metadata](model-artifacts.json)
- [Source provenance and preservation hashes](PROVENANCE.json)

The original full report, raw evidence and later verified all-files archive remain
local. The historical run kept all three weights initially; subsequent authorized
cleanup retained only SQ-10 and removed SQ-15, Qwen3.8 and the earlier Strata
Qwen3.6 weights. No test was rerun for this documentation.
