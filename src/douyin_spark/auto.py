from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .aiclient import AIError, generate_message
from .browser import ChatSession, ChatUnavailable, _is_image


@dataclass
class Job:
    kind: str  # login | probe | send | auto | close
    event_id: str = ""
    nickname: str = ""
    message: str = ""
    emoji: str = ""
    fallback: str = ""
    ai: dict[str, Any] = field(default_factory=dict)
    recent: list[str] = field(default_factory=list)
    approved: bool = True
    keyword: str = ""
    when: datetime = field(default_factory=datetime.now)
    delay: float = 0.0
    mode: str = "both"  # text | emoji | both


@dataclass
class JobResult:
    kind: str
    event_id: str
    status: str
    detail: str = ""
    message: str = ""
    ai_used: bool = False
    mode: str = "both"
    report: dict[str, Any] = field(default_factory=dict)


class AutomationService:
    """Single background thread owning Playwright, so GUI and jobs never race."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], ChatSession] | None = None,
        generator: Callable[..., str] | None = None,
        idle_seconds: float = 900,
    ) -> None:
        self._session_factory = session_factory or (lambda: ChatSession())
        self._generator = generator or generate_message
        self._session: ChatSession | None = None
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._results: queue.Queue[JobResult] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._generation = 0
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._idle_seconds = idle_seconds
        self.busy = False

    # ------------------------------------------------------------------ controls
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="douyin-automation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._jobs.put(Job(kind="close"))
        thread = self._thread
        if thread:
            thread.join(timeout=8)
        self._session = None
        self._thread = None

    def submit(self, job: Job) -> None:
        self.start()
        self._jobs.put(job)

    @property
    def idle_seconds(self) -> float:
        return self._idle_seconds

    @idle_seconds.setter
    def idle_seconds(self, value: float) -> None:
        self._idle_seconds = float(value)

    def cancel_pending(self) -> int:
        """Drop queued jobs; a job already inside Playwright finishes its current step."""
        with self._lock:
            self._generation += 1
        dropped = 0
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
            dropped += 1
        return dropped

    def poll(self) -> list[JobResult]:
        items: list[JobResult] = []
        while True:
            try:
                items.append(self._results.get_nowait())
            except queue.Empty:
                return items

    # --------------------------------------------------------------------- thread
    def _emit(self, result: JobResult) -> None:
        self._results.put(result)

    def _ensure_session(self) -> ChatSession:
        if self._session is None or not self._session.alive:
            self._session = self._session_factory()
        return self._session

    def _close_if_idle(self) -> None:
        if self._session is None:
            return
        if self._idle_seconds <= 0:
            return
        if time.monotonic() - self._last_used < self._idle_seconds:
            return
        try:
            self._session.stop()
        finally:
            self._session = None

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                job = self._jobs.get(timeout=1.0)
            except queue.Empty:
                self._close_if_idle()
                continue
            if job.kind == "close":
                break
            with self._lock:
                generation = self._generation
            self.busy = True
            try:
                self._handle(job, generation)
            except ChatUnavailable as exc:
                self._emit(JobResult(job.kind, job.event_id, "failed", str(exc), job.message, mode=job.mode))
            except Exception as exc:  # pragma: no cover - defensive
                self._emit(JobResult(job.kind, job.event_id, "failed", f"自动化异常：{exc}", job.message, mode=job.mode))
            finally:
                self.busy = False
                self._last_used = time.monotonic()
        if self._session is not None:
            try:
                self._session.stop()
            finally:
                self._session = None

    def _handle(self, job: Job, generation: int) -> None:
        if job.kind == "login":
            session = self._ensure_session()
            state = session.open_chat()
            if state == "ok":
                self._emit(JobResult("login", "", "login_ok", "抖音聊天页已登录。"))
            else:
                self._emit(JobResult("login", "", "needs_login", "已在自动化浏览器中打开聊天页，请扫码登录。"))
            return
        if job.kind == "probe":
            session = self._ensure_session()
            report = session.probe()
            ok = bool(report.get("login")) and bool(report.get("editor"))
            self._emit(
                JobResult(
                    "probe",
                    "",
                    "probe_ok" if ok else "probe_warn",
                    str(report.get("detail") or ""),
                    report=report,
                )
            )
            return
        if job.kind == "lookup":
            session = self._ensure_session()
            try:
                candidates = session.lookup_user(job.keyword)
            except ChatUnavailable as exc:
                self._emit(JobResult("lookup", "", "lookup_error", str(exc)))
                return
            detail = (
                f"按「{job.keyword}」找到 {len(candidates)} 个候选"
                if candidates
                else "没解析到用户卡片，浏览器已停在搜索结果页，可直接查看"
            )
            self._emit(
                JobResult(
                    "lookup",
                    "",
                    "lookup_ok" if candidates else "lookup_warn",
                    detail,
                    report={"candidates": candidates},
                )
            )
            return
        if job.kind == "send":
            session = self._ensure_session()
            if not job.message.strip() and job.emoji:
                status, detail = session.send_image(job.nickname, job.emoji)
            else:
                status, detail = session.send_text(job.nickname, job.message, job.emoji)
            self._emit(
                JobResult(
                    "send",
                    job.event_id,
                    status if status != "sent" else "sent",
                    detail,
                    job.message,
                )
            )
            return
        if job.kind == "auto":
            self._handle_auto(job, generation)
            return

    def _generate(self, job: Job) -> tuple[str, bool, str]:
        """Return (text, ai_used, note). Always falls back to the template text."""
        fallback = (job.fallback or job.message).strip()
        cfg = job.ai or {}
        if not cfg.get("enabled"):
            return fallback, False, "未开启 AI，使用模板文案"
        try:
            text = self._generator(
                cfg, job.nickname, job.fallback or job.message, when=job.when, recent=job.recent
            )
            return text, True, "AI 生成"
        except AIError as exc:
            return fallback, False, f"AI 失败已回退模板：{exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return fallback, False, f"AI 异常已回退模板：{exc}"

    def _handle_auto(self, job: Job, generation: int) -> None:
        image_path = job.emoji if (job.emoji and _is_image(job.emoji)) else ""
        image_only = bool(image_path) and (job.mode == "emoji" or not job.message.strip())
        if image_only:
            text, ai_used, note = "", False, "只发表情，不生成文案"
        elif job.mode == "emoji" and job.emoji:
            text, ai_used, note = job.emoji, False, "只发表情（文字形式发出）"
        else:
            text, ai_used, note = self._generate(job)
        if not job.approved:
            self._emit(JobResult("auto", job.event_id, "needs_confirm", note, text, ai_used, job.mode))
            return
        self._wait(job.delay)
        with self._lock:
            if generation != self._generation:
                self._emit(JobResult("auto", job.event_id, "cancelled", "发送已取消", text, ai_used, job.mode))
                return
        session = self._ensure_session()
        if image_only:
            status, detail = session.send_image(job.nickname, image_path)
        else:
            emoji_arg = "" if job.mode == "text" else job.emoji
            status, detail = session.send_text(job.nickname, text, emoji_arg)
        detail = f"{detail}；{note}" if detail else note
        if status == "sent":
            self._emit(JobResult("auto", job.event_id, "sent", detail, text, ai_used, job.mode))
        else:
            self._emit(JobResult("auto", job.event_id, status or "failed", detail, text, ai_used, job.mode))

    def _wait(self, seconds: float) -> None:
        """Random pre-send delay, interruptible by stop()."""
        remaining = max(0.0, float(seconds))
        while remaining > 0 and not self._stopping.is_set():
            step = min(0.5, remaining)
            time.sleep(step)
            remaining -= step
