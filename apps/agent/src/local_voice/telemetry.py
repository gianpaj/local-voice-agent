"""Metadata-only local cascade telemetry. Nothing here exports over a network.

Times use the server's monotonic clock. A generated PCM chunk is NOT browser
playback, and configured VAD silence is not a measured acoustic endpoint.
"""
import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode
from pipecat.frames.frames import (
    BotStartedSpeakingFrame, BotStoppedSpeakingFrame, CancelFrame, EndFrame,
    InterruptionFrame, TTSAudioRawFrame,
)
from pipecat.observers.base_observer import BaseObserver


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def elapsed_ms(start, end=None):
    return None if start is None else max(0.0, ((time.monotonic() if end is None else end) - start) * 1000)


class JsonlSink:
    """Bounded nonblocking producer queue; only its owner thread touches disk."""
    def __init__(self, directory, *, max_bytes=10_000_000, backups=5, capacity=2048):
        self.directory = Path(directory)
        self.max_bytes, self.backups = max_bytes, backups
        self.queue = queue.Queue(maxsize=capacity)
        self.dropped = self.write_errors = self.written = 0
        self.closed = False
        self._state_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True, name="voice-metrics-writer")
        self.thread.start()

    def emit(self, record):
        try:
            # The caller has built an allowlisted record, never a raw frame/span.
            line = json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
            with self._state_lock:
                if self.closed:
                    self.dropped += 1
                    return
                self.queue.put_nowait(line)
        except (queue.Full, ValueError, TypeError):
            self.dropped += 1

    def health(self):
        return {"queued": self.queue.qsize(), "written": self.written,
                "dropped": self.dropped, "write_errors": self.write_errors}

    def _run(self):
        while True:
            line = self.queue.get()
            try:
                if line is None:
                    return
                self._write(line)
                self.written += 1
            except OSError:
                self.write_errors += 1
            finally:
                self.queue.task_done()

    def _write(self, line):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / "metrics.jsonl"
        if target.exists() and target.stat().st_size + len(line.encode()) > self.max_bytes:
            oldest = self.directory / f"metrics.jsonl.{self.backups}"
            oldest.unlink(missing_ok=True)
            for number in range(self.backups - 1, 0, -1):
                source = self.directory / f"metrics.jsonl.{number}"
                if source.exists():
                    source.replace(self.directory / f"metrics.jsonl.{number + 1}")
            target.replace(self.directory / "metrics.jsonl.1")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            output.write(line)

    def flush(self):
        self.queue.join()

    def close(self):
        with self._state_lock:
            if self.closed:
                return
            self.closed = True
        self.queue.put(None)  # Called off the audio/event loop during shutdown.
        self.thread.join()


class SafeSpanExporter(SpanExporter):
    """Never serialize raw attributes, events, links, resource or status text."""
    numeric_attributes = frozenset({
        "metrics.ttfb", "metrics.character_count", "turn.number", "turn.duration",
        "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
        "gen_ai.usage.reasoning.output_tokens", "turn.was_interrupted",
    })

    def __init__(self):
        self.sink = None
        self.sessions = OrderedDict()

    def register(self, trace_id, session_id):
        self.sessions[trace_id] = session_id
        while len(self.sessions) > 128:
            self.sessions.popitem(last=False)

    def export(self, spans):
        sink = self.sink
        if sink is None:
            return SpanExportResult.SUCCESS
        for span in spans:
            attrs = span.attributes or {}
            session_id = attrs.get("local.session_id") or attrs.get("conversation.id") or self.sessions.get(span.context.trace_id)
            try:
                session_id = str(uuid.UUID(session_id))
            except (ValueError, TypeError, AttributeError):
                continue  # Unrelated spans cannot enter this project's local logs.
            self.register(span.context.trace_id, session_id)
            metrics = {key.replace(".", "_"): int(value) if isinstance(value, bool) else value for key, value in attrs.items()
                       if key in self.numeric_attributes and isinstance(value, (int, float)) and math.isfinite(value)}
            metrics["total_ms"] = max(0, (span.end_time - span.start_time) / 1_000_000)
            kind = span.name if span.name in {"stt", "llm", "tts", "turn", "conversation"} else "operation"
            sink.emit({"schema_version": 1, "record_type": "span", "timestamp": timestamp(),
                       "session_id": session_id, "turn_id": valid_uuid(attrs.get("local.turn_id")),
                       "request_id": valid_uuid(attrs.get("local.request_id")), "stage": kind,
                       "model": None, "status": attrs.get("local.status") if attrs.get("local.status") in {
                           "ok", "cancelled", "error", "timeout", "empty", "interrupted", "disconnected", "superseded"
                       } else "error" if span.status.status_code == StatusCode.ERROR else "interrupted" if attrs.get("turn.was_interrupted") else "ok",
                       "trace_id": f"{span.context.trace_id:032x}", "span_id": f"{span.context.span_id:016x}",
                       "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
                       "metrics": metrics})
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def valid_uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


_exporter = SafeSpanExporter()
_provider = None


def configure_tracing(sink):
    """One provider per process, one active application sink per lifespan.

    Do not use OTEL_EXPORTER_OTLP_ENDPOINT or environment-based auto exporters.
    Installing a second/global exporter could leak native Pipecat text attributes.
    """
    global _provider
    if _provider is None:
        current = trace.get_tracer_provider()
        if not isinstance(current, trace.ProxyTracerProvider):
            raise RuntimeError("A preconfigured global tracer provider is not supported by local-only telemetry")
        _provider = TracerProvider(resource=Resource({"service.name": "local-voice-agent"}))
        _provider.add_span_processor(SimpleSpanProcessor(_exporter))
        trace.set_tracer_provider(_provider)
    if _exporter.sink is not None and _exporter.sink is not sink:
        raise RuntimeError("Only one local telemetry lifespan may be active per process")
    _exporter.sink = sink


def close_tracing(sink):
    if _provider:
        _provider.force_flush()
    if _exporter.sink is sink:
        _exporter.sink = None
    sink.flush()
    health = sink.health()
    sink.emit({"schema_version": 1, "record_type": "session", "timestamp": timestamp(),
               "session_id": None, "turn_id": None, "request_id": None, "stage": "telemetry",
               "model": None, "status": "shutdown", "metrics": {
                   "dropped_records": health["dropped"], "write_errors": health["write_errors"],
                   "written_records": health["written"]}})
    sink.close()


@dataclass
class Turn:
    id: str
    started: float
    span: object
    vad_stopped: float | None = None
    first_pcm: float | None = None
    bot_started: float | None = None
    endpointing_vad_reported_ms: float | None = None
    response_completed: bool = False
    status: str | None = None
    requests: list = field(default_factory=list)


class RequestMetrics:
    def __init__(self, session, turn, stage, model, metrics=None):
        self.session, self.turn, self.stage, self.model = session, turn, stage, model
        self.id = str(uuid.uuid4())
        self.started = time.monotonic()
        self.metrics = dict(metrics or {})
        self.finished = False
        self.status = None
        self.span = trace.get_tracer("local_voice").start_span(stage, context=trace.set_span_in_context(turn.span), attributes={
            "local.session_id": session.id, "local.turn_id": turn.id, "local.request_id": self.id,
        })
        turn.requests.append(self)
        turn.response_completed = False

    def finish(self, status):
        if self.finished:
            return
        self.finished = True
        self.status = status
        self.span.set_attribute("local.status", status)
        self.metrics["total_ms"] = elapsed_ms(self.started)
        if self.metrics.get("audio_seconds", 0) > 0:
            self.metrics["rtf"] = self.metrics["total_ms"] / 1000 / self.metrics["audio_seconds"]
        if status in {"error", "timeout"}:
            self.span.set_status(Status(StatusCode.ERROR))
        self.session.record("stage", self.turn, self.stage, self.model, status, self.metrics, self)
        self.span.end()


class SessionMetrics:
    def __init__(self, sink, endpointing_seconds, *, configuration=None):
        self.sink = sink
        supplied = configuration or {}
        # Categorical configuration is not a numeric metric. Never serialize
        # arbitrary config/env values (which may include paths or credentials).
        self.configuration = {
            "stt_backend": supplied.get("stt_backend") if supplied.get("stt_backend") in {"parakeet", "whistle"} else None,
            "stt_mode": supplied.get("stt_mode") if supplied.get("stt_mode") in {"cli", "resident"} else None,
            "stt_threads": supplied.get("stt_threads") if type(supplied.get("stt_threads")) is int and 1 <= supplied["stt_threads"] <= 64 else None,
        }
        self.id = str(uuid.uuid4())
        self.started = time.monotonic()
        self.endpointing_ms = endpointing_seconds * 1000
        self.span = trace.get_tracer("local_voice").start_span("conversation", attributes={"local.session_id": self.id})
        _exporter.register(self.span.get_span_context().trace_id, self.id)
        self.current = self.response_turn = None
        self.turns = []
        self.closed = False
        self._output_pending = deque(maxlen=16)
        self._server_speaking_turn = None
        self._last_server_event = None
        self.record("session", status="started", metrics={
            "sample_rate_in": 16000, "sample_rate_out": 24000, "llm_context_tokens": 4096,
            "llm_max_output_tokens": 128, "phonon_threads": 4,
            "endpointing_config_ms": self.endpointing_ms,
        })

    def record(self, record_type, turn=None, stage=None, model=None, status="ok", metrics=None, request=None):
        span = request.span if request else turn.span if turn else self.span
        context = span.get_span_context()
        self.sink.emit({"schema_version": 1, "record_type": record_type, "timestamp": timestamp(),
                       "session_id": self.id, "turn_id": turn.id if turn else None,
                       "request_id": request.id if request else None, "stage": stage, "model": model,
                       "status": status, "trace_id": f"{context.trace_id:032x}",
                       "span_id": f"{context.span_id:016x}", "metrics": metrics or {},
                       "configuration": self.configuration,
                       "runtime": {"pipecat_version": "1.15.0", "app_version": "0.1.0"}})

    def register_native(self, tracing_context):
        if tracing_context and tracing_context.get_conversation_context():
            context = trace.get_current_span(tracing_context.get_conversation_context()).get_span_context()
            _exporter.register(context.trace_id, self.id)

    def start_turn(self):
        # Retain only turns with live requests; completed history is on disk.
        self.turns[:] = [t for t in self.turns if t.status is None or any(not r.finished for r in t.requests)]
        if self.current and self.current.status is None:
            self.end_turn(self.current, "ok" if self.current.response_completed else "interrupted" if self.current.bot_started is not None else "superseded")
        turn_id = str(uuid.uuid4())
        span = trace.get_tracer("local_voice").start_span("turn", context=trace.set_span_in_context(self.span), attributes={
            "local.session_id": self.id, "local.turn_id": turn_id,
        })
        self.current = Turn(turn_id, time.monotonic(), span)
        self.turns.append(self.current)
        return self.current

    def request(self, stage, model, *, turn=None, metrics=None):
        turn = turn or self.response_turn or self.current or self.start_turn()
        return RequestMetrics(self, turn, stage, model, metrics)

    def generated_pcm(self, request):
        # A request retains its original turn even when a new user turn interrupts it.
        turn = request.turn
        if turn.status is None and turn.first_pcm is None:
            turn.first_pcm = time.monotonic()

    def note_output_frame(self, frame):
        """Inline transport input hook: remember the owner of queued PCM.

        TTS tags the original audio frame before its audio-context queue. This
        does not consult session.response_turn, which can already refer to the
        next answer by the time an older server speaking event is observed.
        """
        if isinstance(frame, InterruptionFrame):
            self._output_pending.clear()
        elif isinstance(frame, TTSAudioRawFrame):
            owner = getattr(frame, "local_voice_turn", None)
            if owner and owner.status is None and owner is not self._server_speaking_turn:
                if (not self._output_pending or self._output_pending[-1] is not owner) and len(self._output_pending) < 16:
                    self._output_pending.append(owner)

    def stamp_server_event(self, frame):
        """Inline output push hook: capture event time and immutable ownership.

        Pipecat broadcasts upstream/downstream siblings for the same event.
        Both must retain the same stamp without advancing the pending owner.
        """
        if not isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            return
        previous = self._last_server_event
        if previous and getattr(frame, "broadcast_sibling_id", None) == previous[0]:
            stamp = previous[1]
        else:
            if isinstance(frame, BotStartedSpeakingFrame):
                owner = self._server_speaking_turn
                if owner is None and self._output_pending:
                    owner = self._output_pending.popleft()
                self._server_speaking_turn = owner
            else:
                owner = self._server_speaking_turn
                self._server_speaking_turn = None
            observed_at = time.monotonic()
            stamp = (owner, observed_at)
            if owner and owner.status is None:
                if isinstance(frame, BotStartedSpeakingFrame):
                    if owner.bot_started is None:
                        owner.bot_started = observed_at
                    owner.response_completed = False
                elif owner.bot_started is not None and all(r.finished for r in owner.requests):
                    owner.response_completed = True
        frame.local_voice_server_event = stamp
        self._last_server_event = (frame.id, stamp)

    def turn_outcome(self, turn, proposed="ok"):
        outcomes = {r.status for r in turn.requests if r.finished}
        for outcome in ("error", "timeout", "cancelled", "empty"):
            if outcome in outcomes:
                return "interrupted" if outcome == "cancelled" and proposed == "interrupted" else outcome
        return proposed

    def end_turn(self, turn, status):
        if turn.status is not None:
            return
        status = self.turn_outcome(turn, status)
        turn.status = status
        turn.span.set_attribute("local.status", status)
        if status in {"error", "timeout"}:
            turn.span.set_status(Status(StatusCode.ERROR))
        metrics = {"agent_ttfa_ms": elapsed_ms(turn.vad_stopped, turn.first_pcm) if turn.first_pcm is not None else None,
                   "server_speaking_ms": elapsed_ms(turn.vad_stopped, turn.bot_started) if turn.bot_started is not None else None,
                   "endpointing_config_ms": self.endpointing_ms,
                   "endpointing_observed_ms": None,
                   "endpointing_vad_reported_ms": turn.endpointing_vad_reported_ms,
                   "turn_duration_ms": elapsed_ms(turn.started),
                   "browser_playback_ms": None, "acoustic_ttfa_ms": None}
        self.record("turn", turn, status=status, metrics=metrics)
        turn.span.end()

    def close(self, status="disconnected"):
        if self.closed:
            return
        self.closed = True
        for turn in self.turns:
            for request in turn.requests:
                if not request.finished:
                    request.finish(status)
            if turn.status is None:
                self.end_turn(turn, "ok" if turn.response_completed else status)
        health = self.sink.health()
        self.record("session", status=status, metrics={"session_duration_ms": elapsed_ms(self.started),
                    "dropped_records": health["dropped"], "write_errors": health["write_errors"]})
        self.span.set_attribute("local.status", status)
        self.span.end()


class CascadeObserver(BaseObserver):
    def __init__(self, session):
        super().__init__(observe_every_push=False)
        self.session = session
        self._end_timer = None

    def _cancel_end(self):
        if self._end_timer:
            self._end_timer.cancel()
            self._end_timer = None

    async def cleanup(self):
        self._cancel_end()
        await super().cleanup()

    async def on_push_frame(self, data):
        frame, session = data.frame, self.session
        # VAD ownership is updated inline by ParakeetSTT before its segment
        # enters the async queue. Worker observers run asynchronously and must
        # never choose a segment's owner or reset it after transcription starts.
        if isinstance(frame, BotStartedSpeakingFrame):
            turn, observed_at = getattr(frame, "local_voice_server_event", (None, None))
            if turn and turn.status is None:
                self._cancel_end()
                if turn.bot_started is None:
                    turn.bot_started = observed_at
                turn.response_completed = False
        elif isinstance(frame, BotStoppedSpeakingFrame):
            # There can be several sentences/audio contexts per turn. Finalize
            # only once LLM/TTS have completed, otherwise a later sentence could
            # arrive after an early server-speaking stop.
            turn, _observed_at = getattr(frame, "local_voice_server_event", (None, None))
            if turn and turn.status is None and turn.bot_started is not None and all(r.finished for r in turn.requests):
                turn.response_completed = True
                self._cancel_end()
                self._end_timer = asyncio.get_running_loop().call_later(2.5, session.end_turn, turn, "ok")
        elif isinstance(frame, InterruptionFrame):
            turn = session.response_turn
            if turn and turn is not session.current and turn.status is None:
                session.end_turn(turn, "interrupted")
        elif isinstance(frame, (CancelFrame, EndFrame)):
            # Services finish cancelled requests during worker cleanup; server
            # calls session.close afterwards so their final durations are kept.
            pass
