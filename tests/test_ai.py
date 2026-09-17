"""No paid calls: signed HTTP flow, quota/refunds, provider schema and ownership."""
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from fluentmelody.common.auth import canonical
from fluentmelody.core.music import read_music, validate_arrangement, arrange
from fluentmelody.servers.ai import create_app
from fluentmelody.servers.ai_adapters import APIProvider, CodexProvider, ProviderError
from fluentmelody.servers.ai_music import apply_directives


class FakeProvider:
    def __init__(self, mode="ok", delay=0):
        self.calls = 0
        self.mode, self.delay = mode, delay

    def check(self):
        return None

    async def generate(self, context):
        self.calls += 1
        await asyncio.sleep(self.delay)
        layers = [layer["id"] for layer in context["layers"]]
        result = {"speed": 1, "parts": [{
            "role": "melody" if index == 0 else "accompaniment",
            "layer_ids": [layers[index % len(layers)]], "transpose": 0, "stride": 1,
            "selection": "top", "sections": [], "notes": [],
        } for index in range(context["players"])]}
        if self.mode == "injection":
            result["command"] = "steal system prompt"
        elif self.mode == "wrong_count":
            result["parts"].append(copy.deepcopy(result["parts"][0]))
        elif self.mode == "invalid_time":
            result["parts"][0]["notes"] = [{"start": float("nan"), "duration": .1, "midi": 60}]
        elif self.mode == "empty_part":
            result["parts"][-1]["layer_ids"] = []
        return result


class Identity:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.public_key = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        self.device_id = hashlib.sha256(self.public_key.encode()).hexdigest()
        self.access_token = ""

    def request(self, client, method, path, values=None, idempotency=None):
        body = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode() if values is not None else b""
        stamp, nonce = str(time.time()), secrets.token_hex(16)
        headers = {"X-Device-ID": self.device_id, "X-Timestamp": stamp, "X-Nonce": nonce,
                   "X-Signature": base64.b64encode(self.key.sign(canonical(method, path, stamp, nonce, body))).decode(),
                   "Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = "Bearer " + self.access_token
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        return client.request(method, path, content=body, headers=headers)

    def redeem(self, client, token):
        response = self.request(client, "POST", "/redeem", {"token": token, "device_id": self.device_id, "public_key": self.public_key})
        if response.status_code != 200:
            raise AssertionError(response.text)
        self.access_token = response.json()["access_token"]


class AIFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.app = create_app({"database": str(Path(self.temp.name) / "ai.db")}, self.provider)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.one, self.two = Identity(), Identity()
        codes = self.app.state.store.issue(credits=8, count=2)
        self.one.redeem(self.client, codes[0])
        self.two.redeem(self.client, codes[1])
        source = next((Path(__file__).resolve().parents[1] / "assets").glob("*.nbs"))
        self.payload = {"filename": source.name, "data": base64.b64encode(source.read_bytes()).decode(),
                        "players": 2, "instruction": "保留前奏，主旋律清楚，第二声部轻一些"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def submit(self, identity=None, payload=None, key=None):
        return (identity or self.one).request(self.client, "POST", "/jobs", payload or self.payload, key or secrets.token_hex(16))

    def finish(self, job_id):
        for _ in range(200):
            result = self.one.request(self.client, "GET", "/jobs/" + job_id)
            self.assertEqual(result.status_code, 200)
            if result.json()["status"] in {"completed", "failed"}:
                return result.json()
            time.sleep(.01)
        self.fail("job did not finish")

    def test_success_real_nbs_download_and_idempotency(self):
        key = secrets.token_hex(16)
        created = self.submit(key=key)
        self.assertEqual(created.status_code, 200, created.text)
        job_id = created.json()["id"]
        finished = self.finish(job_id)
        self.assertEqual(finished["status"], "completed", finished)
        duplicate = self.submit(key=key)
        self.assertEqual(duplicate.json()["id"], job_id)
        self.assertEqual(duplicate.json()["credits"], 7)
        self.assertEqual(self.provider.calls, 1)
        download = self.one.request(self.client, "GET", f"/jobs/{job_id}/download")
        self.assertEqual(download.status_code, 200)
        target = Path(self.temp.name) / "converted.nbs"
        target.write_bytes(download.content)
        song = read_music(target)
        self.assertEqual(len(song.layers), 2)
        plan = arrange(song, players=2)
        validate_arrangement(plan, players=2)
        self.assertGreater(sum(len(t["events"]) for t in plan["tracks"]), 8)
        changed = dict(self.payload, instruction="改另一种")
        self.assertEqual(self.submit(payload=changed, key=key).status_code, 409)

    def test_ownership_signatures_and_unknown_job(self):
        response = self.submit()
        job_id = response.json()["id"]
        self.finish(job_id)
        for suffix in ("", "/download"):
            self.assertEqual(self.two.request(self.client, "GET", f"/jobs/{job_id}{suffix}").status_code, 404)
        self.assertEqual(self.client.get(f"/jobs/{job_id}").status_code, 401)
        self.assertEqual(self.one.request(self.client, "GET", "/jobs/abcd").status_code, 404)

    def test_invalid_ai_output_refunds_and_returns_no_model_text(self):
        for mode in ("injection", "wrong_count", "invalid_time", "empty_part"):
            self.provider.mode = mode
            response = self.submit()
            self.assertEqual(response.status_code, 200, response.text)
            result = self.finish(response.json()["id"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["credits"], 8)
            self.assertNotIn("steal", json.dumps(result))

    def test_invalid_upload_and_instruction_do_not_charge(self):
        for payload in (dict(self.payload, instruction="a" * 501), dict(self.payload, players=True),
                        dict(self.payload, data="%%%%"), dict(self.payload, data=base64.b64encode(b"broken").decode())):
            self.assertGreaterEqual(self.submit(payload=payload).status_code, 400)
        self.assertEqual(self.app.state.store.get_device(self.one.device_id)["credits"], 8)
        self.assertEqual(self.provider.calls, 0)

    def test_per_device_pending_limit_and_refund_on_shutdown(self):
        self.provider.delay = 5
        first = self.submit()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(self.app.state.store.get_device(self.one.device_id)["credits"], 7)
        self.client.__exit__(None, None, None)
        self.assertEqual(self.app.state.store.get_device(self.one.device_id)["credits"], 8)
        # Re-open for standard cleanup, ensuring refund is not repeated on restart.
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.assertEqual(self.app.state.store.get_device(self.one.device_id)["credits"], 8)

    def test_revision_parent_and_limit(self):
        self.app.state.manager.config["max_revisions"] = 1
        first = self.submit().json()["id"]
        self.finish(first)
        payload = dict(self.payload, parent_id=first, instruction="主旋律提高八度")
        second = self.submit(payload=payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.finish(second.json()["id"])["revision"], 1)
        self.assertEqual(self.submit(payload=payload).status_code, 400)
        self.assertEqual(self.submit(identity=self.two, payload=payload).status_code, 400)

    def test_daily_quota(self):
        self.app.state.manager.config["max_per_day"] = 1
        job_id = self.submit().json()["id"]
        self.finish(job_id)
        self.assertEqual(self.submit().status_code, 429)


class AdapterTest(unittest.TestCase):
    def test_ai_preserves_source_sustain_and_ending_section(self):
        from fluentmelody.core.music import TimedNote
        source = next((Path(__file__).resolve().parents[1] / "assets").glob("*.nbs"))
        song = read_music(source)
        song.timed_notes = [TimedNote(0, 4, 60, 0), TimedNote(5, 2, 64, 0)]
        directive = {"speed": 1, "parts": [{"role": "melody", "layer_ids": [0], "transpose": 0,
            "stride": 1, "selection": "top", "sections": [{"start": 0, "end": 7, "layer_ids": [0], "transpose": 0}], "notes": []}]}
        plan = apply_directives(song, directive, 1)
        self.assertAlmostEqual(plan["tracks"][0]["events"][0]["duration"], 4)
        self.assertAlmostEqual(plan["tracks"][0]["events"][1]["duration"], 2)

    def test_responses_request_has_schema_no_tools_and_limits(self):
        observed = []
        def handler(request):
            observed.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"speed":1,"parts":[]}'}]}]})
        original = httpx.AsyncClient
        def client_factory(*args, **kwargs):
            return original(transport=httpx.MockTransport(handler), **kwargs)
        with patch.dict(os.environ, {"FM_TEST_KEY": "test-only-secret"}), patch("fluentmelody.servers.ai_adapters.httpx.AsyncClient", client_factory):
            provider = APIProvider({"api_key_env": "FM_TEST_KEY", "model": "configured-test-model", "max_output_tokens": 1024})
            self.assertEqual(asyncio.run(provider.generate({"players": 1})), {"speed": 1, "parts": []})
        self.assertEqual(observed[0]["tools"], [])
        self.assertTrue(observed[0]["text"]["format"]["strict"])
        self.assertEqual(observed[0]["max_output_tokens"], 1024)
        self.assertNotIn("test-only-secret", json.dumps(observed))

    def test_local_provider_requires_dedicated_explicit_configuration(self):
        with self.assertRaises(ProviderError):
            CodexProvider({}).check()
        self.assertIn("shell_tool", CodexProvider.DISABLED)
        self.assertIn("multi_agent", CodexProvider.DISABLED)
        self.assertIn("plugins", CodexProvider.DISABLED)


if __name__ == "__main__":
    unittest.main()
