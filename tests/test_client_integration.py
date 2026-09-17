"""Real client identity/API against the local ASGI service; never sends game input."""
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from fluentmelody.client.config import Identity
from fluentmelody.client.network import API, ServiceError
from fluentmelody.servers.multiplayer import create_app


class ClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.app = create_app(self.root / "server.sqlite3")
        self.store = self.app.state.store
        self.apis = []
        self.host = self.make_client("host")
        self.member = self.make_client("member")
        self.host_token, self.member_token = self.store.issue(count=2)
        self.host.redeem(self.host_token)
        self.member.redeem(self.member_token)

    def make_client(self, name):
        api = API("http://127.0.0.1:8765", Identity(self.root / name))
        api.client.close()
        api.client = TestClient(self.app)
        self.apis.append(api)
        return api

    def tearDown(self):
        for api in self.apis:
            api.close()
        self.directory.cleanup()

    def setup_room(self):
        room = self.host.request("POST", "/rooms", {"name": "房主甲"})
        self.code = room["code"]
        self.member.request("POST", "/rooms/join", {"code": self.code, "name": "成员乙"})
        path = Path(__file__).resolve().parents[1] / "assets" / "小星星_三音轨示例.nbs"
        return self.host.request("POST", self.path("song"), {"filename": path.name,
                "data": base64.b64encode(path.read_bytes()).decode()})

    def path(self, action):
        return f"/rooms/{self.code}/{action}"

    def test_real_identity_roundtrip_and_individual_expiration(self):
        restored = Identity(self.root / "host")
        self.assertEqual(restored.device_id, self.host.identity.device_id)
        self.assertEqual(restored.public_key, self.host.identity.public_key)
        self.assertEqual(restored.session(self.host.url), self.host.identity.session(self.host.url))
        self.assertNotEqual(self.host.identity.device_id, self.member.identity.device_id)
        first = self.host.account()
        self.assertAlmostEqual(first["expires_at"] - time.time(), 86400, delta=3)
        with self.assertRaisesRegex(ServiceError, "其他机器"):
            self.member.redeem(self.host_token)
        recovery = self.host.redeem(self.host_token)
        self.assertEqual(recovery["expires_at"], first["expires_at"])
        with self.store.connect() as db:
            db.execute("UPDATE devices SET expires_at=0 WHERE device_id=?", (self.host.identity.device_id,))
        self.assertEqual(self.host.account()["expires_at"], 0)
        self.assertGreater(self.member.account()["expires_at"], time.time())
        with self.assertRaisesRegex(ServiceError, "到期"):
            self.host.request("POST", "/rooms", {"name": "过期设备"})

    def test_real_signatures_reject_replay_and_machine_change(self):
        transport = self.host.client
        saved = {}
        def capture(method, url, **kwargs):
            saved.update(method=method, url=url, **kwargs)
            return transport.stream(method, url, **kwargs)
        with patch.object(self.host, "client") as fake:
            fake.stream.side_effect = capture
            self.host.request("GET", "/me")
        replay = transport.request(saved["method"], saved["url"], headers=saved["headers"], content=saved["content"])
        self.assertEqual(replay.status_code, 409)
        with patch("fluentmelody.client.config.machine_id", return_value="another-machine"):
            with self.assertRaisesRegex(ValueError, "另一台电脑"):
                Identity(self.root / "host")

    def test_two_real_api_clients_complete_room_lifecycle(self):
        room = self.setup_room()
        revision = room["song_revision"]
        self.assertEqual(len(room["members"]), 2)
        own = self.member.request("GET", self.path("state"))
        self.assertEqual(own["song_revision"], revision)
        self.assertTrue(own["events"])
        cached = self.member.request("GET", self.path("state") + f"?song_revision={revision}")
        self.assertNotIn("events", cached)
        for event, successor in zip(own["events"], own["events"][1:]):
            self.assertGreaterEqual(successor["start"] - event["start"] - event["duration"], .1 - 1e-6)
        room = self.host.request("POST", self.path("ready"), {"ready": True, "song_revision": revision})
        self.assertEqual(room["status"], "waiting")
        room = self.member.request("POST", self.path("ready"), {"ready": True, "song_revision": revision})
        self.assertEqual(room["status"], "countdown")
        host_state = self.host.request("GET", self.path("state"))
        self.assertEqual(room["start_at"], host_state["start_at"])
        self.assertEqual(room["generation"], host_state["generation"])
        with self.assertRaisesRegex(ServiceError, "房主"):
            self.member.request("POST", self.path("pause"), {})
        paused = self.host.request("POST", self.path("pause"), {})
        self.assertEqual(paused["status"], "paused")
        resumed = self.host.request("POST", self.path("pause"), {})
        self.assertEqual(resumed["status"], "countdown")
        self.assertEqual(resumed["position"], paused["position"])
        held = self.member.request("POST", self.path("hold"), {"reason": "客户端网络恢复"})
        self.assertEqual(held["status"], "paused")
        stopped = self.host.request("POST", self.path("stop"), {})
        self.assertEqual(stopped["status"], "waiting")
        self.assertFalse(any(member["ready"] for member in stopped["members"]))
        self.assertEqual(self.member.request("POST", self.path("leave"), {}), {"left": True})
        self.assertEqual(len(self.host.request("GET", self.path("state"))["members"]), 1)
        self.assertEqual(self.host.request("POST", self.path("leave"), {}), {"left": True})
        self.assertNotIn(self.code, self.app.state.rooms.rooms)


class RecordingPlayer:
    def __init__(self):
        self.calls = []
        self.state = "stopped"
        self.position = 0
        self.error = None

    def play(self, events, *, start_at, position):
        self.calls.append((copy.deepcopy(events), start_at, position))
        self.state = "countdown"

    def stop(self):
        self.state = "stopped"


class ClientWindowIntegrationTests(unittest.TestCase):
    """Exercise actual snapshot rendering and player scheduling with inert input."""
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from fluentpy.qt import QtWidgets
        cls.qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        ClientIntegrationTests.setUp(self)
        from fluentmelody.client.app import MainWindow
        self.window = MainWindow(dry_run=True, config_dir=self.root / "window")
        self.window.tick.stop()
        self.window.net_tick.stop()
        self.window.ai_tick.stop()
        self.window.mp.close()
        self.window.mp = self.host
        self.window.identity = self.host.identity
        self.window.player = RecordingPlayer()

    def tearDown(self):
        self.window.room_code = None
        self.window.close()
        self.window.ai.close()
        self.window.deleteLater()
        self.qt.processEvents()
        ClientIntegrationTests.tearDown(self)

    make_client = ClientIntegrationTests.make_client
    setup_room = ClientIntegrationTests.setup_room
    path = ClientIntegrationTests.path

    def start_room(self):
        self.window.enter_room(self.setup_room())
        self.host.request("POST", self.path("ready"), {"ready": True})
        self.member.request("POST", self.path("ready"), {"ready": True})
        state = self.host.request("GET", self.path("state"))
        self.window.apply_room(state)
        return state

    def test_gui_accepts_real_snapshot_and_shared_clock(self):
        state = self.setup_room()
        self.window.enter_room(state)
        self.assertEqual(self.window.members.rowCount(), 2)
        self.assertEqual(self.window.room_code, self.code)
        self.host.request("POST", self.path("ready"), {"ready": True})
        self.member.request("POST", self.path("ready"), {"ready": True})
        state = self.host.request("GET", self.path("state"))
        self.window.apply_room(state)
        self.assertEqual(len(self.window.player.calls), 1)
        events, start_at, position = self.window.player.calls[-1]
        self.assertEqual(events, state["events"])
        self.assertAlmostEqual(start_at, state["start_at"] - self.host.offset)
        self.assertEqual(position, state["position"])
        # Transition from countdown to playing preserves the same worker schedule.
        running = dict(state, status="playing")
        self.window.apply_room(running)
        self.assertEqual(len(self.window.player.calls), 1)
        paused = self.host.request("POST", self.path("pause"), {})
        self.window.apply_room(paused)
        self.assertEqual(self.window.player.state, "stopped")

    def test_old_room_response_cannot_undo_pause(self):
        stale = self.start_room()
        paused = self.host.request("POST", self.path("pause"), {})
        self.window.apply_room(paused)
        self.assertEqual(self.window.player.state, "stopped")
        # Even an old generation packaged later must not restart a newer pause.
        self.window.apply_room(dict(stale, server_time=paused["server_time"] + 1))
        self.assertEqual(self.window.room["status"], "paused")
        self.assertEqual(len(self.window.player.calls), 1)
        # A same-generation response with an older timestamp is stale as well.
        self.window.apply_room(dict(stale, generation=paused["generation"]))
        self.assertEqual(self.window.room["status"], "paused")
        self.assertEqual(self.window.player.state, "stopped")

    def test_slow_response_after_connection_loss_requires_hold_recovery(self):
        stale = self.start_room()
        epoch = self.window.room_sync_epoch
        self.window.fail_room("模拟连接超时")
        self.assertTrue(self.window.room_unsafe)
        self.assertEqual(self.window.player.state, "stopped")
        self.assertGreater(self.window.room_sync_epoch, epoch)
        self.window.apply_room(stale, request_epoch=epoch)
        self.window.apply_room(stale)
        self.assertTrue(self.window.room_unsafe)
        self.assertEqual(len(self.window.player.calls), 1)
        # Run the real poll closure synchronously to verify hold precedes state.
        calls = []
        original_request = self.host.request
        def request(method, path, *args, **kwargs):
            calls.append((method, path))
            return original_request(method, path, *args, **kwargs)
        def run_task(title, fn, done=None, **kwargs):
            result = fn()
            if done:
                done(result)
        with patch.object(self.host, "request", side_effect=request), patch.object(self.window, "run_task", side_effect=run_task):
            self.window.poll_room()
        self.assertEqual(calls[0], ("POST", self.path("hold")))
        self.assertTrue(calls[1][1].startswith(self.path("state")))
        self.assertFalse(self.window.room_unsafe)
        self.assertEqual(self.window.room["status"], "paused")
        self.assertTrue(self.window.room["events"])
        self.assertEqual(len(self.window.player.calls), 1)
        self.window.apply_room(stale, recovered=True, request_epoch=epoch)
        self.assertEqual(self.window.room["status"], "paused")
        # Only a later, explicit room resume schedules playback again.
        resumed = original_request("POST", self.path("pause"), {})
        self.window.apply_room(resumed, request_epoch=self.window.room_sync_epoch)
        self.assertEqual(len(self.window.player.calls), 2)

    def test_invalid_room_callback_stops_existing_performance(self):
        state = self.start_room()
        bad = dict(state, members=None, server_time=state["server_time"] + 1)
        task_id = 876
        self.window._jobs[task_id] = ("同步房间", self.window.apply_room, "room_poll", True)
        self.window._pending.add("room_poll")
        self.window._complete(task_id, bad, None)
        self.assertEqual(self.window.player.state, "stopped")
        self.assertTrue(self.window.room_unsafe)
        self.assertTrue((self.window.config.directory / "last_error.txt").exists())

    def test_native_input_error_pauses_room_once_and_stays_stopped(self):
        self.start_room()
        self.window.player.error = "模拟系统拒绝输入"
        self.window.player.state = "error"
        self.window._tick()
        self.assertEqual(self.window.player.state, "stopped")
        self.assertTrue(self.window.room_unsafe)
        failed_epoch = self.window.room_sync_epoch
        marker = self.window.room_input_error_seen
        self.window._tick()
        self.assertEqual(self.window.room_sync_epoch, failed_epoch)
        self.assertEqual(self.window.room_input_error_seen, marker)
        calls = []
        original_request = self.host.request
        def request(method, path, *args, **kwargs):
            calls.append((method, path))
            return original_request(method, path, *args, **kwargs)
        def run_task(title, fn, done=None, **kwargs):
            result = fn()
            if done:
                done(result)
        with patch.object(self.host, "request", side_effect=request), patch.object(self.window, "run_task", side_effect=run_task):
            self.window.poll_room()
            self.assertEqual(self.app.state.rooms.rooms[self.code].status, "paused")
            self.assertEqual(self.window.room["status"], "paused")
            self.assertFalse(self.window.room_unsafe)
            # The worker retains its error until the next explicit play. This
            # retained diagnostic must not trigger an endless hold/recovery loop.
            self.window._tick()
            self.window._tick()
            self.window.poll_room()
        self.assertEqual(sum(path == self.path("hold") for _, path in calls), 1)
        self.assertEqual(self.window.room_sync_epoch, failed_epoch)
        self.assertFalse(self.window.room_unsafe)
        self.assertEqual(self.window.player.state, "stopped")
        self.assertEqual(len(self.window.player.calls), 1)


if __name__ == "__main__":
    unittest.main()
