#!/usr/bin/env python3
"""Explicit public-fixture acquisition; never invoked by app setup/startup.

Small English LibriSpeech subset plus public JFK audio, digital silence, and
optional synthetic Phonon number/name controls. Artifacts are gitignored.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import wave

DATASET = "hf-internal-testing/librispeech_asr_dummy"
DATASET_REVISION = "5be91486e11a2d616f4ec5db8d3fd248585ac07a"
JFK = "https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav"
SYNTHETIC = [
    ("synthetic-names", "Please ask Michael and Sarah to visit London on Friday.",
     [["Michael"], ["Sarah"], ["London"], ["Friday"]]),
    ("synthetic-numbers", "The booking is for twenty three cars on June fifth at nine thirty.",
     [["twenty three", "23"], ["June fifth", "June 5th", "June 5"], ["nine thirty", "9 30"]]),
]


def write_wav(path, pcm):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)


def acquire(url, target):
    # Exact URLs originate from official fixture publisher/API responses, not
    # from conversation/audio input. No remote source is sent to ffmpeg.
    raw = target.with_suffix(".download")
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read(20_000_001)
        if len(content) > 20_000_000:
            raise ValueError("Public fixture exceeded the 20 MB acquisition bound")
        raw.write_bytes(content)
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-protocol_whitelist", "file,pipe", "-i", str(raw), "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", str(target)], check=True, timeout=30)
    finally:
        raw.unlink(missing_ok=True)


async def prepare(args):
    directory = args.output
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cases = []
    rows_url = (f"https://datasets-server.huggingface.co/rows?dataset={DATASET}"
                f"&revision={DATASET_REVISION}&config=clean&split=validation&offset=0&length=3")
    with urllib.request.urlopen(rows_url, timeout=30) as response:
        rows = json.load(response)["rows"]
    for wrapped in rows:
        row = wrapped["row"]
        filename = f"librispeech-{row['id']}.wav"
        acquire(row["audio"][0]["src"], directory / filename)
        cases.append({"id": f"librispeech-{row['id']}", "audio": filename,
                      "reference": row["text"], "source": f"https://huggingface.co/datasets/{DATASET}/tree/{DATASET_REVISION}",
                      "kind": "public-natural-speech", "probes": [["Quilter"]] if wrapped["row_idx"] < 2 else []})
    acquire(JFK, directory / "jfk.wav")
    cases.append({"id": "jfk", "audio": "jfk.wav", "reference":
                  "And so my fellow Americans ask not what your country can do for you ask what you can do for your country",
                  "kind": "public-natural-speech", "source": JFK})
    write_wav(directory / "silence.wav", bytes(16000 * 2 * 2))
    cases.append({"id": "silence", "audio": "silence.wav", "reference": "",
                  "kind": "digital-silence", "source": "generated digital silence"})
    if args.add_synthetic:
        from local_voice.config import Config
        from local_voice.tts import PhononEngine
        import numpy as np
        import soxr
        config = Config.from_env()
        engine = PhononEngine(config.phonon_model)
        try:
            await engine.load()
            for identifier, text, probes in SYNTHETIC:
                chunks = bytearray()
                async for chunk in engine.stream(text):
                    chunks.extend(chunk)
                samples = np.frombuffer(chunks, dtype="<i2").astype(np.float32) / 32768
                pcm = (soxr.resample(samples, 24000, 16000) * 32767).astype("<i2").tobytes()
                write_wav(directory / f"{identifier}.wav", pcm)
                cases.append({"id": identifier, "audio": f"{identifier}.wav", "reference": text,
                              "kind": "synthetic-phonon-31m-english", "source": "local Phonon 31M English Q8_0",
                              "probes": probes})
        finally:
            await engine.close()
    for case in cases:
        case["wav_sha256"] = hashlib.sha256((directory / case["audio"]).read_bytes()).hexdigest()
    manifest = directory / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(case) + "\n" for case in cases))
    print(json.dumps({"cases": len(cases), "manifest": str(manifest)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/corpus/stt-comparison"))
    parser.add_argument("--add-synthetic", action="store_true", help="Run local Phonon to add number/name controls")
    asyncio.run(prepare(parser.parse_args()))
