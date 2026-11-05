"""The persisted telemetry contract must be safe and meaningful, not merely valid JSON."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame,
    LLMTextFrame, MetricsFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTFBMetricsData, TTFATMetricsData
from pipecat.processors.frame_processor import FrameDirection

from local_voice.telemetry import (CascadeObserver, JsonlSink, SessionMetrics, SafeSpanExporter,
    configure_tracing, close_tracing)
from test_adapters import config


def records(directory):
    return [json.loads(line) for path in sorted(directory.glob("metrics.jsonl*")) for line in path.read_text().splitlines()]


@pytest.fixture
def telemetry(tmp_path):
    sink = JsonlSink(tmp_path)
    configure_tracing(sink)
    session = SessionMetrics(sink, 0.6)
    yield session, sink, tmp_path
    session.close()
    close_tracing(sink)


def test_privacy_exporter_discards_every_content_surface(telemetry):
    session, sink, path = telemetry
    sentinel = "DO_NOT_PERSIST_dialogue_secret_4824"
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span(sentinel, attributes={
        "conversation.id": session.id, "input": sentinel, "output": sentinel, "text": sentinel,
        "transcript": sentinel, "tool.arguments": sentinel, "metrics.ttfb": 0.25,
        "gen_ai.usage.input_tokens": 9, "local.turn_id": sentinel,
    }) as span:
        span.add_event(sentinel, {"value": sentinel})
        span.record_exception(RuntimeError(sentinel))
        span.set_status(Status(StatusCode.ERROR, sentinel))
    sink.flush()
    payload = "\n".join(p.read_text() for p in path.glob("metrics.jsonl*"))
    assert sentinel not in payload
    item = next(r for r in records(path) if r["record_type"] == "span")
    assert item["stage"] == "operation" and item["status"] == "error"
    assert item["metrics"]["metrics_ttfb"] == 0.25
    assert item["turn_id"] is None and "events" not in item


async def test_vad_ttfa_and_server_speaking_are_separate_and_missing_stays_null(telemetry):
    session, sink, path = telemetry
    observer = CascadeObserver(session)
    async def pushed(frame):
        await observer.on_push_frame(SimpleNamespace(frame=frame))
    # Speech ownership is established inline by the STT processor, not by a
    # queued observer event. This test isolates only the audio/server boundaries.
    turn = session.start_turn()
    turn.vad_stopped = time.monotonic()
    turn.endpointing_vad_reported_ms = 600
    await pushed(VADUserStartedSpeakingFrame())
    await pushed(VADUserStoppedSpeakingFrame(stop_secs=0.6))
    request = session.request("tts", "phonon", turn=turn)
    session.response_turn = turn
    await asyncio.sleep(0.002)
    session.generated_pcm(request)
    await asyncio.sleep(0.002)
    await pushed(BotStartedSpeakingFrame())
    request.finish("ok")
    await pushed(BotStoppedSpeakingFrame())
    session.close()
    await observer.cleanup()
    sink.flush()
    item = next(r for r in records(path) if r["record_type"] == "turn")
    assert item["status"] == "ok"
    assert item["metrics"]["server_speaking_ms"] > item["metrics"]["agent_ttfa_ms"] > 0
    assert item["metrics"]["endpointing_config_ms"] == 600
    assert item["metrics"]["endpointing_vad_reported_ms"] == 600
    assert item["metrics"]["endpointing_observed_ms"] is None
    assert item["metrics"]["browser_playback_ms"] is None
    assert item["metrics"]["acoustic_ttfa_ms"] is None


def test_cancelled_request_remains_on_original_turn_and_missing_first_audio(telemetry):
    session, sink, path = telemetry
    old = session.start_turn()
    request = session.request("llm", "qwen", turn=old, metrics={"first_answer_token_ms": None})
    new = session.start_turn()
    request.finish("cancelled")
    request.finish("ok")  # Finishing twice must not duplicate the observation.
    session.generated_pcm(request)  # Stale producer cannot populate the new turn.
    session.close()
    sink.flush()
    stages = [r for r in records(path) if r["record_type"] == "stage"]
    assert len(stages) == 1 and stages[0]["turn_id"] == old.id != new.id
    assert stages[0]["status"] == "cancelled" and stages[0]["metrics"]["first_answer_token_ms"] is None
    assert new.first_pcm is None
    assert all(r["metrics"]["agent_ttfa_ms"] is None for r in records(path) if r["record_type"] == "turn")


def test_rotation_retains_bounded_files_and_flushes(tmp_path):
    sink = JsonlSink(tmp_path, max_bytes=180, backups=2)
    for n in range(20):
        sink.emit({"record_type": "session", "n": n, "metrics": {"total_ms": 123.4}})
    sink.close()
    files = sorted(tmp_path.glob("metrics.jsonl*"))
    assert len(files) == 3
    assert max(r["n"] for r in records(tmp_path)) == 19
    assert sink.health()["written"] == 20 and sink.health()["write_errors"] == 0
    assert all(p.stat().st_mode & 0o077 == 0 for p in files)


def test_nonblocking_queue_reports_drops_and_write_errors(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class BlockedSink(JsonlSink):
        def _write(self, line):
            entered.set()
            release.wait(2)
            raise OSError("sentinel private disk path")
    sink = BlockedSink(tmp_path, capacity=1)
    sink.emit({"n": 0})
    assert entered.wait(1)
    sink.emit({"n": 1})
    started = time.monotonic()
    sink.emit({"n": 2})
    assert time.monotonic() - started < 0.05
    assert sink.health()["dropped"] == 1
    release.set()
    sink.close()
    assert sink.health()["write_errors"] == 2


async def test_stt_empty_timeout_and_cancel_are_persisted(config, telemetry, monkeypatch):
    import local_voice.stt as module
    session, sink, path = telemetry
    service = module.ParakeetSTT(config, telemetry=session)
    async def ignored_error(*args, **kwargs):
        pass
    service.push_error = ignored_error
    for result in ("empty", "timeout", "cancelled"):
        turn = session.start_turn()
        service._segment_turns[service._transcribing_segment] = turn
        async def transcribe(*args, **kwargs):
            if result == "empty":
                return ""
            if result == "timeout":
                raise TimeoutError()
            raise asyncio.CancelledError()
        monkeypatch.setattr(module, "transcribe_pcm", transcribe)
        if result == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                async for _ in service.run_stt(b"\1\0" * 800):
                    pass
        else:
            async for _ in service.run_stt(b"\1\0" * 800):
                pass
    session.close()
    sink.flush()
    stages = [r for r in records(path) if r["record_type"] == "stage"]
    assert [r["status"] for r in stages] == ["empty", "timeout", "cancelled"]
    assert all(r["metrics"]["audio_seconds"] == 0.05 for r in stages)
    assert all(r["metrics"]["cli_ms"] is None for r in stages)
    assert [r["status"] for r in records(path) if r["record_type"] == "turn"][:2] == ["empty", "timeout"]


async def test_llm_native_metrics_capture_original_request_and_answer_not_reasoning(config, telemetry, monkeypatch):
    from local_voice.llm import BoundedOllama, OLLamaLLMService
    session, sink, path = telemetry
    old = session.start_turn()
    session.response_turn = old
    service = BoundedOllama(config, telemetry=session)
    original_push = OLLamaLLMService.push_frame
    async def no_transport(*args, **kwargs):
        pass
    monkeypatch.setattr(OLLamaLLMService, "push_frame", no_transport)
    async def response(self, context):
        await self.push_frame(MetricsFrame([TTFBMetricsData(processor=self.name, value=0.01)]))
        session.start_turn()  # User interrupted while this stream is unwinding.
        await self.push_frame(MetricsFrame([TTFATMetricsData(processor=self.name, ttfat=0.02, ttfb=0.01, thinking_time=0.01)]))
        await self.push_frame(LLMTextFrame("sentinel answer not for logs"))
        await self.push_frame(MetricsFrame([LLMUsageMetricsData(processor=self.name,
            value=LLMTokenUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9))]))
        raise asyncio.CancelledError()
    monkeypatch.setattr(OLLamaLLMService, "_process_context", response)
    with pytest.raises(asyncio.CancelledError):
        await service._process_context(SimpleNamespace(messages=[]))
    session.close()
    sink.flush()
    item = next(r for r in records(path) if r["record_type"] == "stage")
    assert item["status"] == "cancelled" and item["turn_id"] == old.id
    assert item["metrics"]["service_ttfb_ms"] == 10
    assert item["metrics"]["service_ttfat_ms"] == 20
    assert item["metrics"]["first_answer_token_ms"] is not None
    assert item["metrics"]["total_tokens"] == 9
    assert "sentinel answer" not in (path / "metrics.jsonl").read_text()


async def test_delayed_vad_observer_cannot_bind_new_segment_to_old_response(config, telemetry, monkeypatch):
    import local_voice.stt as module
    session, sink, path = telemetry
    old = session.start_turn()
    session.end_turn(old, "ok")
    session.response_turn = old
    service = module.ParakeetSTT(config, telemetry=session)
    observer = CascadeObserver(session)
    start, stop = VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame(stop_secs=0.6)
    await service._handle_user_started_speaking(start)
    await service._handle_user_stopped_speaking(stop)
    audio, number = service._segment_queue.get_nowait()
    service._transcribing_segment = number
    new = session.current
    async def transcribe(*args, **kwargs):
        return "not for retained logs"
    monkeypatch.setattr(module, "transcribe_pcm", transcribe)
    frames = [frame async for frame in service.run_stt(audio)]
    assert frames[0].local_voice_turn is new
    # Model completion beats the delayed worker observer queue.
    await observer.on_push_frame(SimpleNamespace(frame=start))
    await observer.on_push_frame(SimpleNamespace(frame=stop))
    assert session.current is new and session.response_turn is new and new is not old
    assert len(session.turns) == 1
    session.close()
    await observer.cleanup()
    sink.flush()
    item = next(r for r in records(path) if r["record_type"] == "stage")
    assert item["turn_id"] == new.id and item["turn_id"] != old.id
    assert not any(r["status"] == "superseded" for r in records(path))


async def test_cancelled_tts_is_not_successful_turn_or_span(telemetry):
    session, sink, path = telemetry
    turn = session.start_turn()
    turn.vad_stopped = time.monotonic()
    session.response_turn = turn
    request = session.request("tts", "phonon", turn=turn)
    observer = CascadeObserver(session)
    await observer.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
    request.finish("cancelled")
    await observer.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))
    session.close()
    await observer.cleanup()
    sink.flush()
    items = records(path)
    assert next(r for r in items if r["record_type"] == "turn")["status"] == "cancelled"
    request_span = next(r for r in items if r["record_type"] == "span" and r["request_id"] == request.id)
    assert request_span["status"] == "cancelled"
    turn_span = next(r for r in items if r["record_type"] == "span" and r["stage"] == "turn")
    assert turn_span["status"] == "cancelled"
