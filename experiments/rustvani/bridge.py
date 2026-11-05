#!/usr/bin/env python3
"""Private, bounded JSONL IPC to the existing models; not an HTTP service.

Protocol text/audio are transient IPC, never benchmark log fields. A single
owning process reuses Phonon. This bridge deliberately preserves the baseline
CLI, checkpoint, voice, precision, sampling and reasoning settings.
"""
import asyncio
import base64
import json
import importlib.metadata
import os
import signal
import sys
import time

WIRE = sys.stdout
# Imported libraries may print startup banners. They cannot corrupt JSONL IPC.
sys.stdout = sys.stderr
MAX_LINE = 2_000_000


def emit(data):
    WIRE.write(json.dumps(data, separators=(",", ":")) + "\n")
    WIRE.flush()


async def main():
    from dotenv import load_dotenv
    from loguru import logger
    if os.environ.get("RUSTVANI_PROJECT_ENV"):
        load_dotenv(os.environ["RUSTVANI_PROJECT_ENV"], override=False)
    from local_voice.config import Config
    from local_voice.llm import SYSTEM_PROMPT
    from local_voice.stt import transcribe_pcm
    from local_voice.tts import PhononEngine
    import httpx
    logger.remove()
    config = Config.from_env()
    engine = PhononEngine(config.phonon_model)
    await engine.load()
    # CPU engine initialization and first synthesis are outside turn timings.
    async for _ in engine.stream("Ready."):
        pass
    emit({"event": "ready", "settings": {
        "stt": {"runtime": "existing-transcribe-cli", "backend": "cuda", "threads": 4,
                "checkpoint_format": "GGUF-Q8_0", "timestamps": "none", "input_rate": 16000},
        "llm": {"model": config.ollama_model, "context": 4096, "max_tokens": 128,
                "temperature": 0.5, "reasoning_effort": "none", "history": "single-turn",
                "system_prompt": "project-baseline"},
        "tts": {"runtime": "ptts", "version": importlib.metadata.version("ptts"),
                "model": "phonon-31m-en", "device": "cpu", "quantization": "q8_0",
                "threads": 4, "sample_rate": 24000, "buffering": "full_reply"}
    }})
    reader = asyncio.StreamReader(limit=MAX_LINE)
    pipe = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_running_loop().connect_read_pipe(lambda: pipe, sys.stdin)
    running = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, running.cancel)
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            while line := await reader.readline():
                if len(line) > MAX_LINE:
                    raise ValueError("IPC limit exceeded")
                request = json.loads(line)
                started = time.monotonic()
                if request["op"] == "stt":
                    pcm = base64.b64decode(request["pcm"], validate=True)
                    text = await transcribe_pcm(config, pcm)
                    if not text:
                        raise ValueError("No transcript")
                    emit({"event": "transcript", "text": text})
                elif request["op"] == "llm":
                    if not 0 < len(request["text"]) <= 16000:
                        raise ValueError("Invalid transcript length")
                    body = {"model": config.ollama_model, "stream": True, "max_tokens": 128,
                            "temperature": 0.5, "reasoning_effort": "none",
                            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                         {"role": "user", "content": request["text"]}]}
                    first = None
                    async with client.stream("POST", config.ollama_url + "/v1/chat/completions", json=body) as response:
                        response.raise_for_status()
                        async for raw in response.aiter_lines():
                            if not raw.startswith("data:"):
                                continue
                            data = raw[5:].strip()
                            if data == "[DONE]":
                                break
                            message = json.loads(data)
                            for choice in message.get("choices", []):
                                text = choice.get("delta", {}).get("content")
                                if text:
                                    if first is None:
                                        first = (time.monotonic() - started) * 1000
                                    emit({"event": "token", "text": text})
                    if first is None:
                        raise ValueError("No answer text")
                    emit({"event": "llm_first", "first_answer_token_ms": first})
                elif request["op"] == "tts":
                    if not 0 < len(request["text"]) <= 1200:
                        raise ValueError("Invalid synthesis length")
                    count, first = 0, None
                    async for pcm in engine.stream(request["text"]):
                        if first is None:
                            first = (time.monotonic() - started) * 1000
                        count += len(pcm)
                        emit({"event": "pcm", "pcm": base64.b64encode(pcm).decode("ascii")})
                    if not count:
                        raise ValueError("No audio")
                    emit({"event": "tts_metrics", "first_pcm_ms": first, "audio_duration_ms": count / 48})
                else:
                    raise ValueError("Unknown operation")
                emit({"event": "done", "duration_ms": (time.monotonic() - started) * 1000})
    finally:
        await engine.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as error:
        emit({"event": "error", "error_type": type(error).__name__})
        raise SystemExit(1)
