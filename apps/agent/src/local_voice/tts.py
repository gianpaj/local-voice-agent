"""Phonon CPU stream with an owning thread and bounded, cancellable PCM delivery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import queue
import threading
import time

import numpy as np
from pipecat.frames.frames import CancelFrame, InterruptionFrame, TTSAudioRawFrame
from pipecat.services.tts_service import TTSService


def pcm16(samples):
    return (np.clip(np.asarray(samples), -1.0, 1.0) * 32767).astype("<i2").tobytes()


@dataclass
class Generation:
    id: int
    canceled: threading.Event = field(default_factory=threading.Event)


class PhononEngine:
    def __init__(self, model_path):
        self.model_path = model_path
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phonon-owner")
        self.model = None
        self.active: Generation | None = None
        self.generation = 0

    async def load(self):
        def load():
            import ptts
            ptts.set_num_threads(4)
            self.model = ptts.TTS(str(self.model_path), lang="en", device="cpu", quant="q8_0")
        await asyncio.get_running_loop().run_in_executor(self.executor, load)

    def interrupt(self):
        self.generation += 1
        if self.active:
            self.active.canceled.set()

    async def stream(self, text):
        self.interrupt()
        generation = Generation(self.generation)
        self.active = generation
        chunks = queue.Queue(maxsize=8)
        done = threading.Event()
        errors = []

        def produce():
            iterator = None
            try:
                if generation.canceled.is_set():
                    return
                iterator = self.model.stream(text)
                for samples in iterator:
                    if generation.canceled.is_set():
                        break
                    data = pcm16(samples)
                    while not generation.canceled.is_set():
                        try:
                            chunks.put(data, timeout=0.05)
                            break
                        except queue.Full:
                            continue
            except Exception as error:
                errors.append(error)
            finally:
                if iterator is not None and hasattr(iterator, "close"):
                    iterator.close()  # Native iterator is closed by its owning thread.
                done.set()

        worker = asyncio.get_running_loop().run_in_executor(self.executor, produce)
        try:
            while not done.is_set() or not chunks.empty():
                if generation.canceled.is_set() or generation.id != self.generation:
                    break
                try:
                    data = chunks.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if generation.id == self.generation and not generation.canceled.is_set():
                    yield data
            if errors:
                raise RuntimeError("Local Phonon synthesis failed") from errors[0]
        finally:
            generation.canceled.set()
            # Shield the owner: canceling an await alone does not stop Rust synthesis.
            await asyncio.shield(worker)
            if self.active is generation:
                self.active = None

    async def close(self):
        self.interrupt()
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)


class PhononTTS(TTSService):
    def __init__(self, engine: PhononEngine):
        super().__init__(sample_rate=24000, push_start_frame=True, push_stop_frames=True)
        self.engine = engine

    async def run_tts(self, text, context_id):
        if len(text) > 1200:
            await self.push_error("That response is too long to synthesize. Please ask for a shorter answer.")
            return
        started = time.monotonic()
        first = True
        try:
            async for data in self.engine.stream(text):
                if first:
                    from loguru import logger
                    logger.info("stage=tts first_pcm_seconds={:.3f}", time.monotonic() - started)
                    first = False
                yield TTSAudioRawFrame(data, 24000, 1, context_id=context_id)
        except RuntimeError as error:
            await self.push_error(str(error))

    async def process_frame(self, frame, direction):
        if isinstance(frame, (InterruptionFrame, CancelFrame)):
            self.engine.interrupt()
        await super().process_frame(frame, direction)

    async def on_audio_context_interrupted(self, context_id):
        self.engine.interrupt()

    async def cleanup(self):
        self.engine.interrupt()
        await super().cleanup()
