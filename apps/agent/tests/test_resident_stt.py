"""Exercise the actual child protocol/lifecycle without loading real weights."""
import asyncio
from dataclasses import replace
from pathlib import Path
import struct
import sys
import time

import pytest

from local_voice.native_protocol import MAX_JSON, MAX_PCM, decode, encode, lengths
from local_voice.resident_stt import ResidentParakeet
from local_voice.stt import transcribe_pcm
from test_adapters import config

FAKE_BINDING = '''
from pathlib import Path
import os,threading,time
from types import SimpleNamespace
__version__='0.4.1'
def native_version():return '0.4.1'
def native_commit():return '5700f12'
def set_log_callback(callback):pass
class Aborted(RuntimeError):pass
class Model:
 def __init__(self,path,backend):
  assert backend=='cuda'
  self.path=Path(path)
  mode=self.path.read_bytes()[4:].decode()
  if mode=='startup_hang':time.sleep(60)
  if mode=='startup_error':raise RuntimeError('PRIVATE_native_path_do_not_log')
 def session(self,n_threads):return Session(self)
 def close(self):pass
class Session:
 def __init__(self,model):self.model=model;self.abort=threading.Event()
 def cancel(self):self.abort.set()
 def close(self):pass
 def run(self,pcm,timestamps):
  assert timestamps=='none'
  assert abs(float(pcm[0])-1/32768)<1e-8
  self.abort.clear()
  mode=self.model.path.read_bytes()[4:].decode()
  if mode=='crash':os._exit(4)
  if mode=='error':raise RuntimeError('PRIVATE_native_path_do_not_log')
  if mode=='hang':time.sleep(60)
  if mode in ('slow','cooperative'):
   for _ in range(60 if mode=='cooperative' else 6):
    if self.abort.wait(.02):raise Aborted('cancelled')
  return SimpleNamespace(text='Hello local world.',timings=SimpleNamespace(mel_ms=0,encode_ms=2.5,decode_ms=1.0))
'''


@pytest.fixture
def resident_config(config, tmp_path):
    binding = tmp_path / "bindings/transcribe_cpp"
    binding.mkdir(parents=True)
    (binding / "__init__.py").write_text(FAKE_BINDING)
    library = tmp_path / "libtranscribe.so"
    library.touch()
    config.parakeet_model.write_bytes(b"GGUFok")
    return replace(config, parakeet_mode="resident", parakeet_library=library,
                   parakeet_bindings=binding.parent, parakeet_startup_timeout=2)


def test_protocol_rejects_unbounded_or_invalid_frames():
    with pytest.raises(ValueError):
        encode({"type": "run"}, b"x" * (MAX_PCM + 1))
    with pytest.raises(ValueError):
        lengths(struct.pack("!II", MAX_JSON + 1, 0))
    with pytest.raises(ValueError):
        lengths(struct.pack("!II", 2, MAX_PCM + 1))
    with pytest.raises(ValueError):
        decode(b'[]')
    with pytest.raises(ValueError):
        encode({"type": "run", "value": float("nan")})


async def test_reuses_one_child_and_keeps_missing_native_timings_null(resident_config):
    engine = ResidentParakeet(resident_config)
    try:
        await engine.load()
        pid = engine.process.pid
        for _ in range(3):
            timings = {}
            text = await transcribe_pcm(resident_config, b"\1\0" * 1600, timings=timings, engine=engine)
            assert text == "Hello local world."
            assert engine.process.pid == pid and engine.generation == 1
            assert timings["worker_startup_ms"] == 0
            assert timings["native_mel_ms"] is None
            assert timings["native_encode_ms"] == 2.5
    finally:
        await engine.close()
    assert not Path(f"/proc/{pid}").exists()


async def test_dispatcher_never_silently_falls_back(resident_config):
    with pytest.raises(RuntimeError, match="application-owned"):
        await transcribe_pcm(resident_config, b"\1\0" * 100)
    assert await transcribe_pcm(resident_config, bytes(100)) == ""
    with pytest.raises(ValueError):
        await transcribe_pcm(resident_config, b"x")


@pytest.mark.parametrize("mode", ("error", "crash"))
async def test_fault_invalidates_generation_and_next_request_can_reload(resident_config, mode):
    engine = ResidentParakeet(resident_config)
    try:
        await engine.load()
        old_pid = engine.process.pid
        resident_config.parakeet_model.write_bytes(b"GGUF" + mode.encode())
        with pytest.raises(RuntimeError, match="retry to reload"):
            await engine.transcribe(b"\1\0" * 100)
        assert not engine.ready and not Path(f"/proc/{old_pid}").exists()
        resident_config.parakeet_model.write_bytes(b"GGUFok")
        assert await engine.transcribe(b"\1\0" * 100) == "Hello local world."
        assert engine.generation == 2 and engine.process.pid != old_pid
    finally:
        await engine.close()


@pytest.mark.parametrize("cancel_task", (False, True))
async def test_native_abort_reuses_model_without_delivering_stale_result(resident_config, cancel_task):
    engine = ResidentParakeet(replace(resident_config, stt_timeout=0.06))
    try:
        await engine.load()
        old_pid = engine.process.pid
        resident_config.parakeet_model.write_bytes(b"GGUFcooperative")
        task = asyncio.create_task(engine.transcribe(b"\1\0" * 100))
        if cancel_task:
            await asyncio.sleep(0.03)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel_task else TimeoutError):
            await task
        assert engine.ready and engine.process.pid == old_pid
        resident_config.parakeet_model.write_bytes(b"GGUFok")
        assert await engine.transcribe(b"\1\0" * 100) == "Hello local world."
    finally:
        await engine.close()


async def test_stuck_native_call_is_killed_with_bounded_cleanup(resident_config):
    engine = ResidentParakeet(replace(resident_config, stt_timeout=0.05))
    try:
        await engine.load()
        pid = engine.process.pid
        resident_config.parakeet_model.write_bytes(b"GGUFhang")
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await engine.transcribe(b"\1\0" * 100)
        assert time.monotonic() - started < 2.5
        assert not engine.ready and not Path(f"/proc/{pid}").exists()
    finally:
        await engine.close()


@pytest.mark.parametrize("mode", ("startup_hang", "startup_error"))
async def test_failed_startup_is_reaped_and_can_retry(resident_config, mode):
    resident_config.parakeet_model.write_bytes(b"GGUF" + mode.encode())
    engine = ResidentParakeet(replace(resident_config, parakeet_startup_timeout=0.15))
    try:
        with pytest.raises((TimeoutError, RuntimeError)):
            await engine.load()
        assert engine.process is None and not engine.ready
        resident_config.parakeet_model.write_bytes(b"GGUFok")
        await engine.load()
        assert engine.ready
    finally:
        await engine.close()


async def test_cancelling_queued_request_does_not_cancel_active_inference(resident_config):
    engine = ResidentParakeet(resident_config)
    try:
        await engine.load()
        resident_config.parakeet_model.write_bytes(b"GGUFslow")
        first = asyncio.create_task(engine.transcribe(b"\1\0" * 100))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(engine.transcribe(b"\1\0" * 100))
        await asyncio.sleep(0.01)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert await first == "Hello local world." and engine.ready
    finally:
        await engine.close()


async def test_shutdown_does_not_wait_for_stuck_native_call(resident_config):
    engine = ResidentParakeet(resident_config)
    await engine.load()
    pid = engine.process.pid
    resident_config.parakeet_model.write_bytes(b"GGUFhang")
    task = asyncio.create_task(engine.transcribe(b"\1\0" * 100))
    await asyncio.sleep(0.03)
    await asyncio.wait_for(engine.close(), 2)
    with pytest.raises(RuntimeError):
        await task
    assert not Path(f"/proc/{pid}").exists()
    with pytest.raises(RuntimeError, match="closed"):
        await engine.load()


async def test_bounded_admission_rejects_more_than_two_waiting_requests(resident_config):
    engine = ResidentParakeet(resident_config)
    try:
        await engine.load()
        resident_config.parakeet_model.write_bytes(b"GGUFslow")
        tasks = [asyncio.create_task(engine.transcribe(b"\1\0" * 100)) for _ in range(3)]
        await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError, match="busy"):
            await engine.transcribe(b"\1\0" * 100)
        assert await asyncio.gather(*tasks) == ["Hello local world."] * 3
    finally:
        await engine.close()


async def test_readiness_recovery_is_one_attempt_not_a_restart_loop(resident_config):
    engine = ResidentParakeet(resident_config)
    try:
        await engine.load()
        engine.process.kill()
        await engine.process.wait()
        resident_config.parakeet_model.write_bytes(b"GGUFstartup_error")
        engine.request_recovery()
        await engine.recovery_task
        first_attempt = engine.recovery_task
        assert not engine.ready
        resident_config.parakeet_model.write_bytes(b"GGUFok")
        engine.request_recovery()
        assert engine.recovery_task is first_attempt  # No repeated failed reloads.
        # An explicit request/restart after fixing configuration can still recover.
        assert await engine.transcribe(b"\1\0" * 100) == "Hello local world."
        assert engine.ready and not engine.recovery_attempted
    finally:
        await engine.close()


async def test_shutdown_escalates_before_waiting_on_term_resistant_request(resident_config, monkeypatch):
    engine = ResidentParakeet(resident_config)
    program = '''
import signal,socket,sys,time
from local_voice.native_protocol import encode,receive_blocking
signal.signal(signal.SIGTERM,signal.SIG_IGN)
sock=socket.socket(fileno=int(sys.argv[1]))
sock.sendall(encode({'type':'ready','version':'0.4.1','commit':'5700f12'}))
while True:
 message,pcm=receive_blocking(sock)
 if message['type']=='run':time.sleep(60)
'''
    async def spawn(child):
        return await asyncio.create_subprocess_exec(sys.executable, "-c", program, str(child.fileno()),
            pass_fds=(child.fileno(),), start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    monkeypatch.setattr(engine, "_spawn", spawn)
    await engine.load()
    pid = engine.process.pid
    task = asyncio.create_task(engine.transcribe(b"\1\0" * 100))
    await asyncio.sleep(0.03)
    await asyncio.wait_for(engine.close(), 2)
    with pytest.raises(RuntimeError):
        await task
    assert not Path(f"/proc/{pid}").exists()


async def test_repeated_cancel_during_cleanup_still_reaps_child(resident_config):
    engine = ResidentParakeet(replace(resident_config, stt_timeout=0.05))
    await engine.load()
    pid = engine.process.pid
    resident_config.parakeet_model.write_bytes(b"GGUFhang")
    task = asyncio.create_task(engine.transcribe(b"\1\0" * 100))
    await asyncio.sleep(0.9)  # Deadline + abort grace have entered process cleanup.
    task.cancel()
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not Path(f"/proc/{pid}").exists() and not engine.cleanup_tasks
    await engine.close()


async def test_close_cancels_startup_before_a_child_handle_exists(resident_config, monkeypatch):
    engine = ResidentParakeet(resident_config)
    entered = asyncio.Event()
    async def delayed_spawn(child):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(engine, "_spawn", delayed_spawn)
    loading = asyncio.create_task(engine.load())
    await entered.wait()
    await asyncio.wait_for(engine.close(), 1)
    with pytest.raises(asyncio.CancelledError):
        await loading
    assert engine.process is None and not engine.ready and not engine.cleanup_tasks
