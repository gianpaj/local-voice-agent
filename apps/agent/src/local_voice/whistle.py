"""Optional CPU Whistle ASR through its matching, already-installed Needle CLI.

Only the native speech runner is invoked. No downloads, Python management CLI,
Needle text/tool model, hosted fallback or audio-depth/quality reduction.
"""
import asyncio
import json
import math
import os
from pathlib import Path
import tempfile
import time
import wave


def offline_env():
    return {**os.environ, "NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"}


async def transcribe_whistle(config, pcm, *, timings=None):
    # Keep this adapter independent of Pipecat's VAD/turn ownership. The shared
    # caller validates and bounds PCM before selecting an inference backend.
    from .stt import terminate_process

    with tempfile.TemporaryDirectory(prefix="local-voice-whistle-") as directory:
        wav_path = Path(directory) / "utterance.wav"
        output_path = Path(directory) / "result.json"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        started = time.monotonic()
        # File-backed output avoids an unbounded subprocess PIPE buffer. Limit
        # parsed response size too; native stderr may contain input text/paths.
        with output_path.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                str(config.needle_bin), "--model", str(config.whistle_model),
                "--audio", str(wav_path), "--audio-language", "en",
                "--threads", str(config.stt_threads),
                stdin=asyncio.subprocess.DEVNULL, stdout=output,
                stderr=asyncio.subprocess.DEVNULL,
                env=offline_env(), start_new_session=True,
            )
            try:
                async with asyncio.timeout(config.stt_timeout):
                    await process.wait()
                if process.returncode != 0:
                    raise RuntimeError(f"Local Whistle transcription failed (exit {process.returncode})")
                if output_path.stat().st_size > 256_000:
                    raise RuntimeError("Whistle returned an oversized response")
                try:
                    result = json.loads(output_path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError):
                    raise RuntimeError("Whistle returned malformed JSON") from None
                if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                    raise RuntimeError("Whistle response must contain a string text field")
                if timings is not None:
                    # Upstream TTFT is audio-input to decoder first token, not
                    # this app's VAD-stop to generated speech TTFA.
                    for key in ("ttft_ms", "decode_tps"):
                        value = result.get(key)
                        timings[f"native_{key}"] = (
                            value if isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and value > 0 else None)
                return result["text"].strip()
            finally:
                if timings is not None:
                    timings["cli_ms"] = (time.monotonic() - started) * 1000
                await terminate_process(process)
