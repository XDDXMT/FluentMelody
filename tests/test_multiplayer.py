import base64
import concurrent.futures
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from fluentmelody.common.auth import Error, Store, canonical
from fluentmelody.servers.multiplayer import create_app


class Device:
    def __init__(self, client):
        self.client = client
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.id = hashlib.sha256(self.key.public_key().public_bytes_raw()).hexdigest()
        self.access = None

    def prepare(self, method, path, values=None):
        body = json.dumps(values, separators=(",", ":"), ensure_ascii=False).encode() if values is not None else b""
        timestamp, nonce = str(time.time()), secrets.token_hex(16)
        signature = self.key.sign(canonical(method, path.split("?", 1)[0], timestamp, nonce, body))
        headers = {"X-Device-ID": self.id, "X-Timestamp": timestamp, "X-Nonce": nonce,
                   "X-Signature": base64.b64encode(signature).decode(), "Content-Type": "application/json"}
        if self.access:
            headers["Authorization"] = "Bearer " + self.access
        return headers, body

    def request(self, method, path, values=None):
        headers, body = self.prepare(method, path, values)
        return self.client.request(method, path, headers=headers, content=body)

    def redeem(self, token):
        result = self.request("POST", "/redeem", {"token": token, "device_id": self.id, "public_key": self.public})
        if result.status_code == 200:
            self.access = result.json()["access_token"]
        return result


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name) / "rooms.sqlite3")
        self.client = TestClient(self.app)
        self.store = self.app.state.store

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_token_binding_recovery_and_real_expiry(self):
        device, stranger = Device(self.client), Device(self.client)
        code = self.store.issue(days=1)[0]
        response = device.redeem(code)
        self.assertEqual(response.status_code, 200)
        first = response.json()["expires_at"]
        self.assertAlmostEqual(first - time.time(), 86400, delta=2)
        self.assertEqual(stranger.redeem(code).status_code, 409)
        response = device.redeem(code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expires_at"], first)
        with self.store.connect() as db:
            db.execute("UPDATE devices SET expires_at=? WHERE device_id=?", (time.time() - 10, device.id))
        self.assertEqual(device.request("GET", "/me").status_code, 200)
        self.assertEqual(device.request("POST", "/rooms", {"name": "Host"}).status_code, 402)

    def test_nonce_replay_and_body_tampering(self):
        device = Device(self.client)
        device.redeem(self.store.issue()[0])
        headers, body = device.prepare("GET", "/me")
        self.assertEqual(self.client.get("/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=headers).status_code, 409)
        headers, body = device.prepare("POST", "/rooms", {"name": "Original"})
        self.assertEqual(self.client.post("/rooms", headers=headers, content=b'{"name":"Changed"}').status_code, 401)
        stolen = Device(self.client)
        stolen.access = device.access
        self.assertEqual(stolen.request("GET", "/me").status_code, 401)

    def test_redeem_requires_device_possession(self):
        device = Device(self.client)
        result = self.client.post("/redeem", json={"token": self.store.issue()[0], "device_id": device.id, "public_key": device.public})
        self.assertEqual(result.status_code, 401)

    def test_separate_services_and_atomic_credit_consumption(self):
        with self.assertRaises(ValueError):
            Store(self.store.db_path, "ai")
        store = Store(Path(self.directory.name) / "ai.sqlite3", "ai")
        device = Device(self.client)
        code = store.issue(credits=1)[0]
        store.redeem(code, device.id, device.public)
        def spend(_):
            try:
                store.consume_credits(device.id)
                return True
            except Error:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(spend, range(8)))
        self.assertEqual(sum(results), 1)
        store.refund_credits(device.id)
        self.assertEqual(store.get_device(device.id)["credits"], 1)


class RoomTests(unittest.TestCase):
    def setUp(self):
        AuthTests.setUp(self)
        self.host, self.member, self.stranger = [Device(self.client) for _ in range(3)]
        for device, code in zip((self.host, self.member, self.stranger), self.store.issue(count=3)):
            self.assertEqual(device.redeem(code).status_code, 200)
        response = self.host.request("POST", "/rooms", {"name": "房主"})
        self.assertEqual(response.status_code, 200)
        self.code = response.json()["code"]
        response = self.member.request("POST", "/rooms/join", {"code": self.code, "name": "成员"})
        self.assertEqual(response.status_code, 200)
        example = Path(__file__).resolve().parents[1] / "assets" / "小星星_三音轨示例.nbs"
        values = {"filename": example.name, "data": base64.b64encode(example.read_bytes()).decode()}
        response = self.host.request("POST", self.path("song"), values)
        self.assertEqual(response.status_code, 200, response.text)
        self.revision = response.json()["song_revision"]

    def tearDown(self):
        AuthTests.tearDown(self)

    def path(self, suffix):
        return f"/rooms/{self.code}/{suffix}"

    def test_all_ready_start_and_host_pause(self):
        first = self.host.request("POST", self.path("ready"), {"song_revision": self.revision}).json()
        self.assertEqual(first["status"], "waiting")
        second = self.member.request("POST", self.path("ready"), {"song_revision": self.revision}).json()
        self.assertEqual(second["status"], "countdown")
        self.assertGreater(second["start_at"], time.time() + 2)
        self.assertEqual(self.member.request("POST", self.path("pause"), {}).status_code, 403)
        paused = self.host.request("POST", self.path("pause"), {}).json()
        self.assertEqual(paused["status"], "paused")
        resumed = self.host.request("POST", self.path("pause"), {}).json()
        self.assertEqual(resumed["status"], "countdown")
        self.assertEqual(resumed["position"], paused["position"])
        self.assertGreater(resumed["generation"], paused["generation"])

    def test_disconnect_and_reconnect_never_autoresumes(self):
        self.host.request("POST", self.path("ready"), {})
        self.member.request("POST", self.path("ready"), {})
        manager = self.app.state.rooms
        room = manager.rooms[self.code]
        room.members[self.member.id].last_seen -= 10
        manager.sweep()
        self.assertEqual(room.status, "paused")
        self.assertIn("断线", room.notice)
        self.assertEqual(self.host.request("POST", self.path("pause"), {}).status_code, 409)
        response = self.member.request("GET", self.path("state")).json()
        self.assertEqual(response["status"], "paused")
        self.assertTrue(all(member["connected"] for member in response["members"]))
        self.assertEqual(self.host.request("POST", self.path("pause"), {}).json()["status"], "countdown")

    def test_member_scope_revisions_hold_and_no_overlap(self):
        self.assertEqual(self.stranger.request("GET", self.path("state")).status_code, 404)
        self.assertEqual(self.member.request("POST", self.path("ready"), {"song_revision": -1}).status_code, 409)
        response = self.member.request("GET", self.path("state")).json()
        events = response["events"]
        self.assertTrue(events)
        for left, right in zip(events, events[1:]):
            self.assertGreaterEqual(right["start"] - left["start"] - left["duration"], .1 - 1e-6)
        unchanged = self.member.request("GET", self.path("state") + f"?song_revision={self.revision}").json()
        self.assertNotIn("events", unchanged)
        held = self.member.request("POST", self.path("hold"), {"reason": "网络延迟"}).json()
        self.assertEqual(held["status"], "paused")
        self.assertIn("网络延迟", held["notice"])

    def test_expired_member_pauses_room_and_host_leave_transfers(self):
        self.host.request("POST", self.path("ready"), {})
        self.member.request("POST", self.path("ready"), {})
        with self.store.connect() as db:
            db.execute("UPDATE devices SET expires_at=0 WHERE device_id=?", (self.member.id,))
        self.app.state.rooms.sweep()
        room = self.app.state.rooms.rooms[self.code]
        self.assertEqual(room.status, "paused")
        self.assertIn("授权", room.notice)
        self.assertEqual(self.host.request("POST", self.path("leave"), {}).status_code, 200)
        self.assertEqual(room.host_id, self.member.id)
        self.assertEqual(room.status, "waiting")


if __name__ == "__main__":
    unittest.main()
