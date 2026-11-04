"""Utterance-level Parakeet CLI adapter, bounded and cancellation-safe."""
import asyncio
import os
import signal
import tempfile
import time
import wave
from pathlib import Path

from pipecat.frames.frames import TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from .config import Config


async def terminate_process(process):
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 1)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def transcribe_pcm(config: Config, pcm: bytes) -> str:
    if len(pcm) % 2:
        raise ValueError("Expected signed 16-bit mono PCM")
    if len(pcm) > int((config.max_utterance_seconds + 1.5) * 16000 * 2):
        raise ValueError("Please pause at least every 30 seconds; that utterance was too long")
    if not pcm or not any(pcm):
        return ""
    with tempfile.TemporaryDirectory(prefix="local-voice-stt-") as directory:
        wav_path = Path(directory) / "utterance.wav"
        output = Path(directory) / "transcript.txt"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        process = await asyncio.create_subprocess_exec(
            str(config.parakeet_bin), "--backend", "cuda", "--threads", "4",
            "--timestamps", "none", "--quiet", "-m", str(config.parakeet_model),
            str(wav_path), "-o", str(output),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(config.stt_timeout):
                await process.wait()
            if process.returncode != 0 or not output.exists():
                raise RuntimeError(f"Local Parakeet transcription failed (exit {process.returncode})")
            return output.read_text().strip()
        finally:
            await terminate_process(process)


class ParakeetSTT(SegmentedSTTService):
    def __init__(self, config: Config, status=None):
        super().__init__(sample_rate=16000, trailing_silence_secs=0.25)
        self.config = config
        self.status = status
        self._segment_queue = asyncio.Queue(maxsize=2)
        self._overflow = False

    @property
    def wants_wav_segments(self):
        return False

    async def process_audio_frame(self, frame, direction):
        if self._overflow:
            return
        await super().process_audio_frame(frame, direction)
        if len(self._audio_buffer) > int(self.config.max_utterance_seconds * 32000):
            self._audio_buffer.clear()
            self._overflow = True
            await self.push_error("Please pause at least every 30 seconds. This utterance was not transcribed.")

    async def _handle_user_stopped_speaking(self, frame):
        if self._overflow:
            self._overflow = False
            self._user_speaking = False
            self._audio_buffer.clear()
            return
        if self._segment_queue.full():
            self._user_speaking = False
            self._audio_buffer.clear()
            await self.push_error("Transcription is busy. Please wait and try that again.")
            return
        await super()._handle_user_stopped_speaking(frame)

    async def run_stt(self, audio):
        started = time.monotonic()
        if self.status:
            await self.status("transcribing")
        try:
            text = await transcribe_pcm(self.config, audio)
            if text:
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), language=Language.EN)
        except TimeoutError:
            await self.push_error("Local transcription timed out. Please try a shorter phrase.")
        except (ValueError, RuntimeError) as error:
            await self.push_error(str(error))
        finally:
            from loguru import logger
            logger.info("stage=stt seconds={:.3f}", time.monotonic() - started)
