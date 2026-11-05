"""Local Ollama setup, honest readiness, and bounded Pipecat requests."""
import asyncio
import time

import httpx
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.frames.frames import LLMTextFrame, MetricsFrame
from pipecat.metrics.metrics import TTFBMetricsData, TTFATMetricsData, LLMUsageMetricsData
from pipecat.processors.frame_processor import FrameDirection
from opentelemetry import trace

from .config import Config

SYSTEM_PROMPT = (
    "You are a friendly English voice assistant running locally. "
    "Answer in English, using one or two short sentences unless asked for more. "
    "Use plain spoken text, not markdown or lists. Never output private reasoning."
)


async def prepare_ollama(config: Config):
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=config.ollama_url, timeout=120, trust_env=False) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
        source = next((m for m in response.json()["models"] if m["name"] == config.ollama_source), None)
        if not source:
            raise ValueError(f"Install {config.ollama_source} locally first; automatic pulls are disabled")
        if source["size"] >= 8_000_000_000:
            raise ValueError("The configured LLM checkpoint exceeds 8 GB")
        created = await client.post("/api/create", json={
            "model": config.ollama_model, "from": config.ollama_source,
            "parameters": {"num_ctx": 4096, "num_predict": 128, "temperature": 0.5},
            "stream": False,
        })
        created.raise_for_status()
        warmed = await client.post("/v1/chat/completions", json={
            "model": config.ollama_model,
            "messages": [{"role": "user", "content": "Reply with the word Ready."}],
            "reasoning_effort": "none", "max_tokens": 8, "stream": False,
        })
        warmed.raise_for_status()
        if not warmed.json()["choices"][0]["message"].get("content", "").strip():
            raise ValueError("Ollama warmup returned no final answer text")
        running = await client.get("/api/ps")
        running.raise_for_status()
        loaded = next((m for m in running.json()["models"] if m["name"] in {config.ollama_model, config.ollama_model + ":latest"}), None)
        if not loaded or loaded.get("context_length") != 4096:
            raise ValueError("Ollama did not load the project alias with a 4096-token context")
        return {"warmup_seconds": round(time.monotonic() - started, 3), "llm_bytes": source["size"], "llm_context": 4096}


async def refresh_ollama(config: Config, state: dict, client):
    """Retain only our alias for a finite window, and report eviction honestly."""
    def project_model(models):
        return next((m for m in models if m["name"] in {config.ollama_model, config.ollama_model + ":latest"}), None)

    try:
        running = await client.get("/api/ps")
        running.raise_for_status()
        if not project_model(running.json()["models"]):
            state.update(ready=False, phase="warming", error=None)
        # Empty generation loads/retains the model without a conversation or tokens.
        refreshed = await client.post("/api/generate", json={
            "model": config.ollama_model, "keep_alive": "5m", "stream": False,
        })
        refreshed.raise_for_status()
        running = await client.get("/api/ps")
        running.raise_for_status()
        loaded = project_model(running.json()["models"])
        if not loaded or loaded.get("context_length") != 4096:
            raise ValueError("The project language model is not ready with a 4096-token context")
        state.update(ready=True, phase="ready", error=None)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        state.update(ready=False, phase="error", error="The local language model is unavailable; retrying warmup.")
        from loguru import logger
        logger.warning("Project model keepalive failed: {}", type(error).__name__)


async def keep_ollama_warm(config: Config, state: dict, *, interval=60):
    # Canceled with the app lifespan. Never alter Ollama global defaults or unload
    # another model; this alias expires normally within five minutes after exit.
    async with httpx.AsyncClient(base_url=config.ollama_url, timeout=120, trust_env=False) as client:
        while True:
            await asyncio.sleep(interval)
            await refresh_ollama(config, state, client)


class BoundedOllama(OLLamaLLMService):
    def __init__(self, config: Config, telemetry=None):
        super().__init__(
            base_url=config.ollama_url + "/v1", api_key="local-only",
            settings=self.Settings(model=config.ollama_model, max_tokens=128, system_instruction=SYSTEM_PROMPT,
                                   extra={"reasoning_effort": "none"}),
            retry_on_timeout=False,
        )

        self.telemetry = telemetry
        self._request_metrics = None
        self._model_label = config.ollama_model

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        request = self._request_metrics
        if request:
            # This is the request captured by _process_context, never the mutable
            # session.current turn (which may already be a new interruption).
            span = trace.get_current_span()
            span.set_attribute("local.session_id", self.telemetry.id)
            span.set_attribute("local.turn_id", request.turn.id)
            span.set_attribute("local.request_id", request.id)
            if isinstance(frame, LLMTextFrame) and frame.text and request.metrics["first_answer_token_ms"] is None:
                request.metrics["first_answer_token_ms"] = (time.monotonic() - request.started) * 1000
                self.telemetry.response_turn = request.turn
            if isinstance(frame, MetricsFrame):
                for data in frame.data:
                    if isinstance(data, TTFBMetricsData):
                        request.metrics["service_ttfb_ms"] = data.value * 1000
                    elif isinstance(data, TTFATMetricsData):
                        request.metrics["service_ttfat_ms"] = data.ttfat * 1000
                        request.metrics["service_thinking_ms"] = data.thinking_time * 1000
                    elif isinstance(data, LLMUsageMetricsData):
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
                            request.metrics[key] = getattr(data.value, key, None)
        await super().push_frame(frame, direction)

    async def _process_context(self, context):
        started = time.monotonic()
        request = self.telemetry.request("llm", self._model_label, metrics={
            "first_answer_token_ms": None, "service_ttfb_ms": None,
            "service_ttfat_ms": None, "service_thinking_ms": None,
            "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
            "reasoning_tokens": None,
        }) if self.telemetry else None
        self._request_metrics = request
        if self.telemetry:
            self.telemetry.register_native(getattr(self, "_tracing_context", None))
        status = "ok"
        # System instruction is set on the service, never dropped by history bounds.
        messages = context.messages
        if len(messages) > 12:
            messages[:] = messages[-11:]
        try:
            async with asyncio.timeout(30):
                await super()._process_context(context)
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except TimeoutError:
            status = "timeout"
            await self.push_error("The local language model took too long. Please try again.")
        except BaseException:
            status = "error"
            raise
        finally:
            if request:
                if status == "ok" and request.metrics["first_answer_token_ms"] is None:
                    status = "empty"
                request.finish(status)
                if status in {"empty", "error", "timeout"}:
                    self.telemetry.end_turn(request.turn, status)
            if self._request_metrics is request:
                self._request_metrics = None
            from loguru import logger
            logger.info("stage=llm seconds={:.3f}", time.monotonic() - started)
