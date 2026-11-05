import httpx
from dataclasses import replace

from local_voice.server import create_app
from test_adapters import config

ORIGIN = {"origin": "http://localhost:3000"}


async def test_whistle_does_not_construct_or_require_unused_resident_parakeet(config, monkeypatch):
    def unused(*args, **kwargs):
        raise AssertionError("Whistle must not load resident Parakeet")
    monkeypatch.setattr("local_voice.server.ResidentParakeet", unused)
    app = create_app(replace(config, stt_backend="whistle", parakeet_mode="resident"), initialize=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = (await client.get("/health")).json()
        assert response["stt_runtime"] == "cli"
        assert response["models"]["stt_device"] == "cpu"


async def test_readiness_origin_size_validation_and_busy(config):
    app = create_app(config, initialize=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/health")
        assert health.status_code == 200 and not health.json()["ready"]
        assert (await client.post("/api/offer", json={"sdp": "x"})).status_code == 403
        assert (await client.post("/api/offer", headers=ORIGIN, json={"sdp": "x"})).status_code == 503
        assert (await client.post("/api/offer", headers=ORIGIN, content="x" * 256001)).status_code == 413
        app.state.voice_state.update(ready=True, busy=True)
        assert (await client.post("/api/offer", headers=ORIGIN, json={"sdp": "x"})).status_code == 409
        assert (await client.post("/api/offer", headers=ORIGIN, json={"sdp": "x", "type": "answer"})).status_code == 400
        assert (await client.patch("/api/offer", headers=ORIGIN, json={"pc_id": "missing", "candidates": []})).status_code == 404
        assert (await client.post("/api/offer", headers=ORIGIN, json={"sdp": "x", "unknown": True})).status_code == 422
