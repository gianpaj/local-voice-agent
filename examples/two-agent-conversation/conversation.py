"""Requested synthetic two-agent recording; no production service changes.

Separate agent histories; only 24 kHz PCM is handed to the other agent.
Parakeet and Ollama inference runtimes are shared, not the dialogue state.
Playback is paced in real time into a software recording, not physical speakers.
"""
import asyncio
import audioop
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import wave

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import httpx
import numpy as np
import ptts

from local_voice.config import Config
from local_voice.resident_stt import ResidentParakeet
from local_voice.tts import pcm16

ROOT = Path(__file__).resolve().parent
RATE = 24000
TARGET_SECONDS = 120
CATALOG = {
    "Vicky": {"voice": "Freya", "voice_id": "GgfEkEJtxZR7gnpy"},
    "John": {"voice": "Easton", "voice_id": "LMDIYE0Oa377Qh6Z"},
}


def digest(pcm):
    return hashlib.sha256(pcm).hexdigest()


def synth(model, text):
    started = time.monotonic()
    first = None
    chunks = []
    with model.stream(text) as stream:
        for chunk in stream:
            if first is None:
                first = (time.monotonic() - started) * 1000
            chunks.append(pcm16(chunk))
    audio = b"".join(chunks)
    if not audio or not any(audio):
        raise RuntimeError("The selected voice produced no non-silent audio")
    return audio, {"first_pcm_ms": first, "total_ms": (time.monotonic() - started) * 1000}


async def transcribe(engine, pcm):
    # Each receiver decodes the other agent's waveform; no peer text is supplied.
    downsampled, _ = audioop.ratecv(pcm, 2, 1, RATE, 16000, None)
    return await engine.transcribe(downsampled)


@dataclass
class Agent:
    name: str
    voice: str
    model: object
    history: list = field(default_factory=list)

    def __post_init__(self):
        self.history = [{"role": "system", "content": (
            f"You are {self.name}, a friendly fictional adult meeting someone in a new city. "
            "Have an engaging, natural conversation about exploring the city, food and everyday plans. "
            "On your first turn introduce yourself by your name. Respond to what the other person says. "
            "Speak only your own words, no labels, stage directions, quotation marks or narration. "
            "Use one or two short sentences, usually 15 to 23 words. Avoid repetitive greetings after "
            "the introduction and don't ask a question in every reply. You are having a casual chat, "
            "not offering customer support. Don't invent real reservations or completed real-world actions."
        )}]

    async def respond(self, pcm, *, engine, client, config, closing=False):
        started = time.monotonic()
        if pcm is None:
            heard = None
            prompt = "Start the conversation: say hello, ask how are you, and introduce yourself as Vicky."
        else:
            heard = await transcribe(engine, pcm)
            if not heard.strip():
                raise RuntimeError(f"{self.name} could not hear the other agent")
            prompt = heard
        stt_ms = (time.monotonic() - started) * 1000 if pcm is not None else None
        self.history.append({"role": "user", "content": prompt})
        messages = list(self.history)
        if closing:
            messages.append({"role": "system", "content": "The conversation is ending now. Give a natural brief goodbye in at most 9 words. Do not ask any question."})
        llm_start = time.monotonic()
        response = await client.post(config.ollama_url + "/api/chat", json={
            "model": config.ollama_model, "stream": False, "think": False,
            "messages": messages, "keep_alive": "5m",
            "options": {"num_ctx": 4096, "num_predict": 128, "temperature": 0.7},
        })
        response.raise_for_status()
        text = response.json()["message"]["content"].strip()
        llm_ms = (time.monotonic() - llm_start) * 1000
        if not text or len(text) > 1200:
            raise RuntimeError("The local LLM returned an empty or oversized turn")
        self.history.append({"role": "assistant", "content": text})
        audio, tts = await asyncio.to_thread(synth, self.model, text)
        if len(audio) / (RATE * 2) > 25:
            raise RuntimeError("The generated utterance exceeds the audio-input budget")
        return audio, {"speaker": self.name, "voice": self.voice,
                       "heard_transcription": heard, "response_text": text,
                       "input_pcm_sha256": digest(pcm) if pcm is not None else None,
                       "output_pcm_sha256": digest(audio), "stt_ms": stt_ms,
                       "llm_total_ms": llm_ms, "tts": tts,
                       "processing_ms": (time.monotonic() - started) * 1000,
                       "audio_seconds": len(audio) / (RATE * 2), "closing": closing}


async def main():
    config = Config.from_env()
    if config.parakeet_mode != "resident" or config.stt_backend != "parakeet":
        raise RuntimeError("This recorded experiment expects the installed resident Parakeet stack")
    ROOT.mkdir(exist_ok=True, parents=True)
    ptts.set_num_threads(4)
    engine = ResidentParakeet(config)
    agents = []
    turns = []
    stereo = np.zeros((0, 2), dtype=np.float32)
    try:
        await engine.load()
        # Distinct catalog embeddings, same selected local model checkpoint.
        for name, entry in CATALOG.items():
            model = await asyncio.to_thread(ptts.TTS, str(config.phonon_model),
                                           lang="en", device="cpu", quant="q8_0", voice=entry["voice"])
            probe, _ = await asyncio.to_thread(synth, model, f"Hello, my name is {name}.")
            decoded = await transcribe(engine, probe)
            print(json.dumps({"phase": "voice_verified", "speaker": name, "voice": entry["voice"],
                              "registered_voices": model.voices, "probe_transcription": decoded}), flush=True)
            agents.append(Agent(name, entry["voice"], model))

        async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
            origin = time.monotonic()
            previous = None
            closing_count = 0
            for index in range(40):
                agent = agents[index % 2]
                closing = closing_count > 0 or time.monotonic() - origin >= TARGET_SECONDS - 10
                if closing:
                    closing_count += 1
                audio, record = await agent.respond(previous, engine=engine, client=client,
                                                    config=config, closing=closing)
                if index:
                    assert record["input_pcm_sha256"] == turns[-1]["output_pcm_sha256"]
                offset = int((time.monotonic() - origin) * RATE)
                samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768
                end = offset + len(samples)
                if len(stereo) < end:
                    stereo = np.pad(stereo, ((0, end - len(stereo)), (0, 0)))
                # Gentle stereo placement; both voices remain audible in both ears.
                gains = (0.85, 0.45) if index % 2 == 0 else (0.45, 0.85)
                stereo[offset:end, 0] += samples * gains[0]
                stereo[offset:end, 1] += samples * gains[1]
                record.update(index=index + 1, audio_offset_seconds=offset / RATE)
                turns.append(record)
                (ROOT / "turns.json").write_text(json.dumps(turns, indent=2) + "\n")
                print(json.dumps({"phase": "turn", "index": index + 1, "speaker": agent.name,
                                  "offset_seconds": round(offset / RATE, 2),
                                  "audio_seconds": round(record["audio_seconds"], 2)}), flush=True)
                await asyncio.sleep(record["audio_seconds"])
                previous = audio
                if closing_count >= 2:
                    break
                await asyncio.sleep(config.vad_stop_seconds)
            else:
                raise RuntimeError("Turn limit exceeded")

        wav = ROOT / "vicky-and-john.wav"
        with wave.open(str(wav), "wb") as output:
            output.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
            output.writeframes(pcm16(stereo))
        mp3 = ROOT / "vicky-and-john.mp3"
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
        # Independently decode the delivered encoding, not just source PCM/metadata.
        decoded_pcm = subprocess.check_output(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                                              "-i", str(mp3), "-ac", "1", "-ar", str(RATE),
                                              "-f", "s16le", "pipe:1"])
        verification = []
        for turn in turns:
            start = round(turn["audio_offset_seconds"] * RATE) * 2
            length = round(turn["audio_seconds"] * RATE) * 2
            text = await transcribe(engine, decoded_pcm[start:start + length])
            if not text.strip():
                raise RuntimeError("A recorded turn became silent or undecodable after MP3 encoding")
            verification.append({"index": turn["index"], "speaker": turn["speaker"], "transcription": text})
        summary = {"duration_seconds": len(stereo) / RATE, "turns": len(turns),
                   "voices": CATALOG, "speech_only_audio_seconds": sum(t["audio_seconds"] for t in turns),
                   "pcm_handoffs_verified": len(turns) - 1, "mp3_turns_decoded": len(verification),
                   "independent_agent_histories": True, "cross_agent_input": "24 kHz mono PCM only",
                   "shared_runtimes": ["Parakeet resident CUDA", "Ollama local-voice-qwen"],
                   "transport": "headless real-time-paced software audio loop; not browser/WebRTC or a phone call",
                   "timing_note": "Utterances are buffered before playout; this is not the streaming app latency benchmark",
                   "verification": verification}
        (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (ROOT / "transcript.txt").write_text("\n\n".join(f"{t['audio_offset_seconds']:.2f}s {t['speaker']}: {t['response_text']}" for t in turns) + "\n")
        print(json.dumps({k: v for k, v in summary.items() if k != "verification"}, indent=2), flush=True)
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
