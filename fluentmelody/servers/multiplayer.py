"""Independent room synchronization service. Run one process per database."""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time

from fastapi import FastAPI, Request

from fluentmelody.common.auth import Error, Store, install_error_handlers, json_body, redeem_request, verify_request
from fluentmelody.core.music import arrange, read_music, validate_arrangement


@dataclass
class Member:
    device_id: str
    name: str
    last_seen: float
    ready: bool = False
    connected: bool = True


@dataclass
class Room:
    code: str
    host_id: str
    members: dict = field(default_factory=dict)
    song: object = None
    plan: dict | None = None
    status: str = "waiting"
    start_at: float | None = None
    position: float = 0
    generation: int = 0
    song_revision: int = 0
    notice: str = "等待房主加载歌曲"
    updated_at: float = field(default_factory=time.time)


def clean_name(value, fallback="演奏者"):
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise Error("名称必须是文本")
    return "".join(char for char in value.strip() if char.isprintable())[:24] or fallback


class RoomManager:
    def __init__(self, store, timeout=3.0, countdown=3.0, max_rooms=32):
        self.store = store
        self.timeout = max(1.0, float(timeout))
        self.countdown = max(1.0, float(countdown))
        self.max_rooms = max_rooms
        self.rooms = {}
        self.lock = threading.RLock()

    def _current_position(self, room, now):
        if room.status in {"countdown", "playing"} and room.start_at is not None:
            return room.position + max(0, now - room.start_at)
        return room.position

    def _pause(self, room, reason, now):
        room.position = self._current_position(room, now)
        if room.plan:
            room.position = min(room.position, room.plan["duration"])
        room.start_at = None
        room.status = "paused" if room.plan else "waiting"
        room.generation += 1
        room.notice = reason
        room.updated_at = now

    def _reset(self, room, reason):
        room.status = "waiting"
        room.position = 0
        room.start_at = None
        room.generation += 1
        room.notice = reason
        room.updated_at = time.time()
        for member in room.members.values():
            member.ready = False

    def _rearrange(self, room):
        if room.song is not None:
            room.plan = validate_arrangement(arrange(room.song, players=len(room.members)), players=len(room.members))
            room.song_revision += 1
        self._reset(room, "成员已变化，请重新准备" if room.plan else "等待房主加载歌曲")

    def sweep(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            for code, room in list(self.rooms.items()):
                if now - max(member.last_seen for member in room.members.values()) > 3600:
                    del self.rooms[code]
                    continue
                for member in room.members.values():
                    if not member.connected:
                        continue
                    try:
                        device = self.store.get_device(member.device_id)
                        active = not device["revoked"] and device["expires_at"] > now
                    except Error:
                        active = False
                    if now - member.last_seen > self.timeout or not active:
                        member.connected = False
                        member.ready = False
                        reason = f"{member.name} 已断线，演奏暂停" if active else f"{member.name} 的联机授权已到期或停用，演奏暂停"
                        self._pause(room, reason, now)
                if room.status == "countdown" and now >= room.start_at:
                    room.status = "playing"
                if room.status == "playing" and room.plan and self._current_position(room, now) >= room.plan["duration"]:
                    self._reset(room, "演奏结束，可重新准备")

    def _room(self, code, device_id, touch=True):
        self.sweep()
        room = self.rooms.get(code)
        if not room or device_id not in room.members:
            raise Error("房间不存在，或你不是房间成员", 404)
        if touch:
            member = room.members[device_id]
            if not member.connected:
                member.connected = True
                member.ready = False
                room.notice = f"{member.name} 已重新连接，请房主确认后继续"
            member.last_seen = time.time()
            room.updated_at = time.time()
        return room

    def snapshot(self, room, device_id, include_events=True):
        now = time.time()
        members = []
        own_events = []
        for index, member in enumerate(room.members.values()):
            track = room.plan["tracks"][index] if room.plan else None
            members.append({"device_id": member.device_id, "name": member.name,
                            "ready": member.ready, "connected": member.connected,
                            "track_name": track["name"] if track else "待分配"})
            if member.device_id == device_id and track:
                own_events = track["events"]
        result = {"code": room.code, "host_id": room.host_id, "status": room.status,
                  "start_at": room.start_at, "position": room.position,
                  "playhead": self._current_position(room, now), "generation": room.generation,
                  "song_revision": room.song_revision,
                  "name": room.plan["name"] if room.plan else "尚未加载歌曲",
                  "duration": room.plan["duration"] if room.plan else 0,
                  "members": members, "notice": room.notice, "server_time": now,
                  "summary": room.plan.get("summary", "") if room.plan else "",
                  "warnings": room.plan.get("warnings", []) if room.plan else []}
        if include_events:
            result["events"] = own_events
        return result

    def create(self, device_id, name):
        with self.lock:
            self.sweep()
            if len(self.rooms) >= self.max_rooms:
                raise Error("服务器房间已满，请稍后再试", 503)
            if any(device_id in room.members for room in self.rooms.values()):
                raise Error("请先退出当前房间", 409)
            while True:
                code = str(secrets.randbelow(900000) + 100000)
                if code not in self.rooms:
                    break
            room = Room(code, device_id)
            room.members[device_id] = Member(device_id, clean_name(name, "房主"), time.time())
            self.rooms[code] = room
            return self.snapshot(room, device_id)

    def join(self, device_id, code, name):
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            raise Error("请输入六位连接码")
        with self.lock:
            self.sweep()
            room = self.rooms.get(code)
            if not room:
                raise Error("连接码无效或房间已经关闭", 404)
            if device_id in room.members:
                self._room(code, device_id)
                return self.snapshot(room, device_id)
            if any(device_id in other.members for other in self.rooms.values()):
                raise Error("请先退出当前房间", 409)
            if len(room.members) >= 8:
                raise Error("房间最多支持八人", 409)
            if room.status in {"playing", "countdown"}:
                raise Error("房间正在演奏，请房主先暂停", 409)
            room.members[device_id] = Member(device_id, clean_name(name), time.time())
            try:
                self._rearrange(room)
            except Exception:
                del room.members[device_id]
                raise
            return self.snapshot(room, device_id)

    def state(self, device_id, code, song_revision=None):
        with self.lock:
            room = self._room(code, device_id)
            # Omit unchanged note arrays to keep 300 ms heartbeats lightweight.
            return self.snapshot(room, device_id, song_revision != room.song_revision)

    def set_song(self, device_id, code, song):
        with self.lock:
            room = self._room(code, device_id)
            self._host(room, device_id)
            plan = validate_arrangement(arrange(song, players=len(room.members)), players=len(room.members))
            room.song, room.plan = song, plan
            room.song_revision += 1
            self._reset(room, "歌曲已分配，每人使用自己的准备快捷键或点击准备")
            return self.snapshot(room, device_id)

    @staticmethod
    def _host(room, device_id):
        if room.host_id != device_id:
            raise Error("只有房主可以执行此操作", 403)

    def ready(self, device_id, code, value=True, revision=None):
        if not isinstance(value, bool):
            raise Error("ready 应为布尔值")
        with self.lock:
            room = self._room(code, device_id)
            if not room.plan:
                raise Error("请先由房主加载歌曲", 409)
            if room.status not in {"waiting", "paused"}:
                raise Error("演奏已开始；暂停请由房主操作", 409)
            if revision is not None and revision != room.song_revision:
                raise Error("歌曲分配已经更新，请等待同步后重新准备", 409)
            room.members[device_id].ready = value
            room.notice = "等待所有成员准备"
            if all(member.ready and member.connected for member in room.members.values()):
                room.start_at = time.time() + self.countdown
                room.status = "countdown"
                room.generation += 1
                room.notice = "全部准备完毕，统一倒计时"
            return self.snapshot(room, device_id)

    def pause(self, device_id, code):
        with self.lock:
            room = self._room(code, device_id)
            self._host(room, device_id)
            if room.status in {"playing", "countdown"}:
                self._pause(room, "房主已暂停全房间", time.time())
            elif room.status == "paused":
                if not room.plan or not all(member.connected for member in room.members.values()):
                    raise Error("仍有成员离线，全部重新连接后才能继续", 409)
                room.start_at = time.time() + self.countdown
                room.status = "countdown"
                room.generation += 1
                room.notice = "房主继续演奏，统一倒计时"
            else:
                raise Error("尚未开始演奏，请所有成员先准备", 409)
            return self.snapshot(room, device_id)

    def hold(self, device_id, code, reason):
        with self.lock:
            room = self._room(code, device_id)
            text = str(reason or "客户端请求安全暂停")[:120]
            text = "".join(char for char in text if char.isprintable())
            self._pause(room, room.members[device_id].name + "：" + text, time.time())
            return self.snapshot(room, device_id)

    def stop(self, device_id, code):
        with self.lock:
            room = self._room(code, device_id)
            self._host(room, device_id)
            self._reset(room, "房主已停止演奏，可重新准备")
            return self.snapshot(room, device_id)

    def leave(self, device_id, code):
        with self.lock:
            room = self._room(code, device_id)
            member = room.members.pop(device_id)
            if not room.members:
                del self.rooms[code]
                return {"left": True}
            if room.host_id == device_id:
                room.host_id = next(iter(room.members))
            self._rearrange(room)
            room.notice = member.name + " 已退出，音轨重新分配，请重新准备"
            return {"left": True}


def create_app(db_path=None, *, disconnect_timeout=3.0, countdown=3.0):
    store = Store(db_path or os.environ.get("FM_MULTIPLAYER_DB", "data/multiplayer.sqlite3"), "multiplayer")
    manager = RoomManager(store, disconnect_timeout, countdown)

    @asynccontextmanager
    async def lifespan(app):
        async def health():
            while True:
                await asyncio.sleep(.5)
                await asyncio.to_thread(manager.sweep)
        task = asyncio.create_task(health())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="FluentMelody 联机服务器", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.rooms = manager
    install_error_handlers(app)

    @app.get("/time")
    async def server_time():
        return {"server_time": time.time(), "service": "multiplayer", "version": "1.0.0"}

    @app.post("/redeem")
    async def redeem(request: Request):
        return await redeem_request(request, store)

    @app.get("/me")
    async def me(request: Request):
        device = await verify_request(request, store, require_active=False)
        return {**store.public_device(device), "server_time": time.time()}

    @app.post("/rooms")
    async def create(request: Request):
        device = await verify_request(request, store)
        values = await json_body(request)
        store.rate_limit("create:" + device["device_id"], 10, 60)
        return await asyncio.to_thread(manager.create, device["device_id"], values.get("name"))

    @app.post("/rooms/join")
    async def join(request: Request):
        device = await verify_request(request, store)
        values = await json_body(request)
        store.rate_limit("join:" + device["device_id"], 20, 60)
        return await asyncio.to_thread(manager.join, device["device_id"], values.get("code"), values.get("name"))

    @app.get("/rooms/{code}/state")
    async def state(code: str, request: Request):
        device = await verify_request(request, store)
        raw = request.query_params.get("song_revision")
        try:
            revision = int(raw) if raw is not None else None
        except ValueError:
            raise Error("歌曲版本无效")
        return await asyncio.to_thread(manager.state, device["device_id"], code, revision)

    @app.post("/rooms/{code}/song")
    async def song(code: str, request: Request):
        device = await verify_request(request, store)
        store.rate_limit("song:" + device["device_id"], 6, 60)
        values = await json_body(request, 6 * 1024 * 1024)
        filename, encoded = values.get("filename"), values.get("data")
        if not isinstance(filename, str) or Path(filename).suffix.lower() not in {".mid", ".midi", ".nbs"}:
            raise Error("只接受 MIDI 或 NBS 歌曲")
        if not isinstance(encoded, str):
            raise Error("缺少歌曲数据")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise Error("歌曲数据编码无效")
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise Error("歌曲应为 1 字节—4 MB", 413)
        # Check membership and host before parsing untrusted music.
        with manager.lock:
            room = manager._room(code, device["device_id"])
            manager._host(room, device["device_id"])
        def load():
            with tempfile.TemporaryDirectory(prefix="fluentmelody-song-") as directory:
                path = Path(directory) / ("song" + Path(filename).suffix.lower())
                path.write_bytes(raw)
                try:
                    parsed = read_music(path)
                    if not parsed.name or parsed.name == "song":
                        parsed.name = Path(filename).stem[:120]
                    return manager.set_song(device["device_id"], code, parsed)
                except (ValueError, EOFError, OSError) as error:
                    raise Error("歌曲无法读取或编排：" + str(error)[:200])
        return await asyncio.to_thread(load)

    @app.post("/rooms/{code}/ready")
    async def ready(code: str, request: Request):
        device = await verify_request(request, store)
        values = await json_body(request)
        return await asyncio.to_thread(manager.ready, device["device_id"], code, values.get("ready", True), values.get("song_revision"))

    @app.post("/rooms/{code}/pause")
    async def pause(code: str, request: Request):
        device = await verify_request(request, store)
        await json_body(request)
        return await asyncio.to_thread(manager.pause, device["device_id"], code)

    @app.post("/rooms/{code}/hold")
    async def hold(code: str, request: Request):
        device = await verify_request(request, store)
        values = await json_body(request)
        return await asyncio.to_thread(manager.hold, device["device_id"], code, values.get("reason"))

    @app.post("/rooms/{code}/stop")
    async def stop(code: str, request: Request):
        device = await verify_request(request, store)
        await json_body(request)
        return await asyncio.to_thread(manager.stop, device["device_id"], code)

    @app.post("/rooms/{code}/leave")
    async def leave(code: str, request: Request):
        # Expired clients may still leave cleanly.
        device = await verify_request(request, store, require_active=False)
        await json_body(request)
        return await asyncio.to_thread(manager.leave, device["device_id"], code)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="FluentMelody 联机服务器")
    parser.add_argument("--db", default="data/multiplayer.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, workers=1, access_log=False,
                limit_concurrency=100, timeout_keep_alive=10)


if __name__ == "__main__":
    main()
