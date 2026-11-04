import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np
import pytest

from local_voice.config import Config
from local_voice.stt import transcribe_pcm, ParakeetSTT
from local_voice.tts import PhononEngine, pcm16


@pytest.fixture
def config(tmp_path):
    binary = tmp_path / "transcribe-cli"
    binary.write_text(f"#!{sys.executable}\nimport pathlib,sys\nprint('not a transcript banner')\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('Hello local world.')\n")
    binary.chmod(0o700)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFfake")
    phonon = tmp_path / "phonon"
    phonon.mkdir()
    return Config(binary, model, phonon)


async def test_transcript_comes_from_file_not_stdout(config):
    assert await transcribe_pcm(config, b"\x01\x00" * 1600) == "Hello local world."


async def test_silence_does_not_start_a_process(config):
    config.parakeet_bin.unlink()
    assert await transcribe_pcm(config, bytes(1600)) == ""


async def test_timeout_and_cancel_reap_subprocess(config, tmp_path):
    pid = tmp_path / "pid"
    config.parakeet_bin.write_text(f"#!{sys.executable}\nimport os,time,pathlib\npathlib.Path({str(pid)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    with pytest.raises(TimeoutError):
        await transcribe_pcm(replace(config, stt_timeout=0.15), b"\x01\x00" * 1600)
    assert not Path(f"/proc/{pid.read_text()}").exists()
    task = asyncio.create_task(transcribe_pcm(config, b"\x01\x00" * 1600))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not Path(f"/proc/{pid.read_text()}").exists()


async def test_bad_pcm_and_overlong_input(config):
    with pytest.raises(ValueError, match="16-bit"):
        await transcribe_pcm(config, b"x")
    with pytest.raises(ValueError, match="too long"):
        await transcribe_pcm(config, b"\x01\x00" * 16000 * 33)


def test_cloud_url_and_invalid_model_rejected(config):
    with pytest.raises(ValueError, match="loopback"):
        replace(config, ollama_url="https://example.com").validate()
    config.parakeet_model.write_bytes(b"nope")
    with pytest.raises(ValueError, match="GGUF"):
        config.validate()


def test_pcm_conversion_clamps_and_is_int16():
    assert np.frombuffer(pcm16([-2, -1, 0, 1, 2]), dtype="<i2").tolist() == [-32767, -32767, 0, 32767, 32767]


async def test_phonon_cancel_closes_iterator_on_owner_and_can_restart(tmp_path):
    closed = []
    class Model:
        def stream(self, text):
            owner = threading.get_ident()
            try:
                for _ in range(100):
                    time.sleep(0.002)
                    yield np.ones(48, dtype=np.float32) * 0.1
            finally:
                closed.append((owner, threading.get_ident()))
    engine = PhononEngine(tmp_path)
    engine.model = Model()
    try:
        first = engine.stream("First")
        assert len(await anext(first)) == 96
        engine.interrupt()
        with pytest.raises(StopAsyncIteration):
            await anext(first)
        assert closed and closed[0][0] == closed[0][1]
        second = engine.stream("Second")
        assert len(await anext(second)) == 96
        await second.aclose()
        assert len(closed) == 2
    finally:
        await engine.close()


async def test_capture_is_bounded_and_rejects_overlong_turn(config):
    from pipecat.frames.frames import InputAudioRawFrame, VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection
    service = ParakeetSTT(replace(config, max_utterance_seconds=0.1))
    service._user_speaking = True
    errors = []
    async def capture_error(message, **kwargs):
        errors.append(message)
    service.push_error = capture_error
    await service.process_audio_frame(InputAudioRawFrame(b"\x01\x00" * 3200, 16000, 1), FrameDirection.DOWNSTREAM)
    assert service._overflow and not service._audio_buffer and errors
    await service._handle_user_stopped_speaking(VADUserStoppedSpeakingFrame())
    assert not service._overflow and service._segment_queue.empty()
