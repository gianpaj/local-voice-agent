# Vicky and John: audio-only agent conversation

[conversation.py](conversation.py) is the **byte-for-byte original generator**
used for the 2026-11-06 recording, now saved in version control. The original
119.815-second run produced 17 turns with 16 matched PCM handoffs; all 17 encoded
MP3 turns were transcribed during validation. Audio and transcripts are not committed.

Source SHA-256: `f3b82a843eb701acf040e8b90e1c2f7b83ba5d0f9b3815817800f17695f5d846`.

## System prompts and context

Both agents use the same local Qwen weights, but have **separate system prompts
and separate full accumulated histories**. They are two dialogue states in one
process, not two independently loaded LLMs or a shared omniscient chat thread.

The system prompt is identical except for the agent's name:

```text
You are {name}, a friendly fictional adult meeting someone in a new city.
Have an engaging, natural conversation about exploring the city, food and everyday plans.
On your first turn introduce yourself by your name. Respond to what the other person says.
Speak only your own words, no labels, stage directions, quotation marks or narration.
Use one or two short sentences, usually 15 to 23 words. Avoid repetitive greetings after
the introduction and don't ask a question in every reply. You are having a casual chat,
not offering customer support. Don't invent real reservations or completed real-world actions.
```

Vicky receives this initial user instruction; the later dialogue is not prewritten:

```text
Start the conversation: say hello, ask how are you, and introduce yourself as Vicky.
```

Each subsequent incoming message contains only the other agent's latest **audio**:

```text
Vicky's generated text → Freya TTS → PCM → Parakeet → John's user message
John's generated text  → Easton TTS → PCM → Parakeet → Vicky's user message
```

That message is appended to the receiver's history; the **whole history**, not
only the latest message, is sent to Qwen on each request. For example, Vicky's
second LLM request contains:

```python
[
    {"role": "system", "content": vicky_system_prompt},
    {"role": "user", "content": initial_greeting_instruction},
    {"role": "assistant", "content": vicky_first_generated_reply},
    {"role": "user", "content": parakeet_transcription_of_john_audio},
]
```

John starts with his own system prompt and the transcription of Vicky's first
audio. An agent remembers its own exact generated replies, but remembers the
other speaker through STT, so recognition errors can make their views differ.
The two-minute harness does not trim the message lists; the model's configured
**4,096-token context window** still limits how much fits on much longer runs.
Generation uses 128 output tokens, temperature 0.7 and `think: false`.

Near the target duration, each final request temporarily gets this additional
system instruction (it is not appended to the permanent history):

```text
The conversation is ending now. Give a natural brief goodbye in at most 9 words. Do not ask any question.
```

## Run locally

Use the repository's existing Linux/Python 3.12 environment, matching resident
Parakeet setup, local Ollama alias and Phonon checkpoint. FFmpeg must be installed.
The ignored root `.env` must select `STT_BACKEND=parakeet` and
`PARAKEET_MODE=resident`; see [native setup](../../docs/native-inference.md).
No hosted inference is used. The runtime may download only the selected small
Freya/Easton voice embeddings from its pinned public catalog on first use.

From the repository root:

```bash
PYTHONPATH=apps/agent/src uv run --project apps/agent python examples/two-agent-conversation/conversation.py
```

The original script writes beside itself. The example's `.gitignore` keeps these
outputs local: `vicky-and-john.wav`, `vicky-and-john.mp3`, `turns.json`,
`transcript.txt` and `summary.json`. **Running again overwrites these outputs**;
copy any recording you want to keep first. The earlier delivered recording and
its original harness remain in the existing ignored experiment directory.

Check history isolation without running inference or generating audio:

```bash
PYTHONPATH=apps/agent/src uv run --project apps/agent python -m unittest discover -s examples/two-agent-conversation -p 'test_*.py'
```

## What this verifies—and what it does not

- Distinct catalog voices: Vicky uses Freya; John uses Easton. Both introductions
  are synthesized and transcribed before the conversation starts.
- Audio-only handoffs: adjacent output/input PCM SHA-256 hashes must match.
  The receiver is never given its peer's generated text directly.
- Real-time pacing: software playout waits for each utterance's duration and an
  explicitly configured 600 ms inter-turn silence. This is not a measured VAD result.
- Recording validation: the MP3 is decoded again and each turn is transcribed.
  Nonempty STT output is not proof of human-rated listening quality or zero WER.
- Reproducibility: the script, exact prompts, transcript, offsets, stage timings
  and handoff hashes are retained. Sampling and wall-clock pacing mean reruns are
  not promised to produce the identical dialogue or exact duration.
- Boundaries: this is a **headless buffered software audio loop**, not a browser,
  telephone, physical-speaker experiment or multi-call capacity benchmark. It
  shares local model runtimes and owns/reaps an extra resident STT worker. It does
  not change the production app, enable general conversation recording or create
  an external API. Synthesis utterances are buffered before playout, so its timing
  measurements must not be mixed with the streaming app's TTFA.
