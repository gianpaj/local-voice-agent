from dataclasses import replace

import httpx
import pytest

from local_voice.server import create_app
from test_adapters import config


async def test_private_default_and_opt_in_stun_agree_at_both_endpoints(config):
    for stun_url in ("", "stun:stun.cloudflare.com:3478"):
        selected = replace(config, stun_url=stun_url)
        selected.validate()
        app = create_app(selected, initialize=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            health = (await client.get("/health")).json()
        expected = [{"urls": stun_url}] if stun_url else []
        assert health["ice_servers"] == expected
        assert [server.urls for server in app.state.handler._ice_servers] == ([stun_url] if stun_url else [])
        assert not health["ready"]


@pytest.mark.parametrize("url", [
    "https://stun.cloudflare.com", "turn:example.com", "stun:user:password@example.com",
    "stun:", "stun:example.com:70000", "stun:example.com/path",
    "stun:example.com?secret=bad", "stun:example.com\n",
])
def test_stun_setting_cannot_smuggle_credentials_or_other_protocols(config, url):
    with pytest.raises(ValueError, match="WEBRTC_STUN_URL"):
        replace(config, stun_url=url).validate()
