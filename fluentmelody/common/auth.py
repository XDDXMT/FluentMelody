"""Device-bound, signed authentication shared by the two independent services.

Codes are stored hashed. A redeemed code belongs to one Ed25519 device key;
redeeming that code again can recover access, but never adds more time/credit.
"""
from __future__ import annotations

import base64
from collections import OrderedDict, deque
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request
from fastapi.responses import JSONResponse


class Error(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    return "\n".join((method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest())).encode("utf-8")


class Store:
    def __init__(self, db_path, kind: str = "multiplayer"):
        if kind not in {"multiplayer", "ai"}:
            raise ValueError("kind must be multiplayer or ai")
        self.db_path = str(Path(db_path).resolve())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.kind = kind
        self.lock = threading.RLock()
        self._rates = OrderedDict()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices(
                    device_id TEXT PRIMARY KEY, public_key TEXT NOT NULL,
                    expires_at REAL NOT NULL DEFAULT 0, credits INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens(
                    token_hash TEXT PRIMARY KEY, label TEXT NOT NULL, duration REAL NOT NULL,
                    credits INTEGER NOT NULL, created_at REAL NOT NULL,
                    redeemed_by TEXT, redeemed_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions(
                    secret_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS nonces(
                    device_id TEXT NOT NULL, nonce TEXT NOT NULL, seen_at REAL NOT NULL,
                    PRIMARY KEY(device_id, nonce));
                CREATE INDEX IF NOT EXISTS nonce_time ON nonces(seen_at);
            """)
            previous = db.execute("SELECT value FROM meta WHERE key='kind'").fetchone()
            if previous and previous[0] != kind:
                raise ValueError("联机服务器和 AI 服务器必须使用独立的数据库")
            db.execute("INSERT OR IGNORE INTO meta VALUES('kind', ?)", (kind,))

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level="DEFERRED", factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def rate_limit(self, key: str, limit: int, window: float):
        now = time.time()
        with self.lock:
            bucket = self._rates.setdefault(key, deque())
            while bucket and bucket[0] < now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                raise Error("请求过于频繁，请稍后再试", 429)
            bucket.append(now)
            self._rates.move_to_end(key)
            while len(self._rates) > 10000:
                self._rates.popitem(last=False)

    def issue(self, days: float = 1, credits: int = 1, count: int = 1):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise Error("发行数量应为 1—10000")
        if not isinstance(days, (int, float)) or not math.isfinite(days) or not 0 < days <= 3650:
            raise Error("有效天数应大于 0 且不超过 3650")
        if isinstance(credits, bool) or not isinstance(credits, int) or not 1 <= credits <= 1000000:
            raise Error("额度应为 1—1000000")
        prefix = "FM" if self.kind == "multiplayer" else "AI"
        codes = [prefix + "-" + secrets.token_urlsafe(24) for _ in range(count)]
        with self.lock, self.connect() as db:
            db.executemany("INSERT INTO tokens(token_hash,label,duration,credits,created_at) VALUES(?,?,?,?,?)",
                           [(digest(code), code[:9], days * 86400 if self.kind == "multiplayer" else 0,
                             credits if self.kind == "ai" else 0, time.time()) for code in codes])
        return codes

    def get_device(self, device_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            raise Error("设备尚未兑换 token", 401)
        return dict(row)

    @staticmethod
    def public_device(device):
        return {key: device[key] for key in ("device_id", "expires_at", "credits")}

    def redeem(self, token: str, device_id: str, public_key: str):
        if not isinstance(token, str) or not 10 <= len(token) <= 200:
            raise Error("token 格式不正确")
        validate_device(device_id, public_key)
        now = time.time()
        access = secrets.token_urlsafe(40)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM tokens WHERE token_hash=?", (digest(token.strip()),)).fetchone()
            if not item or item["revoked"]:
                raise Error("token 不存在或已停用", 403)
            if item["redeemed_by"] and item["redeemed_by"] != device_id:
                raise Error("该 token 已绑定其他机器", 409)
            device = db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if device and (device["public_key"] != public_key or device["revoked"]):
                raise Error("设备凭据不匹配或已停用", 403)
            if not device:
                db.execute("INSERT INTO devices(device_id,public_key,created_at) VALUES(?,?,?)", (device_id, public_key, now))
            if not item["redeemed_by"]:
                if self.kind == "multiplayer":
                    db.execute("UPDATE devices SET expires_at=MAX(expires_at, ?)+? WHERE device_id=?", (now, item["duration"], device_id))
                else:
                    db.execute("UPDATE devices SET credits=credits+? WHERE device_id=?", (item["credits"], device_id))
                db.execute("UPDATE tokens SET redeemed_by=?,redeemed_at=? WHERE token_hash=?", (device_id, now, digest(token.strip())))
            # Recovery replaces old sessions so credentials cannot accumulate indefinitely.
            db.execute("DELETE FROM sessions WHERE device_id=?", (device_id,))
            db.execute("INSERT INTO sessions(secret_hash,device_id,created_at) VALUES(?,?,?)", (digest(access), device_id, now))
            result = dict(db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone())
        return {**self.public_device(result), "access_token": access, "server_time": now}

    def _record_nonce(self, device_id, nonce, now):
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM nonces WHERE seen_at<?", (now - 180,))
            try:
                db.execute("INSERT INTO nonces VALUES(?,?,?)", (device_id, nonce, now))
            except sqlite3.IntegrityError:
                raise Error("该请求已使用，请重新发送", 409)

    def verify_signature(self, headers, method, path, body, public_key, device_id):
        headers = {k.lower(): v for k, v in headers.items()}
        if headers.get("x-device-id") != device_id:
            raise Error("设备标识不匹配", 401)
        timestamp = headers.get("x-timestamp", "")
        nonce = headers.get("x-nonce", "")
        try:
            stamp = float(timestamp)
        except (ValueError, TypeError):
            raise Error("请求时间无效", 401)
        now = time.time()
        if not math.isfinite(stamp) or abs(now - stamp) > 60:
            raise Error("设备时钟与服务器不一致，请校准时间", 401)
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
            raise Error("请求随机码无效", 401)
        try:
            signature = base64.b64decode(headers.get("x-signature", ""), validate=True)
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
            key.verify(signature, canonical(method, path, timestamp, nonce, body))
        except (ValueError, TypeError, InvalidSignature):
            raise Error("设备签名验证失败", 401)
        self._record_nonce(device_id, nonce, now)

    def authenticate(self, headers, method, path, body, require_active=True):
        headers = {k.lower(): v for k, v in headers.items()}
        secret = headers.get("authorization", "")
        if not secret.startswith("Bearer ") or len(secret) > 300:
            raise Error("请先在当前服务器兑换 token", 401)
        device_id = headers.get("x-device-id", "")
        self.rate_limit("device:" + device_id, 600, 60)
        with self.connect() as db:
            row = db.execute("SELECT d.* FROM sessions s JOIN devices d ON d.device_id=s.device_id WHERE s.secret_hash=? AND s.revoked=0 AND d.device_id=?", (digest(secret[7:]), device_id)).fetchone()
        if not row or row["revoked"]:
            raise Error("设备登录已失效，请重新兑换原 token 恢复", 401)
        device = dict(row)
        self.verify_signature(headers, method, path, body, device["public_key"], device_id)
        if require_active and self.kind == "multiplayer" and device["expires_at"] <= time.time():
            raise Error("联机时长已到期，请兑换新的 token", 402)
        return device

    def consume_credits(self, device_id, amount=1):
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be a positive integer")
        with self.lock, self.connect() as db:
            cursor = db.execute("UPDATE devices SET credits=credits-? WHERE device_id=? AND revoked=0 AND credits>=?", (amount, device_id, amount))
            if cursor.rowcount != 1:
                raise Error("AI 编曲额度不足", 402)
        return self.get_device(device_id)

    def refund_credits(self, device_id, amount=1):
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be a positive integer")
        with self.lock, self.connect() as db:
            db.execute("UPDATE devices SET credits=credits+? WHERE device_id=?", (amount, device_id))
        return self.get_device(device_id)

    def revoke(self, device_id=None, token=None):
        if not device_id and not token:
            raise Error("需要提供设备 ID 或 token")
        with self.lock, self.connect() as db:
            if device_id:
                db.execute("UPDATE devices SET revoked=1 WHERE device_id=?", (device_id,))
                db.execute("UPDATE sessions SET revoked=1 WHERE device_id=?", (device_id,))
            if token:
                db.execute("UPDATE tokens SET revoked=1 WHERE token_hash=?", (digest(token),))

    def list_tokens(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT label,duration,credits,created_at,redeemed_by,redeemed_at,revoked FROM tokens ORDER BY created_at DESC LIMIT 10000")]

    def list_devices(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT device_id,expires_at,credits,revoked,created_at FROM devices ORDER BY created_at DESC LIMIT 10000")]


def validate_device(device_id, public_key):
    if not isinstance(device_id, str) or not re.fullmatch(r"[a-f0-9]{64}", device_id):
        raise Error("设备 ID 格式不正确")
    try:
        if not isinstance(public_key, str) or len(public_key) != 44:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    except (ValueError, TypeError):
        raise Error("设备公钥无效")


async def read_body(request: Request, max_bytes: int):
    cached = getattr(request, "_body", None)
    if cached is not None:
        if len(cached) > max_bytes:
            raise Error("请求内容过大", 413)
        return cached
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > max_bytes:
                raise Error("请求内容过大", 413)
        except ValueError:
            raise Error("请求长度无效")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise Error("请求内容过大", 413)
    request._body = bytes(chunks)
    return request._body


async def json_body(request, max_bytes=16384):
    body = await read_body(request, max_bytes)
    try:
        value = json.loads(body or b"{}")
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise Error("请求必须是 JSON 对象")


async def verify_request(request: Request, store: Store, require_active=True):
    body = await read_body(request, 6 * 1024 * 1024)
    return store.authenticate(request.headers, request.method, request.url.path, body, require_active)


async def redeem_request(request: Request, store: Store):
    peer = request.client.host if request.client else "local"
    store.rate_limit("redeem-ip:" + peer, 15, 60)
    values = await json_body(request)
    device_id, public_key = values.get("device_id"), values.get("public_key")
    validate_device(device_id, public_key)
    store.verify_signature(request.headers, request.method, request.url.path, await request.body(), public_key, device_id)
    return store.redeem(values.get("token"), device_id, public_key)


def install_error_handlers(app):
    @app.exception_handler(Error)
    async def handle_error(request, error):
        return JSONResponse({"detail": error.message}, status_code=error.status)
