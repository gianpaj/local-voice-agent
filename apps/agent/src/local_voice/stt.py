"""Utterance-level Parakeet CLI adapter, bounded and cancellation-safe."""
import asyncio
import os
import signal
import tempfile
import time
import wave
from pathlib import Path

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
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


async def transcribe_pcm(config: Config, pcm: bytes, *, timings=None) -> str:
    if len(pcm) % 2:
        raise ValueError("Expected signed 16-bit mono PCM")
    if len(pcm) > int((config.max_utterance_seconds + 1.5) * 16000 * 2):
        raise ValueError("Please pause at least every 30 seconds; that utterance was too long")
    if not pcm or not any(pcm):
        return ""
    if config.stt_backend == "whistle":
        from .whistle import transcribe_whistle
        return await transcribe_whistle(config, pcm, timings=timings)
    with tempfile.TemporaryDirectory(prefix="local-voice-stt-") as directory:
        wav_path = Path(directory) / "utterance.wav"
        output = Path(directory) / "transcript.txt"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        cli_started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            str(config.parakeet_bin), "--backend", "cuda", "--threads", str(config.stt_threads),
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
            if timings is not None:
                timings["cli_ms"] = (time.monotonic() - cli_started) * 1000
            await terminate_process(process)


class ParakeetSTT(SegmentedSTTService):
    def __init__(self, config: Config, status=None, telemetry=None):
        super().__init__(sample_rate=16000, trailing_silence_secs=0.25)
        self.config = config
        self.status = status
        self._segment_queue = asyncio.Queue(maxsize=2)
        self._overflow = False
        self.telemetry = telemetry
        self._segment_turns = {}

    def can_generate_metrics(self):
        return True

    @property
    def wants_wav_segments(self):
        return False

    def _record_rejection(self, captured_seconds):
        if self.telemetry:
            request = self.telemetry.request("stt", self.config.stt_model_id, turn=self.telemetry.current,
                metrics={"audio_seconds": 0, "captured_audio_seconds": captured_seconds, "cli_ms": None, "rejected": 1})
            request.finish("error")
            self.telemetry.end_turn(request.turn, "error")

    async def process_audio_frame(self, frame, direction):
        if self._overflow:
            return
        await super().process_audio_frame(frame, direction)
        if len(self._audio_buffer) > int(self.config.max_utterance_seconds * 32000):
            self._record_rejection(len(self._audio_buffer) / 32000)
            self._audio_buffer.clear()
            self._overflow = True
            await self.push_error("Please pause at least every 30 seconds. This utterance was not transcribed.")

    async def _handle_user_started_speaking(self, frame):
        # This handler runs inline in the STT processor. Pipeline observers are
        # queued notifications, so using one to select ownership races the CLI.
        if self.telemetry:
            self.telemetry.start_turn()
        await super()._handle_user_started_speaking(frame)

    async def _handle_user_stopped_speaking(self, frame):
        if self.telemetry:
            turn = self.telemetry.current or self.telemetry.start_turn()
            turn.vad_stopped = time.monotonic()
            turn.endpointing_vad_reported_ms = frame.stop_secs * 1000 if frame.stop_secs else None
        if self._overflow:
            self._overflow = False
            self._user_speaking = False
            self._audio_buffer.clear()
            return
        if self._segment_queue.full():
            self._record_rejection(len(self._audio_buffer) / 32000)
            self._user_speaking = False
            self._audio_buffer.clear()
            await self.push_error("Transcription is busy. Please wait and try that again.")
            return
        if self.telemetry:
            self._segment_turns[self._segment_count] = self.telemetry.current
        await super()._handle_user_stopped_speaking(frame)

    async def run_stt(self, audio):
        started = time.monotonic()
        request = None
        turn = self._segment_turns.pop(self._transcribing_segment, None)
        if self.telemetry:
            turn = turn or self.telemetry.start_turn()
            request = self.telemetry.request("stt", self.config.stt_model_id, turn=turn,
                metrics={"audio_seconds": len(audio) / 32000, "cli_ms": None,
                         "stt_backend": self.config.stt_backend, "stt_device": self.config.stt_device,
                         "stt_threads": self.config.stt_threads})
        status = "empty"
        try:
            if self.status:
                await self.status("transcribing")
            text = await transcribe_pcm(self.config, audio, timings=request.metrics if request else None)
            if text:
                status = "ok"
                if request:
                    self.telemetry.response_turn = request.turn
                frame = TranscriptionFrame(text, self._user_id, time_now_iso8601(), language=Language.EN)
                frame.local_voice_turn = request.turn if request else None
                yield frame
        except (asyncio.CancelledError, GeneratorExit):
            status = "cancelled"
            raise
        except TimeoutError:
            status = "timeout"
            await self.push_error("Local transcription timed out. Please try a shorter phrase.")
        except (ValueError, RuntimeError) as error:
            status = "error"
            await self.push_error(str(error))
        except BaseException:
            status = "error"
            raise
        finally:
            if request:
                request.finish(status)
                if status in {"empty", "error", "timeout"}:
                    self.telemetry.end_turn(request.turn, status)
            from loguru import logger
            logger.info("stage=stt seconds={:.3f}", time.monotonic() - started)
