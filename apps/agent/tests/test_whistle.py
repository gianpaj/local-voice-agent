"""Backend seam tests; fake runner is not evidence of model quality."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from local_voice.config import Config
from local_voice.stt import transcribe_pcm


@pytest.fixture
def whistle_config(tmp_path):
    directory = tmp_path / "paths with spaces"
    directory.mkdir()
    binary = directory / "needle"
    binary.write_text(f"#!{sys.executable}\nimport json,os,sys,wave\n"
        "assert os.environ['NEEDLE_TELEMETRY']=='0'\n"
        "assert os.environ['DO_NOT_TRACK']=='1'\n"
        "assert os.environ['HF_HUB_OFFLINE']=='1'\n"
        "assert sys.argv[sys.argv.index('--audio-language')+1]=='en'\n"
        "assert sys.argv[sys.argv.index('--threads')+1]=='4'\n"
        "assert '--audio-depth' not in sys.argv\n"
        "with wave.open(sys.argv[sys.argv.index('--audio')+1]) as w:\n"
        " assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2)\n"
        "print(json.dumps({'text':'Call José on Friday.', 'ttft_ms':12.5,'decode_tps':float('nan')}))\n")
    binary.chmod(0o700)
    model = directory / "whistle.cact"
    model.write_bytes(b"test-export")
    phonon = directory / "phonon"
    phonon.mkdir()
    return Config(None, None, phonon, stt_backend="whistle", needle_bin=binary, whistle_model=model)


async def test_whistle_uses_native_json_argv_offline_and_shared_pcm(whistle_config):
    whistle_config.validate()  # No Parakeet install required in Whistle mode.
    timings = {}
    result = await transcribe_pcm(whistle_config, b"\x01\x00" * 1600, timings=timings)
    assert result == "Call José on Friday."
    assert timings["cli_ms"] > 0
    assert timings["native_ttft_ms"] == 12.5
    assert timings["native_decode_tps"] is None


@pytest.mark.parametrize("body,expected", [
    ("print('banner, not JSON')", "malformed JSON"),
    ("print('{}')", "string text"),
    ("print('[]')", "string text"),
    ("raise SystemExit(2)", "exit 2"),
    ("print('x'*256001)", "oversized"),
])
async def test_whistle_rejects_bad_results_without_echoing_content(whistle_config, body, expected):
    whistle_config.needle_bin.write_text(f"#!{sys.executable}\n{body}\n")
    with pytest.raises(RuntimeError, match=expected):
        await transcribe_pcm(whistle_config, b"\x01\x00" * 1600)


async def test_whistle_silence_never_starts_process(whistle_config):
    whistle_config.needle_bin.unlink()
    assert await transcribe_pcm(whistle_config, bytes(3200)) == ""


async def test_whistle_deadline_cancel_reap_and_remove_temporary_audio(whistle_config, tmp_path):
    state = tmp_path / "runner.json"
    whistle_config.needle_bin.write_text(f"#!{sys.executable}\n"
        "import json,os,pathlib,sys,time\n"
        f"pathlib.Path({str(state)!r}).write_text(json.dumps({{'pid':os.getpid(),'audio':sys.argv[sys.argv.index('--audio')+1]}}))\n"
        "time.sleep(60)\n")
    with pytest.raises(TimeoutError):
        await transcribe_pcm(replace(whistle_config, stt_timeout=0.15), b"\x01\x00" * 1600)
    result = json.loads(state.read_text())
    assert not Path(f"/proc/{result['pid']}").exists()
    assert not Path(result["audio"]).exists()
    state.unlink()
    task = asyncio.create_task(transcribe_pcm(whistle_config, b"\x01\x00" * 1600))
    for _ in range(100):
        if state.exists():
            break
        await asyncio.sleep(0.01)
    assert state.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = json.loads(state.read_text())
    assert not Path(f"/proc/{result['pid']}").exists()
    assert not Path(result["audio"]).exists()


def test_invalid_stt_backend_model_and_threads_are_rejected(whistle_config):
    for config, message in [
        (replace(whistle_config, stt_backend="hosted"), "STT_BACKEND"),
        (replace(whistle_config, stt_threads=0), "STT_THREADS"),
        (replace(whistle_config, needle_bin=None), "NEEDLE_BIN"),
        (replace(whistle_config, whistle_model=None), "WHISTLE_MODEL"),
    ]:
        with pytest.raises(ValueError, match=message):
            config.validate()
