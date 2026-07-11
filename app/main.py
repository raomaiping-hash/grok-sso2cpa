"""FastAPI application for the local SSO Bridge product."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import secrets
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .converter import (
    convert_auth_documents,
    load_sso_list,
    merge_auth_payload,
    serialize_json,
    token_to_auth_entry,
    token_to_cliproxy_entry,
)
from .oauth import MissingOAuthDependencyError, RateLimitedError, backoff_sec, sso_to_token

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
JOB_ROOT = ROOT / "data" / "jobs"
JOB_RETENTION_SECONDS = 24 * 60 * 60
MAX_AUTH_BYTES = 50_000_000
JOB_ROOT.mkdir(parents=True, exist_ok=True)


def purge_expired_job_dirs(protected: set[str] | None = None) -> None:
    cutoff = time.time() - JOB_RETENTION_SECONDS
    protected = protected or set()
    for child in JOB_ROOT.iterdir():
        try:
            if child.is_dir() and child.name not in protected and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
        except FileNotFoundError:
            continue


purge_expired_job_dirs()


class AuthInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=2_000_000)


class JobRequest(BaseModel):
    mode: Literal["sso", "from_auth"] = "sso"
    sso_text: str = ""
    auth_files: list[AuthInput] = Field(default_factory=list)
    email_override: str = Field(default="", max_length=320)
    target_cliproxy: bool = True
    target_grok: bool = True
    delay: float = Field(default=20, ge=0, le=900)
    max_delay: float = Field(default=180, ge=30, le=1800)
    retries: int = Field(default=8, ge=1, le=20)
    account_retries: int = Field(default=3, ge=1, le=10)
    concurrency: int = Field(default=4, ge=1, le=64)


@dataclass
class Job:
    id: str
    request: JobRequest
    output_dir: Path
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    total: int = 0
    success: int = 0
    failed: int = 0
    current: int = 0
    current_label: str = ""
    logs: list[str] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, message: str) -> None:
        with self._lock:
            stamp = time.strftime("%H:%M:%S")
            self.logs.append(f"{stamp}  {message}")
            self.logs = self.logs[-300:]

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status
            if status == "running":
                self.started_at = time.time()
            if status in {"completed", "failed"}:
                self.finished_at = time.time()

    def write_file(self, filename: str, content: bytes) -> str:
        safe_name = Path(filename).name
        if not safe_name:
            raise ValueError("输出文件名为空")
        target = self.output_dir / safe_name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 2
            while target.exists():
                target = self.output_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        target.write_bytes(content)
        with self._lock:
            self.files.append({"name": target.name, "size": len(content)})
        return target.name

    def snapshot(self, include_logs: bool = True) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {
                "id": self.id,
                "status": self.status,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total": self.total,
                "success": self.success,
                "failed": self.failed,
                "current": self.current,
                "current_label": self.current_label,
                "files": [
                    {**item, "download_url": f"/api/jobs/{self.id}/download/{item['name']}"}
                    for item in self.files
                ],
                "error": self.error,
                "accounts": list(self.accounts),
            }
            if include_logs:
                result["logs"] = list(self.logs)
            return result

    def redact_input(self) -> None:
        """Drop raw cookies/file bodies after the worker no longer needs them."""

        self.request.sso_text = ""
        self.request.auth_files.clear()


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, request: JobRequest) -> Job:
        self.purge_expired()
        job_id = secrets.token_urlsafe(8)
        job = Job(job_id, request, JOB_ROOT / job_id)
        job.output_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self) -> list[Job]:
        self.purge_expired()
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)[:20]

    def purge_expired(self) -> None:
        cutoff = time.time() - JOB_RETENTION_SECONDS
        with self._lock:
            expired = [job_id for job_id, job in self._jobs.items() if job.finished_at and job.finished_at < cutoff]
            protected = {job_id for job_id, job in self._jobs.items() if job.status in {"queued", "running"}}
            for job_id in expired:
                self._jobs.pop(job_id, None)
        purge_expired_job_dirs(protected)


store = JobStore()
app = FastAPI(title="SSO Bridge", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host == "localhost"
    if not is_loopback:
        return JSONResponse(status_code=403, content={"detail": "SSO Bridge 只允许本机访问"})
    return await call_next(request)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "sso-bridge"}


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [job.snapshot(include_logs=False) for job in store.recent()]


@app.post("/api/jobs", status_code=202)
async def create_job(request: JobRequest) -> dict[str, Any]:
    if not request.target_cliproxy and not request.target_grok:
        raise HTTPException(status_code=422, detail="至少选择一种输出格式")
    if request.mode == "sso" and not load_sso_list(request.sso_text):
        raise HTTPException(status_code=422, detail="请输入至少一个 SSO Cookie")
    if request.mode == "from_auth" and not request.auth_files:
        raise HTTPException(status_code=422, detail="请上传至少一个 auth JSON 文件")
    if request.mode == "from_auth" and not request.target_cliproxy:
        raise HTTPException(status_code=422, detail="已有 auth 文件转换只支持 cliproxyapi 输出")
    if sum(len(item.content.encode("utf-8")) for item in request.auth_files) > MAX_AUTH_BYTES:
        raise HTTPException(status_code=413, detail="上传文件总大小不能超过 50 MB")
    job = store.create(request)
    asyncio.create_task(asyncio.to_thread(run_job, job))
    return job.snapshot()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/download/all.zip")
def download_all(job_id: str) -> FileResponse:
    job = _require_job(job_id)
    archive = job.output_dir / "sso-bridge-export.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in job.files:
            path = job.output_dir / item["name"]
            if path.exists():
                bundle.write(path, arcname=path.name)
    return FileResponse(archive, filename="sso-bridge-export.zip", media_type="application/zip")


@app.get("/api/jobs/{job_id}/download/{filename:path}")
def download_file(job_id: str, filename: str) -> FileResponse:
    job = _require_job(job_id)
    safe_name = Path(filename).name
    path = (job.output_dir / safe_name).resolve()
    if job.output_dir.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="输出文件不存在")
    return FileResponse(path, filename=safe_name, media_type="application/json")


def _require_job(job_id: str) -> Job:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def run_job(job: Job) -> None:
    job.set_status("running")
    try:
        if job.request.mode == "sso":
            run_sso_job(job)
        else:
            run_auth_job(job)
        if job.success:
            job.set_status("completed")
            job.log(f"任务完成：{job.success} 个成功，{job.failed} 个失败")
        else:
            job.set_status("failed")
            job.log("任务未生成任何输出")
    except MissingOAuthDependencyError as exc:
        job.error = str(exc)
        job.log(str(exc))
        job.set_status("failed")
    except Exception as exc:
        job.error = str(exc)
        job.log(f"任务异常：{exc}")
        job.set_status("failed")
    finally:
        job.redact_input()


def run_sso_job(job: Job) -> None:
    request = job.request
    accounts = load_sso_list(request.sso_text)
    request.sso_text = ""
    job.total = len(accounts)
    job.log(f"已读取 {len(accounts)} 个 SSO 输入，并发数 {request.concurrency}")
    grok_payload: dict[str, Any] = {}

    def process_account(index: int, cookie: str, line_email: str) -> tuple[int, str, dict[str, Any] | None, Exception | None]:
        label = line_email or f"账号 {index}"
        job.log(f"[{index}/{len(accounts)}] 开始处理 {label}")
        token: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(1, request.account_retries + 1):
            try:
                token = sso_to_token(
                    cookie,
                    max_retries=request.retries,
                    base_delay=request.delay or 15,
                    log=job.log,
                )
                if token:
                    break
                break
            except RateLimitedError as exc:
                last_error = exc
                if attempt < request.account_retries:
                    cool = backoff_sec(request.delay or 15, attempt, request.max_delay)
                    job.log(f"账号触发限流，冷却 {cool:.0f}s 后重试 ({attempt}/{request.account_retries})")
                    time.sleep(cool)
        return index, line_email, token, last_error

    with ThreadPoolExecutor(max_workers=request.concurrency, thread_name_prefix="sso-account") as executor:
        futures = [executor.submit(process_account, index, cookie, email) for index, (cookie, email) in enumerate(accounts, start=1)]
        for future in as_completed(futures):
            index, line_email, token, last_error = future.result()
            completed = job.success + job.failed + 1
            job.current = completed
            job.current_label = f"并发处理中 · {completed}/{len(accounts)}"
            if not token:
                job.failed += 1
                detail = str(last_error) if last_error else "SSO 转换失败"
                job.accounts.append({"label": line_email or f"账号 {index}", "status": "failed", "error": detail})
                job.log(f"[{index}/{len(accounts)}] 失败：{detail}")
                continue

            email = request.email_override or line_email
            output_names: list[str] = []
            if request.target_cliproxy:
                filename, entry = token_to_cliproxy_entry(token, email=email)
                output_names.append(job.write_file(filename, serialize_json(entry, compact=True)))
            if request.target_grok:
                key, entry = token_to_auth_entry(token, email=email)
                grok_payload = merge_auth_payload(grok_payload, entry, unique=True)
                output_names.append(f"auth.json ({key})")

            job.success += 1
            job.accounts.append({"label": email or f"账号 {index}", "status": "success", "files": output_names})
            job.log(f"[{index}/{len(accounts)}] 完成：{', '.join(output_names)}")

    if request.target_grok and grok_payload:
        job.write_file("auth.json", serialize_json(grok_payload))


def run_auth_job(job: Job) -> None:
    request = job.request
    sources = list(request.auth_files)
    request.auth_files.clear()
    prepared: list[tuple[Any, list[tuple[str, dict[str, Any]]] | None, str]] = []
    for source in sources:
        try:
            documents = convert_auth_documents(json.loads(source.content), email_override=request.email_override)
            prepared.append((source, documents, ""))
            job.total += max(1, len(documents))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            prepared.append((source, None, str(exc)))
            job.total += 1
    job.log(f"已读取 {len(sources)} 个 auth JSON 文件，共 {job.total} 个账号输出")
    if request.target_grok:
        job.log("已有 auth 文件转换模式会生成 cliproxyapi 输出，源格式不会被覆盖")

    for index, (source, documents, error) in enumerate(prepared, start=1):
        job.current = index
        job.current_label = source.name
        if documents is None:
            job.failed += 1
            job.accounts.append({"label": source.name, "status": "failed", "error": error})
            job.log(f"[{index}/{len(sources)}] 失败：{source.name} · {error}")
            continue
        output_names: list[str] = []
        for filename, entry in documents:
            output_name = job.write_file(filename, serialize_json(entry, compact=True))
            output_names.append(output_name)
            job.success += 1
        job.accounts.append({"label": source.name, "status": "success", "files": output_names})
        job.log(f"[{index}/{len(sources)}] 完成：{', '.join(output_names)}")
