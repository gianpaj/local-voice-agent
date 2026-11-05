"""Configuration and real local-model smoke checks (no audio upload)."""
import argparse
import asyncio
import json
import subprocess
import time
import wave
from pathlib import Path
import httpx

from .config import Config
from .llm import prepare_ollama
from .stt import transcribe_pcm
from .tts import PhononEngine
from .resident_stt import ResidentParakeet


async def check(smoke=False, audio=None):
    config = Config.from_env()
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"], check=True, capture_output=True, text=True)
    checkpoint = config.whistle_model if config.stt_backend == "whistle" else config.parakeet_model
    result = {"configuration": "ok", "gpu": gpu.stdout.strip(), "stt_model": config.stt_model_id,
              "stt_device": config.stt_device, "stt_bytes": checkpoint.stat().st_size}
    async with httpx.AsyncClient(base_url=config.ollama_url, timeout=5, trust_env=False) as client:
        version = await client.get("/api/version")
        version.raise_for_status()
        tags = await client.get("/api/tags")
        tags.raise_for_status()
        source = next((m for m in tags.json()["models"] if m["name"] == config.ollama_source), None)
        if not source or source["size"] >= 8_000_000_000:
            raise ValueError("Configured local LLM must already exist and be smaller than 8 GB")
        result.update(ollama_version=version.json()["version"], llm_bytes=source["size"])
    if smoke:
        result.update(await prepare_ollama(config))
        engine = PhononEngine(config.phonon_model)
        stt_engine = ResidentParakeet(config) if config.parakeet_mode == "resident" else None
        try:
            if stt_engine:
                result["resident_stt"] = await stt_engine.load()
            await engine.load()
            started = time.monotonic()
            pcm = bytearray()
            first = None
            async for chunk in engine.stream("Hello. What is two plus two?"):
                if first is None:
                    first = time.monotonic() - started
                pcm.extend(chunk)
            result.update(tts_bytes=len(pcm), tts_first_chunk_seconds=round(first or 0, 3), tts_seconds=round(time.monotonic() - started, 3))
            if audio:
                with wave.open(str(audio), "rb") as wav:
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                        raise ValueError("--audio must be 16 kHz mono signed int16 WAV")
                    pcm16 = wav.readframes(wav.getnframes())
            else:
                import soxr
                import numpy as np
                floats = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
                pcm16 = (soxr.resample(floats, 24000, 16000) * 32767).astype("<i2").tobytes()
            started = time.monotonic()
            transcript = await transcribe_pcm(config, pcm16, **({"engine": stt_engine} if stt_engine else {}))
            result.update(stt_seconds=round(time.monotonic() - started, 3), stt_text=transcript)
            if not transcript:
                raise ValueError("Selected STT returned no text for the speech smoke sample")
        finally:
            await engine.close()
            if stt_engine:
                await stt_engine.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Run all models locally, reusing installed weights")
    parser.add_argument("--audio", type=Path, help="Optional public 16k mono WAV instead of synthesized test speech")
    args = parser.parse_args()
    asyncio.run(check(args.smoke, args.audio))
