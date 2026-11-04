import httpx

from local_voice.server import create_app
from test_adapters import config

ORIGIN = {"origin": "http://localhost:3000"}


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
