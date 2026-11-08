import asyncio
from dataclasses import replace

import httpx
import pytest

from local_voice.llm import keep_ollama_warm, refresh_ollama
from test_adapters import config


async def test_larger_checkpoint_requires_explicit_project_override(config, monkeypatch):
    from local_voice import llm

    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": config.ollama_source, "size": 12_187_167_872}]})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Ready."}}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": config.ollama_model, "context_length": 4096}]})
        return httpx.Response(200, json={"status": "success"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: client_type(**kw, transport=httpx.MockTransport(respond)))
    with pytest.raises(ValueError, match="LLM_MAX_CHECKPOINT_BYTES"):
        await llm.prepare_ollama(config)
    assert calls == ["/api/tags"]  # Fail before creating or loading anything.
    details = await llm.prepare_ollama(replace(config, llm_max_checkpoint_bytes=16_000_000_000))
    assert details["llm_bytes"] == 12_187_167_872
    assert details["llm_context"] == 4096


async def test_evicted_project_model_is_not_ready_until_rewarmed(config):
    state = {"ready": True, "phase": "ready"}
    generating = asyncio.Event()
    release = asyncio.Event()
    calls = []
    loaded = {"name": config.ollama_model + ":latest", "context_length": 4096}

    async def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/generate":
            import json
            assert json.loads(request.content) == {"model": config.ollama_model, "keep_alive": "5m", "stream": False}
            generating.set()
            await release.wait()
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json={"models": [loaded] if release.is_set() else []})

    async with httpx.AsyncClient(base_url=config.ollama_url, transport=httpx.MockTransport(respond)) as client:
        task = asyncio.create_task(refresh_ollama(config, state, client))
        await generating.wait()
        assert state["ready"] is False and state["phase"] == "warming"
        release.set()
        await task
    assert state["ready"] is True and state["phase"] == "ready"
    assert calls == [("GET", "/api/ps"), ("POST", "/api/generate"), ("GET", "/api/ps")]


async def test_keepalive_failure_can_recover_and_has_cancelable_lifespan(config, monkeypatch):
    state = {"ready": True, "phase": "ready"}
    failed = True

    def respond(request):
        if failed:
            return httpx.Response(503)
        return httpx.Response(200, json={"models": [{"name": config.ollama_model, "context_length": 4096}]})

    async with httpx.AsyncClient(base_url=config.ollama_url, transport=httpx.MockTransport(respond)) as client:
        await refresh_ollama(config, state, client)
        assert not state["ready"] and state["phase"] == "error"
        failed = False
        await refresh_ollama(config, state, client)
        assert state["ready"] and state["error"] is None

    # App shutdown must cancel the sleeping maintenance task without another
    # refresh or a keep_alive=0 unload of any project/unrelated model.
    import local_voice.llm as module
    refreshed = asyncio.Event()

    async def refresh(*args):
        refreshed.set()

    monkeypatch.setattr(module, "refresh_ollama", refresh)
    task = asyncio.create_task(keep_ollama_warm(config, state, interval=0.01))
    await refreshed.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled()
