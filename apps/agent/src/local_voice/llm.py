"""Local Ollama setup, honest readiness, and bounded Pipecat requests."""
import asyncio
import time

import httpx
from pipecat.services.ollama.llm import OLLamaLLMService

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


class BoundedOllama(OLLamaLLMService):
    def __init__(self, config: Config):
        super().__init__(
            base_url=config.ollama_url + "/v1", api_key="local-only",
            settings=self.Settings(model=config.ollama_model, max_tokens=128, system_instruction=SYSTEM_PROMPT,
                                   extra={"reasoning_effort": "none"}),
            retry_on_timeout=False,
        )

    async def _process_context(self, context):
        started = time.monotonic()
        # System instruction is set on the service, never dropped by history bounds.
        messages = context.messages
        if len(messages) > 12:
            messages[:] = messages[-11:]
        try:
            async with asyncio.timeout(30):
                await super()._process_context(context)
        except TimeoutError:
            await self.push_error("The local language model took too long. Please try again.")
        finally:
            from loguru import logger
            logger.info("stage=llm seconds={:.3f}", time.monotonic() - started)
