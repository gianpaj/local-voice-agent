"""Async supervisor for one persistent, killable Parakeet native process."""
import asyncio
import math
import os
import signal
import socket
import sys
import time
import uuid

from .native_protocol import MAX_PCM, encode, receive


class ResidentParakeet:
    def __init__(self, config):
        self.config = config
        self.lock = asyncio.Lock()
        self.process = self.reader = self.writer = None
        self.closed = False
        self.info = {}
        self.generation = 0
        self.inflight = 0
        self.recovery_task = None
        self.recovery_attempted = False
        self.cleanup_tasks = set()
        self.spawning_task = None

    @property
    def ready(self):
        return not self.closed and self.process is not None and self.process.returncode is None and bool(self.info)

    def request_recovery(self):
        """One bounded readiness reload, never a replay or an infinite crash loop."""
        if self.closed or self.ready or self.recovery_attempted:
            return
        self.recovery_attempted = True
        async def recover_once():
            try:
                await self.load()
            except (Exception, asyncio.CancelledError):
                pass  # Health stays unready; a restart/config fix may be needed.
        self.recovery_task = asyncio.create_task(recover_once())

    async def _spawn(self, child):
        env = dict(os.environ)
        env["TRANSCRIBE_LIBRARY"] = str(self.config.parakeet_library)
        env["PYTHONPATH"] = str(self.config.parakeet_bindings) + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("TRANSCRIBE_NATIVE_PROVIDER", None)
        # Avoid BLAS oversubscription if a locally linked candidate is selected.
        env["OPENBLAS_NUM_THREADS"] = str(self.config.stt_threads)
        env["OMP_NUM_THREADS"] = str(self.config.stt_threads)
        return await asyncio.create_subprocess_exec(
            sys.executable, "-m", "local_voice.native_worker", "--socket-fd", str(child.fileno()),
            "--model", str(self.config.parakeet_model), "--threads", str(self.config.stt_threads),
            pass_fds=(child.fileno(),), env=env, start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    async def _start_locked(self):
        if self.closed:
            raise RuntimeError("The resident transcription engine is closed")
        if self.ready:
            return
        await self._stop_locked()
        parent, child = socket.socketpair()
        parent.setblocking(False)
        started = time.monotonic()
        try:
            self.spawning_task = asyncio.create_task(self._spawn(child))
            self.process = await self.spawning_task
            if self.closed:
                raise RuntimeError("The resident transcription engine is closed")
            child.close()
            self.reader, self.writer = await asyncio.open_connection(sock=parent)
            async with asyncio.timeout(self.config.parakeet_startup_timeout):
                message, pcm = await receive(self.reader)
            if message.get("type") != "ready" or pcm or message.get("version") != "0.4.1":
                raise RuntimeError("The resident Parakeet worker could not initialize")
            self.info = {"startup_ms": (time.monotonic() - started) * 1000, "version": message["version"],
                         "commit": message.get("commit"), "generation": self.generation + 1}
            value = message.get("model_load_ms")
            self.info["model_load_ms"] = value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None
            self.generation += 1
        except (asyncio.CancelledError, TimeoutError):
            parent.close()
            child.close()
            await self._stop_locked()
            raise
        except Exception as error:
            parent.close()
            child.close()
            await self._stop_locked()
            raise RuntimeError("The resident Parakeet worker could not initialize") from error
        finally:
            self.spawning_task = None

    async def load(self):
        async with self.lock:
            await self._start_locked()
            return dict(self.info)

    async def _send(self, message, pcm=b""):
        self.writer.write(encode(message, pcm))
        await self.writer.drain()

    async def _abort_locked(self, request_id):
        try:
            async with asyncio.timeout(0.75):
                await self._send({"type": "cancel", "id": request_id})
                message, pcm = await receive(self.reader)
                if pcm or message.get("id") != request_id or message.get("type") not in {"cancelled", "result", "error"}:
                    raise ValueError("Invalid cancellation response")
        except (Exception, asyncio.CancelledError):
            await self._stop_locked()

    async def transcribe(self, pcm, *, timings=None):
        if self.inflight >= 3:
            raise RuntimeError("Resident transcription is busy; wait and retry")
        self.inflight += 1
        try:
            return await self._transcribe(pcm, timings=timings)
        finally:
            self.inflight -= 1

    async def _transcribe(self, pcm, *, timings=None):
        if len(pcm) % 2 or len(pcm) > min(MAX_PCM, int((self.config.max_utterance_seconds + 1.5) * 32000)):
            raise ValueError("Invalid or oversized signed 16-bit mono PCM")
        if not pcm or not any(pcm):
            return ""
        queued = time.monotonic()
        async with self.lock:
            if timings is not None:
                timings["worker_queue_ms"] = (time.monotonic() - queued) * 1000
            was_ready = self.ready
            await self._start_locked()
            if timings is not None:
                timings["worker_startup_ms"] = 0.0 if was_ready else self.info["startup_ms"]
            request_id = uuid.uuid4().hex
            try:
                async with asyncio.timeout(self.config.stt_timeout):
                    await self._send({"type": "run", "id": request_id}, pcm)
                    message, extra = await receive(self.reader)
                if extra or message.get("id") != request_id:
                    raise ValueError("Invalid resident transcription response")
                if message.get("type") != "result":
                    raise RuntimeError("Resident Parakeet transcription failed")
                text = message.get("text")
                if not isinstance(text, str):
                    raise ValueError("Invalid resident transcription result")
                if timings is not None:
                    supplied = message.get("timings") or {}
                    if not isinstance(supplied, dict):
                        raise ValueError("Invalid native timing result")
                    for key in ("native_mel_ms", "native_encode_ms", "native_decode_ms", "native_total_ms"):
                        value = supplied.get(key)
                        timings[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None
                self.recovery_attempted = False  # Recovery budget resets after real productive inference.
                return text.strip()
            except (asyncio.CancelledError, TimeoutError):
                await self._abort_locked(request_id)
                raise
            except Exception as error:
                await self._stop_locked()
                raise RuntimeError("The resident Parakeet worker failed; retry to reload it") from error

    async def _stop_locked(self):
        process, writer = self.process, self.writer
        self.info = {}
        try:
            await self._finish_cleanup(self._cleanup_owned(process, writer))
        finally:
            # Retain ownership until the shielded cleanup has actually reaped it.
            if self.process is process:
                self.process = self.reader = self.writer = None

    async def _finish_cleanup(self, operation):
        task = asyncio.create_task(operation)
        self.cleanup_tasks.add(task)
        interrupted = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    interrupted = True
            task.result()
        finally:
            self.cleanup_tasks.discard(task)
        if interrupted:
            raise asyncio.CancelledError()

    async def _cleanup_owned(self, process, writer):
        if writer:
            try:
                writer.write(encode({"type": "close"}))
                await asyncio.wait_for(writer.drain(), 0.1)
            except (Exception, asyncio.CancelledError):
                pass
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.1)
            except (Exception, asyncio.CancelledError):
                pass
        await self._reap_owned(process, graceful=True)

    async def _reap_owned(self, process, *, graceful=False):
        if process and process.returncode is None:
            if graceful:
                try:
                    await asyncio.wait_for(process.wait(), 0.5)
                    return
                except TimeoutError:
                    pass
            for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 1.0)):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    await asyncio.wait_for(process.wait(), grace)
                    break
                except TimeoutError:
                    continue
        if process and process.returncode is not None:
            await process.wait()

    async def close(self):
        self.closed = True
        await self._finish_cleanup(self._close_owned())

    async def _close_owned(self):
        if self.recovery_task and not self.recovery_task.done():
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        if self.spawning_task and not self.spawning_task.done():
            # The process handle does not exist yet. Cancel subprocess creation
            # rather than queueing shutdown behind a future startup deadline.
            self.spawning_task.cancel()
            await asyncio.gather(self.spawning_task, return_exceptions=True)
        # Shutdown never waits the full inference deadline to acquire this lock.
        if self.process and self.process.returncode is None and self.lock.locked():
            # Escalate before awaiting the holder: a TERM-resistant native call
            # must not keep shutdown behind its normal 15/45-second deadline.
            await self._reap_owned(self.process)
        async with self.lock:
            await self._stop_locked()
