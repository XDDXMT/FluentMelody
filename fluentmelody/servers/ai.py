"""Independent AI arranging server: signed clients, prepaid credits, bounded jobs."""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

from fastapi import FastAPI, Request
from fastapi.responses import Response

from fluentmelody.common.auth import Error, Store, install_error_handlers, json_body, redeem_request, verify_request
from .ai_adapters import ProviderError, make_provider
from .ai_music import apply_directives, summarize

MAX_FILE = 4 * 1024 * 1024
DEFAULTS = {"host": "127.0.0.1", "port": 8766, "database": "data/ai.sqlite3",
            "backend": "unconfigured", "api_base": "https://api.openai.com/v1",
            "api_format": "responses", "api_key_env": "FLUENTMELODY_AI_KEY", "model": "",
            "workers": 2, "max_pending": 16, "max_per_day": 20, "max_revisions": 3,
            "credits_per_job": 1, "retention_days": 7, "timeout": 120, "max_output_tokens": 8192}
LOGGER = logging.getLogger("fluentmelody.ai")


def configuration(values=None):
    result = dict(DEFAULTS)
    result.update(values or {})
    bounds = {"workers": (1, 4), "max_pending": (1, 100), "max_per_day": (1, 1000),
              "max_revisions": (0, 10), "credits_per_job": (1, 1000), "retention_days": (1, 90),
              "timeout": (10, 300), "max_output_tokens": (1024, 16384), "port": (1, 65535)}
    for name, (low, high) in bounds.items():
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"AI 配置 {name} 必须为 {low}—{high} 的整数")
    if result["workers"] > result["max_pending"]:
        raise ValueError("max_pending 不能小于 workers")
    return result


class InstanceLock:
    """Only one scheduler may own an AI database; admin token tools remain usable."""
    def __init__(self, db_path):
        self.path = Path(str(db_path) + ".scheduler.lock")
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("此 AI 数据库已由另一个服务进程使用；请勿配置多个 uvicorn 进程。") from None

    def release(self):
        if self.handle:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def parse_music(data, filename):
    from fluentmelody.core.music import read_music
    suffix = Path(filename).suffix.lower()
    if suffix not in {".nbs", ".mid", ".midi"}:
        raise Error("只支持 NBS、MID 或 MIDI 文件")
    with tempfile.TemporaryDirectory(prefix="fluentmelody-score-") as folder:
        path = Path(folder) / ("source" + suffix)
        path.write_bytes(data)
        try:
            song = read_music(path)
        except (ValueError, OSError, EOFError, IndexError, UnicodeError) as exc:
            raise Error("歌曲文件无效或损坏，请重新导出 NBS / MIDI 文件") from exc
    if not song.notes or len(song.notes) > 50000:
        raise Error("歌曲为空或超过 50000 个音符")
    if song.tempo <= 0 or max(n.tick for n in song.notes) / song.tempo > 1800:
        raise Error("歌曲最长支持 30 分钟")
    return song


class JobManager:
    def __init__(self, config, store, provider=None):
        self.config, self.store, self.provider = config, store, provider
        self.queue = asyncio.Queue(maxsize=config["max_pending"])
        self.tasks = []
        self.instance = InstanceLock(store.db_path)
        with contextlib.closing(store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_jobs(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, idempotency TEXT NOT NULL,
                    request_hash TEXT NOT NULL, source_hash TEXT NOT NULL,
                    root_id TEXT NOT NULL, parent_id TEXT, revision INTEGER NOT NULL,
                    filename TEXT NOT NULL, source BLOB NOT NULL, players INTEGER NOT NULL,
                    instruction TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                    created_at REAL NOT NULL, finished_at REAL, charge INTEGER NOT NULL,
                    output BLOB, directives TEXT, name TEXT,
                    UNIQUE(owner,idempotency));
                CREATE INDEX IF NOT EXISTS ai_jobs_owner_time ON ai_jobs(owner,created_at);
                CREATE INDEX IF NOT EXISTS ai_jobs_root ON ai_jobs(root_id);
            """)

    def _recover(self):
        # Refund and mark in the same transaction, safe after crashes/restarts.
        with self.store.lock, contextlib.closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT id,owner,charge FROM ai_jobs WHERE status IN ('queued','running')").fetchall():
                db.execute("UPDATE devices SET credits=credits+? WHERE device_id=?", (row["charge"], row["owner"]))
                db.execute("UPDATE ai_jobs SET status='failed',charge=0,error=?,finished_at=? WHERE id=?",
                           ("服务器重启或停止，未完成的编曲次数已退回。", time.time(), row["id"]))
        self._prune()

    def _prune(self):
        cutoff = time.time() - self.config["retention_days"] * 86400
        with self.store.lock, contextlib.closing(self.store.connect()) as db, db:
            # Keep a whole revision chain while a recent or active child needs it.
            db.execute("""DELETE FROM ai_jobs WHERE created_at<? AND status IN ('completed','failed')
                AND root_id NOT IN (SELECT root_id FROM ai_jobs WHERE created_at>=?
                OR status IN ('queued','running'))""", (cutoff, cutoff))

    async def _maintenance(self):
        while True:
            await asyncio.sleep(3600)
            self._prune()

    async def start(self):
        self.instance.acquire()
        try:
            self._recover()
        except Exception:
            self.instance.release()
            raise
        self.tasks = [asyncio.create_task(self._worker(), name=f"ai-worker-{i}") for i in range(self.config["workers"])]
        self.tasks.append(asyncio.create_task(self._maintenance(), name="ai-retention"))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        try:
            self._recover()
        finally:
            self.instance.release()

    def get(self, owner, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise Error("编曲任务不存在", 404)
        with contextlib.closing(self.store.connect()) as db:
            row = db.execute("SELECT * FROM ai_jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if row is None:
            raise Error("编曲任务不存在或不属于此设备", 404)
        return dict(row)

    def public(self, job):
        account = self.store.get_device(job["owner"])
        return {k: job[k] for k in ("id", "status", "players", "created_at", "revision", "name", "error")} | {
            "credits": account["credits"], "retention_days": self.config["retention_days"]}

    async def submit(self, owner, values, idempotency):
        if not isinstance(idempotency, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", idempotency):
            raise Error("请提供 16—80 位 Idempotency-Key，重试时保持不变")
        if not isinstance(values, dict) or set(values) - {"filename", "data", "players", "instruction", "parent_id"}:
            raise Error("编曲请求包含不支持的字段")
        filename, players = values.get("filename"), values.get("players")
        instruction, encoded = values.get("instruction", ""), values.get("data")
        if not isinstance(filename, str) or not 1 <= len(filename) <= 180:
            raise Error("歌曲文件名无效")
        filename = Path(filename.replace("\\", "/")).name
        if type(players) is not int or not 1 <= players <= 8:
            raise Error("演奏人数应为 1—8 人")
        if not isinstance(instruction, str) or len(instruction) > 500:
            raise Error("编曲要求最多 500 字")
        if not isinstance(encoded, str) or len(encoded) > (MAX_FILE + 2) // 3 * 4:
            raise Error("歌曲文件最大 4 MB", 413)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise Error("歌曲文件编码无效") from None
        if not 1 <= len(data) <= MAX_FILE:
            raise Error("歌曲文件为空或超过 4 MB", 413)
        request_hash = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with contextlib.closing(self.store.connect()) as db:
            existing = db.execute("SELECT * FROM ai_jobs WHERE owner=? AND idempotency=?", (owner, idempotency)).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise Error("相同重试标识对应的歌曲或要求不同", 409)
            return self.public(dict(existing))
        if not self.tasks:
            raise Error("AI 服务工作队列尚未启动", 503)
        provider = self.provider or make_provider(self.config)
        if hasattr(provider, "check"):
            provider.check()
        self.store.rate_limit("ai-submit:" + owner, 5, 60)
        source_hash = hashlib.sha256(data).hexdigest()
        # Parse before reserving credit. The parser itself has note/track limits.
        song = await asyncio.to_thread(parse_music, data, filename)
        summarize(song, players, instruction)
        now = time.time()
        job_id = secrets.token_hex(16)
        with self.store.lock, contextlib.closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM ai_jobs WHERE owner=? AND idempotency=?", (owner, idempotency)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Error("相同重试标识对应的歌曲或要求不同", 409)
                return self.public(dict(existing))
            daily = db.execute("SELECT COUNT(*) FROM ai_jobs WHERE owner=? AND created_at>?", (owner, now - 86400)).fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM ai_jobs WHERE status IN ('queued','running')").fetchone()[0]
            own_pending = db.execute("SELECT COUNT(*) FROM ai_jobs WHERE owner=? AND status IN ('queued','running')", (owner,)).fetchone()[0]
            if daily >= self.config["max_per_day"]:
                raise Error("已达到此设备最近 24 小时的编曲次数上限", 429)
            if own_pending:
                raise Error("此设备已有编曲任务，请等待完成", 409)
            if pending >= self.config["max_pending"] or self.queue.full():
                raise Error("编曲服务器繁忙，请稍后重试", 429)
            root_id, revision, parent_id = job_id, 0, values.get("parent_id")
            if parent_id is not None:
                if not isinstance(parent_id, str):
                    raise Error("修改任务标识无效")
                parent = db.execute("SELECT * FROM ai_jobs WHERE id=? AND owner=?", (parent_id, owner)).fetchone()
                if not parent or parent["status"] != "completed" or parent["source_hash"] != source_hash or parent["players"] != players:
                    raise Error("修改须引用此设备同一首原曲、相同人数的已完成任务")
                root_id = parent["root_id"]
                successful_revisions = db.execute("SELECT COUNT(*) FROM ai_jobs WHERE root_id=? AND revision>0 AND status='completed'", (root_id,)).fetchone()[0]
                if successful_revisions >= self.config["max_revisions"]:
                    raise Error("此编曲已达到修改次数上限")
                revision = successful_revisions + 1
            cost = self.config["credits_per_job"]
            debit = db.execute("UPDATE devices SET credits=credits-? WHERE device_id=? AND revoked=0 AND credits>=?", (cost, owner, cost))
            if debit.rowcount != 1:
                raise Error("AI 编曲次数不足，请先兑换充值 token", 402)
            db.execute("""INSERT INTO ai_jobs(id,owner,idempotency,request_hash,source_hash,root_id,parent_id,
                revision,filename,source,players,instruction,status,created_at,charge)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (job_id, owner, idempotency, request_hash, source_hash,
                root_id, parent_id, revision, filename, data, players, instruction, "queued", now, cost))
        self.queue.put_nowait(job_id)
        return self.public(self.get(owner, job_id))

    async def _worker(self):
        while True:
            job_id = await self.queue.get()
            try:
                with contextlib.closing(self.store.connect()) as db, db:
                    row = db.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
                    if not row or row["status"] != "queued":
                        continue
                    db.execute("UPDATE ai_jobs SET status='running' WHERE id=?", (job_id,))
                await asyncio.wait_for(self._execute(dict(row)), timeout=self.config["timeout"] + 15)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, ProviderError) else "编曲未能完成；本次次数已退回，请稍后重试。"
                # Never log model text, user instructions, API payloads or credentials.
                LOGGER.warning("AI job %s failed: %s", job_id, type(exc).__name__)
                with self.store.lock, contextlib.closing(self.store.connect()) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    job = db.execute("SELECT owner,charge,status FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
                    if job and job["status"] in {"queued", "running"}:
                        db.execute("UPDATE devices SET credits=credits+? WHERE device_id=?", (job["charge"], job["owner"]))
                        db.execute("UPDATE ai_jobs SET status='failed',error=?,charge=0,finished_at=? WHERE id=?", (message, time.time(), job_id))
            finally:
                self.queue.task_done()

    async def _execute(self, job):
        from fluentmelody.core.music import write_arrangement
        song = await asyncio.to_thread(parse_music, job["source"], job["filename"])
        previous = None
        if job["parent_id"]:
            parent = self.get(job["owner"], job["parent_id"])
            previous = json.loads(parent["directives"])
        context = summarize(song, job["players"], job["instruction"], previous)
        provider = self.provider or make_provider(self.config)
        directives = await provider.generate(context)
        plan = await asyncio.to_thread(apply_directives, song, directives, job["players"])
        with tempfile.TemporaryDirectory(prefix="fluentmelody-result-") as folder:
            target = Path(folder) / "arranged.nbs"
            await asyncio.to_thread(write_arrangement, plan, target)
            result = target.read_bytes()
            if len(result) > MAX_FILE:
                raise ProviderError("编曲文件超过大小限制。")
        with contextlib.closing(self.store.connect()) as db, db:
            db.execute("UPDATE ai_jobs SET status='completed',output=?,directives=?,name=?,finished_at=? WHERE id=? AND status='running'",
                       (result, json.dumps(directives, ensure_ascii=False), plan["name"], time.time(), job["id"]))


def create_app(config=None, provider=None):
    settings = configuration(config)
    store = Store(settings["database"], kind="ai")
    manager = JobManager(settings, store, provider)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(title="FluentMelody AI 编曲服务器", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.manager = store, manager
    install_error_handlers(app)

    @app.exception_handler(ProviderError)
    async def provider_error(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.get("/time")
    async def server_time():
        return {"server_time": time.time(), "service": "ai", "version": "1.0.0"}

    @app.post("/redeem")
    async def redeem(request: Request):
        return await redeem_request(request, store)

    @app.get("/me")
    async def me(request: Request):
        device = await verify_request(request, store, require_active=False)
        return {"device_id": device["device_id"], "credits": device["credits"],
                "expires_at": device["expires_at"], "credits_per_job": settings["credits_per_job"],
                "max_revisions": settings["max_revisions"], "max_per_day": settings["max_per_day"]}

    @app.post("/jobs")
    async def submit(request: Request):
        device = await verify_request(request, store)
        values = await json_body(request, max_bytes=6 * 1024 * 1024)
        return await manager.submit(device["device_id"], values, request.headers.get("Idempotency-Key"))

    @app.get("/jobs/{job_id}")
    async def status(job_id: str, request: Request):
        device = await verify_request(request, store)
        return manager.public(manager.get(device["device_id"], job_id))

    @app.get("/jobs/{job_id}/download")
    async def download(job_id: str, request: Request):
        device = await verify_request(request, store)
        job = manager.get(device["device_id"], job_id)
        if job["status"] != "completed" or not job["output"]:
            raise Error("编曲尚未完成或已经失败", 409)
        return Response(content=job["output"], media_type="application/octet-stream", headers={
            "Content-Disposition": 'attachment; filename="FluentMelody_AI.nbs"',
            "Cache-Control": "no-store"})

    return app


def main():
    parser = argparse.ArgumentParser(description="FluentMelody 独立 AI 编曲服务器")
    parser.add_argument("--config", default="ai.example.json")
    args = parser.parse_args()
    path = Path(args.config).resolve()
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict):
            raise ValueError("配置须为 JSON 对象")
        if "database" in values and not Path(values["database"]).is_absolute():
            values["database"] = str(path.parent / values["database"])
        settings = configuration(values)
        app = create_app(settings)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"AI 服务器配置错误：{exc}\n")
    import uvicorn
    uvicorn.run(app, host=settings["host"], port=settings["port"], workers=1,
                log_level="info", access_log=False, limit_concurrency=64,
                timeout_keep_alive=10)


if __name__ == "__main__":
    main()
