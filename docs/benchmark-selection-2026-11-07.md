# Evaluating a local companion / customer-support voice agent

Research date: 2026-11-07. This is a recommendation and source/code review,
not an executed benchmark or verified integration.

## Decision

Compare the actual deployment artifacts: **Qwen3.5 9B Q4_K_M** versus
**Nanbeige4.3-33B IQ2_M**, not their full-precision leaderboard entries.

Use three separate scorecards:

1. **Companion conversation:** EQ-Bench 3, followed by a product-specific spoken-dialogue check.
2. **Support outcomes:** a pinned τ-bench retail/telecom subset with tools and policies.
3. **Real voice performance:** identical recorded callers through the existing
   Parakeet → candidate LLM → Phonon stack; later adopt a full-duplex harness.

A coding score does not answer whether an agent is warm, concise, accurate,
good at clarifying a request, or fast enough to talk to. A single aggregate
score would conceal the quality/latency trade-off we need to measure.

## 1. EQ-Bench 3 — best first companion-oriented test

The published methodology describes 45 challenging scenarios, generally
prewritten multi-turn interactions. It probes emotional/social reasoning,
empathy and conflict handling. The scripted user turns avoid loading another
LLM as an interactive customer. Outputs are scored with a rubric and/or
pairwise model judging. Sources: [methodology](https://eqbench.com/about.html),
[repository](https://github.com/EQ-bench/eqbench3).

The implementation has separate test/judge URLs. Response collection can run
against a local chat-completions endpoint with judging disabled, followed by
an independent judge or blinded human A/B review. Human preference results
are useful product evidence, but are not official EQ-Bench Elo results.
[API client](https://github.com/EQ-bench/eqbench3/blob/affc5a000bbdb965b807e09ca7875f8530fa8f56/utils/api.py),
[CLI](https://github.com/EQ-bench/eqbench3/blob/affc5a000bbdb965b807e09ca7875f8530fa8f56/eqbench3.py).

Important limitation: roleplay includes introspection/debriefs. It is not a
voice-naturalness, long-term memory, or complete companion-product test.
Do not send those introspection sections to TTS or reward verbose essays as
good spoken replies. Preserve official prompts for benchmark runs; evaluate
our short conversational persona separately.

## 2. τ-bench — best customer-support outcome test

This benchmark provides company policies, simulated users, API tools and
task outcome checks across retail, airline and telecom. Retail covers support
transactions; telecom requires coordinating troubleshooting with the caller.
It evaluates completing the job rather than merely sounding helpful.
[Upstream](https://github.com/sierra-research/tau2-bench).

The current source uses LiteLLM with caller-supplied model arguments; a local
OpenAI-compatible endpoint is an integration route for the text agent.
There is still a separate user-simulator model. Current 2.0 defaults also use
semantic communication and NL-assertion judges, so this is **not automatically
free/offline** just because the candidate is local. Configure and retain the
same simulator/judges for both candidates. Do not use each candidate to judge
itself, or swap the customer model along with the candidate.
[LLM implementation](https://github.com/sierra-research/tau2-bench/blob/033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f/src/tau2/utils/llm_utils.py),
[running/scoring defaults](https://github.com/sierra-research/tau2-bench/blob/033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f/docs/running-the-benchmark.md).

Use a hosted simulator/judge only with an agreed provider/cost limit, or a
fixed independent CPU/other-machine model with quality checked first. The
latter changes the evaluation configuration and is not leaderboard-equivalent.
Replaying an identical static customer transcript is useful for response
comparison but does not reproduce interactive τ-bench task success.

### Artificial Analysis and versioning

Artificial Analysis lists **τ²-Bench Telecom**, but marks it legacy. Its
published implementation uses 114 tasks, three repeats and a particular
separate customer simulator. AA's newer speech methodology includes
**τ-Voice** alongside conversational dynamics and human preference.
[AA telecom](https://artificialanalysis.ai/evaluations/tau2-bench),
[AA implementation](https://artificialanalysis.ai/methodology/intelligence-benchmarking),
[AA speech methodology](https://artificialanalysis.ai/methodology/speech-to-speech-benchmarking).

Upstream τ-bench 2.0.0 was released in October 2026. Its task/prompt/judging
changes mean 2.0 is not directly comparable with 1.x. Pin the release and task
IDs before comparing our candidates; report a small subset as a local pilot,
not an AA or official full-suite score.
[Release notes](https://github.com/sierra-research/tau2-bench/releases/tag/v2.0.0).

## 3. Voice harnesses — evaluate the product, not just the text model

**τ-Voice** tests grounded support tasks with spoken interactions, including
full-duplex dynamics. Its public code is in τ-bench. Adapting our cascaded
pipeline is additional integration work, not a model-name switch. The current
Pipecat adapter targets a specific hosted protocol with tool registration,
audio transport and readiness contracts; using Pipecat alone does not prove
compatibility. [Paper](https://arxiv.org/abs/2603.13686),
[Pipecat adapter contract](https://github.com/sierra-research/tau2-bench/blob/033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f/src/tau2/voice/audio_native/avartha_pipecat/README.md).

**NVIDIA NeMo Labs Voice Agent** offers an alternative two-bot harness with
10 small customer-service scenarios, 11 restaurant scenarios, and ported
τ-bench domains. These small sets can be integration smoke tests, not general
capability estimates. [Scenario catalog](https://docs.nvidia.com/nemo/labs-voice-agent/evaluate-voice-agents/understand-scoring/benchmarks-domains/).

Its documented external-agent path requires Pipecat WebSocket/protobuf/RTVI
transport, reset/tool/state handlers and speaking/usage metrics. Our existing
browser WebRTC endpoint has not been checked against that complete contract.
The harness does not require an NVIDIA-hosted LLM simply because NVIDIA
publishes it. [External-agent integration](https://docs.nvidia.com/nemo/labs-voice-agent/evaluate-voice-agents/run-evaluations/evaluating-an-external-agent).

## Secondary options

- **VoiceBench:** public recorded spoken instructions, text/audio comparisons,
  instruction following and QA. Its human speech subsets are useful for
  exposing transcription-induced answer failures. Open-ended subsets use a
  model judge; it does not replace support outcome or companion evaluations.
  [Code/data instructions](https://github.com/MatthewCYM/VoiceBench).
- **BFCL:** a narrower diagnostic for function selection, missing parameters
  and multi-turn calls. Some categories have local/static checking; REST
  categories need third-party API setup. Nanbeige's llama.cpp endpoint needs
  a suitable handler rather than loading its GGUF in the default vLLM path.
  [Repository](https://github.com/gorilla-llm/gorilla/tree/main/berkeley-function-call-leaderboard).
- **LongMemEval / V2:** useful later for remembering preferences and updating
  facts across sessions. Evaluate the memory/retrieval layer together with
  the model; do not cram long histories into a 4K window and call the result
  a fair memory test. [Original](https://github.com/xiaowu0162/LongMemEval),
  [V2](https://github.com/xiaowu0162/LongMemEval-V2).

## Recommended initial comparison — proposed, not launched

### A. Short text pilot

- Preselect **10 EQ-Bench scenarios**, spanning emotional support, disagreement,
  clarification and social nuance; retain the selected IDs and unchanged text.
  The reviewed native CLI has no active scenario-selection flag: a subset
  needs an explicit small runner/fixture copy and must be labeled a custom pilot.
- Preselect **10 retail support tasks** with a range of difficulties. Add
  telecom after the tool pipeline works. Use one fixed independent simulator.
- Make one attempt per case initially to diagnose integration; do not select
  prompts based on which model wins. Expand to the full EQ set and larger
  support coverage, with three trials, before making a model decision.
- Keep official benchmark prompts distinct from the product's companion/support
  system prompts. Custom persona/short-answer runs are separate product evaluations.

### B. Product voice A/B

Use **20 fixed recorded caller scenarios per model**, three repetitions
where practical: companion chat, frustrated customer, booking/correction,
spelled names, dates/numbers, ambiguous requests, natural pauses and interruptions.
These are proposed custom cases, not existing benchmark scores or confidence
that 20 cases represent every user population.

Keep audio, STT, TTS voice, tools/data, audio chunking and turn-detection settings
fixed. Start with clean recordings, then separately add noise/latency variants.
Compare transcripts as well as independently checking what was actually audible.

### C. Scorecards and measurement boundaries

Report dimensions separately, including raw denominators and all failures:

- Support: completed task/world state, policy violations, correct tool
  arguments, unnecessary tool calls, appropriate clarification/escalation.
- Companion: blinded A/B preference, warmth, listening/context consistency,
  non-repetitiveness, appropriate disagreement and spoken brevity.
- Timing: cold/warm first **answer** token, first meaningful speakable phrase,
  p50/p95 caller-end-of-speech → first audible reply, full response duration,
  cancellation/interrupt stop time. Hidden reasoning tokens are not audible
  responsiveness; count their elapsed time rather than treating them as an answer.
- Reliability: timeouts, context overflow, invalid tool calls, repetitions,
  empty replies, peak total/per-process VRAM and host RAM.

Use a monotonic clock on the recorder/receiver for end-to-end audio timings.
Do not subtract unsynchronized timestamps from different hosts. Server-side
first TTS chunk is not the same as first audio heard by a user.

Predeclare separate quality and latency budgets: give quality tasks enough
time to finish, while also reporting the fraction that meet a practical voice
response target. Do not recycle the coding run's arbitrary 180-second limit
or hide slow/failed turns by excluding them from latency summaries.

### D. Hardware and fairness

Read-only live inspection found RTX 4060 Ti 16 GB, the original Qwen alias
running at 4096 context, STT GPU-resident and the voice service ready/idle.
No service was stopped for this research. The earlier Nanbeige fit was
measured with voice processing paused, not during active concurrent calls.

Run **one candidate at a time**, with identical STT/TTS/embedding residency
and concurrency=1. The simulator/judge must not unexpectedly occupy the same
GPU. Generate responses first and judge afterward where possible.

Preflight each complete prompt/tool schema with its own tokenizer, including
reserved output/history. Choose a declared context capacity that fits both;
keep a separate production-4K track if required. Never silently truncate a
policy or omit a selected case because it exceeds context. Log the actual
model hash, quantization, runtime, chat template, reasoning mode, sampling,
cache precision and context settings for every run. Use each supported model
format correctly; forced unsupported no-thinking modes are not a fair shortcut.

## Reviewed entry points — templates only

EQ-Bench supports a no-judge collection run. After installing its dependencies
in a new environment and serving a candidate locally, this is the reviewed
shape for the full suite (not the proposed 10-case subset):

```sh
TEST_API_URL=http://127.0.0.1:11434/v1/chat/completions \
TEST_API_KEY=local-only python eqbench3.py \
  --test-model local-voice-qwen:latest --model-name qwen35-9b-q4km \
  --threads 1 --iterations 1 --ignore-canonical --no-elo --no-rubric \
  --runs-file qwen-eqbench3-responses.json
```

For Nanbeige, serve the already-downloaded GGUF separately and use its endpoint
and model alias. Validate response parsing, generation limits and min_p handling
on two scenarios before batch execution. The command above does not produce
an EQ score until independently judged and has not been executed here.

τ-bench text exposes `--agent-llm`, `--agent-llm-args`, `--user-llm`,
`--user-llm-args`, `--task-ids`, `--num-trials`, `--max-concurrency` and
`--dry-run`. Pass the local endpoint via LiteLLM's `api_base`; explicitly
configure simulator and scoring dependencies rather than accepting paid
defaults accidentally. [CLI reference](https://github.com/sierra-research/tau2-bench/blob/033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f/docs/cli-reference.md).

## Source revision pins inspected

- τ-bench main: `033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f`.
- EQ-Bench 3 main: `affc5a000bbdb965b807e09ca7875f8530fa8f56`.
- Gorilla/BFCL main: `916260dfc116bf06793a1af79b4ec8195b0453b6`.
- NeMo docs inspected are Nightly; pin an actual implementation revision
  before integration rather than assuming the docs stay unchanged.

No evaluation calls, model downloads, package installs, paid API usage,
production changes or new public endpoints were performed for this research.
