"""Loopback-only signaling with one active browser and real model readiness."""
import asyncio
from contextlib import asynccontextmanager
import sys
import time

import numpy as np
import soxr

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from pipecat.transports.smallwebrtc.request_handler import (
    ConnectionMode, IceCandidate, SmallWebRTCPatchRequest,
    SmallWebRTCRequest, SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.connection import IceServer

from .config import Config
from .llm import keep_ollama_warm, prepare_ollama
from .pipeline import build_worker, run_worker
from .tts import PhononEngine
from .stt import transcribe_pcm
from .resident_stt import ResidentParakeet
from .telemetry import JsonlSink, SessionMetrics, configure_tracing, close_tracing, timestamp


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=1, max_length=200_000)
    type: str = "offer"
    pc_id: str | None = Field(default=None, max_length=100)
    restart_pc: bool = False
    request_data: dict | None = None


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: str = Field(max_length=2000)
    sdp_mid: str = Field(max_length=50)
    sdp_mline_index: int = Field(ge=0, le=10)


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pc_id: str = Field(min_length=1, max_length=100)
    candidates: list[Candidate] = Field(max_length=64)


def create_app(config=None, *, initialize=True):
    config = config or Config.from_env()
    handler = SmallWebRTCRequestHandler(
        ice_servers=[IceServer(**server) for server in config.ice_servers],
        connection_mode=ConnectionMode.SINGLE,
    )
    lock = asyncio.Lock()
    state = {"phase": "initializing", "ready": False, "busy": False, "error": None}
    active = {"connection": None, "task": None, "worker": None, "telemetry": None}
    sink = None
    engine = PhononEngine(config.phonon_model)
    stt_engine = ResidentParakeet(config) if config.stt_backend == "parakeet" and config.parakeet_mode == "resident" else None

    async def warmup():
        try:
            config.validate()
            stt_details = {}
            if stt_engine:
                info = await stt_engine.load()
                stt_details = {"stt_worker_startup_ms": info["startup_ms"], "stt_model_load_ms": info["model_load_ms"]}
            await engine.load()
            details = await prepare_ollama(config)
            details.update(stt_details)
            # Ready must include genuine GPU STT and PCM synthesis, not just files.
            pcm = bytearray()
            async for chunk in engine.stream("Ready."):
                pcm.extend(chunk)
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            normalized = (soxr.resample(samples, 24000, 16000) * 32767).astype("<i2").tobytes()
            started = time.monotonic()
            if not await transcribe_pcm(config, normalized, **({"engine": stt_engine} if stt_engine else {})):
                raise ValueError("Selected STT readiness check returned no speech")
            details["stt_preflight_seconds"] = round(time.monotonic() - started, 3)
            state.update(ready=True, phase="ready", **details)
            if sink:
                sink.emit({"schema_version": 1, "record_type": "session", "timestamp": timestamp(),
                           "session_id": None, "turn_id": None, "request_id": None, "stage": "startup",
                           "model": None, "status": "ready", "metrics": {
                               "warmup_ms": details["warmup_seconds"] * 1000,
                               "stt_preflight_ms": details["stt_preflight_seconds"] * 1000,
                               **stt_details}})
            logger.info("Models warmed and ready; no conversation recording enabled")
            await keep_ollama_warm(config, state)
        except Exception as error:
            logger.error("Model preflight failed: {}", type(error).__name__)
            state.update(phase="error", error=str(error))
            if sink:
                sink.emit({"schema_version": 1, "record_type": "session", "timestamp": timestamp(),
                           "session_id": None, "turn_id": None, "request_id": None,
                           "stage": "startup", "model": None, "status": "error", "metrics": {}})

    @asynccontextmanager
    async def lifespan(app):
        nonlocal sink
        sink = JsonlSink(config.metrics_dir, max_bytes=config.metrics_max_bytes, backups=config.metrics_backups)
        try:
            configure_tracing(sink)
        except Exception:
            await asyncio.to_thread(sink.close)
            raise
        warming = asyncio.create_task(warmup()) if initialize else None
        try:
            yield
        finally:
            if warming and not warming.done():
                warming.cancel()
                await asyncio.gather(warming, return_exceptions=True)
            if active["worker"]:
                await active["worker"].cancel(reason="server shutdown")
            await handler.close()
            if active["task"]:
                await asyncio.gather(active["task"], return_exceptions=True)
            await engine.close()
            if stt_engine:
                await stt_engine.close()
            if active["telemetry"]:
                active["telemetry"].close("disconnected")
            await asyncio.to_thread(close_tracing, sink)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.voice_state = state
    app.state.active = active
    app.state.handler = handler

    @app.middleware("http")
    async def limits(request, call_next):
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin not in config.allowed_origins:
                return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
            # Reject chunked/oversized requests too, not only Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 256_000:
                    return JSONResponse({"detail": "Signaling request is too large"}, status_code=413)
            request._body = bytes(body)  # Starlette's cached body for downstream parsing.
        return await call_next(request)

    @app.get("/health")
    async def health():
        runtime_state = {}
        if stt_engine and state["ready"] and not stt_engine.ready:
            stt_engine.request_recovery()
            runtime_state = {"ready": False, "phase": "warming" if stt_engine.recovery_task and not stt_engine.recovery_task.done() else "error",
                             "error": "The local transcription worker is unavailable; checking readiness."}
        return {**state, "ice_servers": config.ice_servers,
                "stt_runtime": config.parakeet_mode if config.stt_backend == "parakeet" else "cli",
                "ready": state["ready"] and (stt_engine.ready if stt_engine else True),
                **runtime_state,
                "telemetry": sink.health() if sink else {"active": False}, "models": {"stt": config.stt_model_id, "stt_device": config.stt_device, "llm": config.ollama_model, "tts": "Phonon 31M English CPU"}}

    async def attached(connection):
        backend = getattr(config, "stt_backend", "parakeet")
        telemetry = SessionMetrics(sink, config.vad_stop_seconds, configuration={
            "stt_backend": backend,
            "stt_mode": config.parakeet_mode if backend == "parakeet" else "cli",
            "stt_threads": config.stt_threads,
        }) if sink else None
        try:
            worker = build_worker(connection, config, engine, telemetry, **({"stt_engine": stt_engine} if stt_engine else {}))
        except BaseException:
            if telemetry:
                telemetry.close("error")
            raise
        active.update(connection=connection, worker=worker, telemetry=telemetry)

        async def run():
            outcome = "disconnected"
            try:
                await run_worker(worker)
            except asyncio.CancelledError:
                raise
            except Exception:
                outcome = "error"
                logger.exception("Voice session failed")
            finally:
                engine.interrupt()
                await connection.disconnect()
                if telemetry:
                    telemetry.close(outcome)
                active.update(connection=None, task=None, worker=None, telemetry=None)
                state["busy"] = False
        active["task"] = asyncio.create_task(run())

    @app.post("/api/offer")
    async def offer(body: Offer):
        if body.type != "offer":
            raise HTTPException(400, "Expected an SDP offer")
        if not state["ready"]:
            raise HTTPException(503, state["error"] or "Local models are warming up")
        if stt_engine and not stt_engine.ready:
            stt_engine.request_recovery()
            raise HTTPException(503, "The local transcription worker is warming or unavailable")
        async with lock:
            current = active["connection"]
            if state["busy"] and (not current or body.pc_id != current.pc_id):
                raise HTTPException(409, "Another browser is already using the voice agent")
            if body.pc_id and (not current or body.pc_id != current.pc_id):
                raise HTTPException(404, "This voice session has ended; reconnect")
            state["busy"] = True
            try:
                answer = await handler.handle_web_request(SmallWebRTCRequest(**body.model_dump()), attached)
                if not active["connection"]:
                    await handler.close()
                    raise HTTPException(500, "The local voice pipeline could not start")
                return answer
            except Exception:
                state["busy"] = bool(active["connection"])
                raise

    @app.patch("/api/offer")
    async def patch(body: Patch):
        await handler.handle_patch_request(SmallWebRTCPatchRequest(body.pc_id, [IceCandidate(**x.model_dump()) for x in body.candidates]))
        return {"ok": True}

    @app.delete("/api/offer")
    async def stop(request: Request):
        body = await request.json()
        connection = active["connection"]
        if connection and body.get("pc_id") == connection.pc_id:
            engine.interrupt()
            await active["worker"].cancel(reason="browser stopped")
            await connection.disconnect()
        return {"ok": True}

    return app


def main():
    import uvicorn
    # Library DEBUG logs include speech text/SDP; keep default logs metadata-only.
    logger.remove()
    logger.add(sys.stderr, level="INFO", filter=lambda record: not record["name"].startswith("pipecat"))
    uvicorn.run(create_app(), host="127.0.0.1", port=7860, log_level="warning")


if __name__ == "__main__":
    main()
