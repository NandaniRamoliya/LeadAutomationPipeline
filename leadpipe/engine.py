"""The 24/7 loop. Wakes up every `engine.tick_seconds`, does whatever is due, sleeps again.

Priority every cycle (most valuable first):
    1. replies (inbox)         people waiting for an answer come first
    2. timers + onboarding     close finished tracks, move booked calls on, advance new clients
    3. sending                 one email per gap, inside office hours and daily caps
    4. writing                 drafts for every follow-up that is due soon
    5. research + new leads    audit websites, enroll the best leads, find more businesses when the queue is low
    6. daily digest            one summary to you per day
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import onboarding
from .analytics import digest_text
from .config import Config
from .db import DB, iso, now_iso, parse_iso, utcnow
from .discovery import discovery_due, run_discovery
from .enrich import enrich_pending
from .handler import process_inbox
from .llm import LLM
from .notify import notify
from .pipeline import enroll_new_contacts, process_timers
from .sender import Mailer, engine_tz, send_due
from .writer import Writer, write_due

log = logging.getLogger("leadpipe.engine")


class Engine:
    def __init__(self, db: DB, cfg: Config):
        self.db, self.cfg = db, cfg
        self.llm = LLM(cfg)
        self.writer = Writer(db, cfg, self.llm)
        self.mailer = Mailer(cfg)
        self.stop_event = threading.Event()

    # ---- scheduling helpers ------------------------------------------------
    def _due(self, key: str, minutes: float) -> bool:
        last = parse_iso(self.db.kv_get(f"job:{key}"))
        return not last or utcnow() - last >= timedelta(minutes=minutes)

    def _mark(self, key: str) -> None:
        self.db.kv_set(f"job:{key}", now_iso())

    def _run(self, name: str, fn, *args) -> int:
        try:
            result = fn(*args)
            return int(result or 0)
        except Exception as exc:  # a failing job must never kill the loop
            log.exception("job %s failed", name)
            self.db.log("engine", f"job '{name}' failed: {exc}", level="error")
            return 0

    # ---- one cycle -------------------------------------------------------------
    def cycle(self) -> dict:
        e = self.cfg.section("engine")
        db, cfg = self.db, self.cfg
        out: dict = {}
        db.kv_set("heartbeat", now_iso())

        if self._due("inbox", float(e.get("inbox_every_minutes", 5))):
            out["replies"] = self._run("inbox", process_inbox, db, cfg, self.writer, self.llm)
            self._mark("inbox")

        out["timers"] = self._run("timers", process_timers, db, cfg)
        out["onboarding"] = self._run("onboarding", onboarding.tick, db, cfg, self.writer)

        out["sent"] = self._run("send", send_due, db, cfg, self.mailer)

        if self._due("write", float(e.get("write_every_minutes", 10))):
            out["enrolled"] = self._run("enroll", enroll_new_contacts, db, cfg)
            out["drafted"] = self._run("write", write_due, db, cfg, self.writer)
            self._mark("write")

        if self._due("enrich", float(e.get("enrich_every_minutes", 20))):
            out["enriched"] = self._run("enrich", enrich_pending, db, cfg, None, self.llm)
            self._mark("enrich")
            if discovery_due(db, cfg):
                out["discovered"] = self._run("discover", run_discovery, db, cfg)

        self._daily_digest()
        return out

    def _daily_digest(self) -> None:
        hour = int(self.cfg.get("notifications.daily_digest_hour", 19))
        local = datetime.now(engine_tz(self.cfg))
        key = f"digest:{local.date().isoformat()}"
        if local.hour >= hour and not self.db.kv_get(key):
            self.db.kv_set(key, True)
            text = digest_text(self.db, self.cfg)
            notify(self.db, self.cfg, f"Daily summary {local.date().isoformat()}", text)

    # ---- forever -------------------------------------------------------------------
    def run_forever(self) -> None:
        lock = _acquire_lock(self.cfg.data_dir / "engine.lock")
        tick = float(self.cfg.get("engine.tick_seconds", 60))
        self.db.log("engine", f"engine started (mode={self.mailer.mode}, ai={self.llm.provider})")
        log.info("Engine running. Sending mode: %s. AI: %s. Press Ctrl+C to stop.", self.mailer.mode, self.llm.provider)
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                result = self.cycle()
                busy = {k: v for k, v in result.items() if v}
                if busy:
                    log.info("cycle: %s", busy)
                self.stop_event.wait(max(1.0, tick - (time.monotonic() - started)))
        finally:
            self.db.log("engine", "engine stopped")
            lock.unlink(missing_ok=True)

    def install_signal_handlers(self) -> None:
        def _stop(*_):
            log.info("Stopping after the current step...")
            self.stop_event.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except ValueError:  # not in main thread
                pass


def _acquire_lock(path: Path) -> Path:
    """Only one engine per database: two engines would double-send."""
    if path.exists():
        try:
            pid = int(path.read_text().strip() or 0)
        except ValueError:
            pid = 0
        alive = False
        if pid and pid != os.getpid():
            try:
                os.kill(pid, 0)
                alive = True
            except (OSError, PermissionError):
                alive = False
        if alive:
            raise SystemExit(f"Another engine is already running (pid {pid}). Stop it first, or delete {path}.")
    path.write_text(str(os.getpid()))
    return path


def engine_status(db: DB, cfg: Config) -> dict:
    hb = parse_iso(db.kv_get("heartbeat"))
    tick = float(cfg.get("engine.tick_seconds", 60))
    alive = bool(hb and utcnow() - hb < timedelta(seconds=max(tick * 3, 180)))
    return {"alive": alive, "heartbeat": iso(hb) if hb else None,
            "paused": bool(db.kv_get("sending_paused", False)),
            "mode": cfg.get("sending.mode", "dry_run"),
            "autopilot_cold": bool(db.kv_get("autopilot_cold", cfg.get("approval.autopilot_cold", False))),
            "autopilot_replies": bool(db.kv_get("autopilot_replies", cfg.get("approval.autopilot_replies", False))),
            "last_inbox_at": db.kv_get("last_inbox_at"), "last_discovery_at": db.kv_get("last_discovery_at")}
