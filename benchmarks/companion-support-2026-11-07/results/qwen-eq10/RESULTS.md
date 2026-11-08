# Qwen EQ pilot: generation results

This is the **19:30 UTC collection audit checkpoint**. Subsequent independent
Codex CLI grading is complete: **4.30/20 on the custom ten-scenario rubric**.
See the [grading report](codex-judge/RESULTS.md) and [current progress](progress.json)
for the follow-up retail stage. The collection metrics below remain unchanged.

Response sets complete: **10/10**; API calls: 38/38; collection errors: 0.
At collection time, EQ quality was unassigned and retail had not run; the
independent customer simulator/judge route was not yet selected.
These are not ten passed EQ tasks and not an official leaderboard result.

Generated tokens: 53743; median server decode: 43.287 tokens/s.
First completion-content token: p50 1.046 s; p95 1.724 s.
That content includes the required introspective sections. It is not the first useful spoken
reply or end-to-end voice latency. Percentiles are descriptive for this small selected sample.
Median full-call duration: 34.165 s; maximum observed input: 7238 tokens.
Peak aggregate GPU allocation: 10655 MiB; minimum observed host available RAM: 8.282 GiB.

## Per-scenario collection

| ID | Scenario | Status | Sections | Debrief | Quality |
| --- | --- | --- | --- | --- | --- |
| 1 | Lunchroom Theft Scapegoat | completed | True | True | Pending |
| 2 | Flirtation Accusation | completed | True | True | Pending |
| 3 | Violent Boss Fantasies | completed | True | True | Pending |
| 6 | Teen Dishes Showdown | completed | True | True | Pending |
| 9 | Wedding Dress Misstep | completed | True | True | Pending |
| 13 | Laundry Program Clash | completed | True | True | Pending |
| 131 | Teen Privacy Battle | completed | True | True | Pending |
| 133 | Theater Passion Dispute | completed | True | True | Pending |
| 137 | Shared Room Conflict | completed | True | True | Pending |
| 302 | Texting Ex Boundaries | completed | True | True | Pending |

## Interpretation

The model produced complete structured transcripts on the selected cases. Format/content presence
checks are not semantic empathy, helpfulness or policy-compliance grading. No comparison with
Nanbeige is available yet, and no inference about TTS event/animal-sound capability follows.

The upstream prompts and debriefs were preserved. The model used a separate 32K benchmark
profile instead of the voice application’s 4K/128-token preset. No silent prompt truncation,
candidate failure retries, spoken-audio measurements or paid evaluation requests were used.

Original Qwen voice profile restoration is recorded in restore.json and independently checked
after collection (post-run-verification.json). Full raw streams remain local; reproducible fixtures, transcripts and metrics
are suitable for the public learning record with the upstream license notices.

## Run timeline and recovery

Collection controller: 2026-11-07T19:07:48.514796+00:00 → 2026-11-07T19:29:56.333832+00:00.

Independent restoration check: 2026-11-07T19:30:11.284446+00:00; service active/running and thawed, HTTP ready/idle, original Qwen context 4096. No fresh end-to-end voice call was made in this check.

The user subsequently selected Codex CLI `gpt-6.1-sol` / `xhigh` through the
existing ChatGPT login. The [follow-up grading report](codex-judge/RESULTS.md)
records the custom judge protocol and retail checkpoint. No API-key billing
route was used; Codex usage is recorded separately from local Qwen inference.
