"""Model-free check: full private histories, PCM-only peer inputs, temporary closing."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location("two_agent_recording", Path(__file__).with_name("conversation.py"))
conversation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = conversation
spec.loader.exec_module(conversation)


class DialogueContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_histories_accumulate_without_peer_generated_text_backchannel(self):
        generated = iter(["RAW_VICKY_1", "RAW_JOHN_1", "RAW_VICKY_2", "RAW_JOHN_2"])
        requests = []

        async def post(url, *, json):
            requests.append(copy.deepcopy(json))
            text = next(generated)
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"message": {"content": text}})

        client = SimpleNamespace(post=post)
        config = SimpleNamespace(ollama_url="http://127.0.0.1:11434", ollama_model="test-local")
        vicky_pcm, john_pcm = b"\1\0" * 240, b"\2\0" * 240
        vicky = conversation.Agent("Vicky", "Freya", "vicky-model")
        john = conversation.Agent("John", "Easton", "john-model")
        heard = AsyncMock(side_effect=["HEARD_VICKY_1", "HEARD_JOHN_1", "HEARD_VICKY_2"])
        engine = object()

        def synth(model, text):
            return (vicky_pcm if model == "vicky-model" else john_pcm,
                    {"first_pcm_ms": 1.0, "total_ms": 2.0})

        with patch.object(conversation, "transcribe", heard), patch.object(conversation, "synth", synth):
            a1, _ = await vicky.respond(None, engine=engine, client=client, config=config)
            b1, b_record = await john.respond(a1, engine=engine, client=client, config=config)
            a2, a_record = await vicky.respond(b1, engine=engine, client=client, config=config)
            await john.respond(a2, engine=engine, client=client, config=config, closing=True)

        self.assertEqual([call.args for call in heard.await_args_list],
                         [(engine, vicky_pcm), (engine, john_pcm), (engine, vicky_pcm)])
        self.assertIsNot(vicky.history, john.history)
        self.assertIn("You are Vicky", requests[0]["messages"][0]["content"])
        self.assertIn("You are John", requests[1]["messages"][0]["content"])
        self.assertEqual([m["role"] for m in requests[2]["messages"]],
                         ["system", "user", "assistant", "user"])
        self.assertEqual(requests[2]["messages"][2:], [
            {"role": "assistant", "content": "RAW_VICKY_1"},
            {"role": "user", "content": "HEARD_JOHN_1"},
        ])
        self.assertEqual(requests[1]["messages"][1], {"role": "user", "content": "HEARD_VICKY_1"})
        self.assertNotIn("RAW_VICKY_1", str(requests[1]["messages"]))
        self.assertNotIn("RAW_JOHN_1", str(requests[2]["messages"]))
        self.assertEqual([m["role"] for m in requests[3]["messages"]],
                         ["system", "user", "assistant", "user", "system"])
        self.assertIn("conversation is ending", requests[3]["messages"][-1]["content"])
        self.assertEqual(sum(m["role"] == "system" for m in john.history), 1)
        self.assertEqual(requests[2]["options"]["num_ctx"], 4096)
        self.assertEqual(b_record["input_pcm_sha256"], conversation.digest(vicky_pcm))
        self.assertEqual(a_record["input_pcm_sha256"], conversation.digest(john_pcm))


if __name__ == "__main__":
    unittest.main()
