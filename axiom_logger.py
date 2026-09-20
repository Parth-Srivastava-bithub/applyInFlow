"""
axiom_logger.py — Centralized, Asynchronous Axiom Logging Client for AutoApply

Features:
- Non-blocking background ingestion worker with automatic batching and graceful flush on exit.
- Structured correlation IDs: `run_id`, `step_id`, `service`, `status`, `duration_ms`.
- Try-catch context manager: `with logger.step("01_SCRAPE"): ...`
- Captures full stack traces, input context, and execution latencies.
- Dual output: Rich console logging + Axiom cloud ingest.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 on Windows console to prevent charmap encoding errors with unicode arrows/emojis
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Setup clean console logger
console_logger = logging.getLogger("AutoApply.Axiom")
if not console_logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    console_logger.addHandler(ch)
    console_logger.setLevel(logging.INFO)


class AxiomIngestQueue:
    """Thread-safe background queue that batches log events and ships them to Axiom."""

    def __init__(self, api_key: str | None = None, ingest_url: str | None = None):
        self.api_key = api_key or os.getenv("AXIOM_API_KEY") or os.getenv("AXIOM_TOKEN")
        self.dataset = os.getenv("AXIOM_DATASET", "applyinflow")
        default_url = f"https://us-east-1.aws.edge.axiom.co/v1/ingest/{self.dataset}"
        self.ingest_url = ingest_url or os.getenv("AXIOM_INGEST_URL", default_url)

        self.queue: queue.Queue[dict] = queue.Queue(maxsize=10000)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="Axiom-Ingest-Worker")
        
        if self.api_key:
            self._worker_thread.start()
            atexit.register(self.flush_and_stop)
        else:
            console_logger.warning("AXIOM_API_KEY not found; logs will be output to console only.")

    def enqueue(self, event: dict):
        """Put an event into the background queue."""
        if not self.api_key:
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            console_logger.warning("Axiom log queue is full; dropping event.")

    def _worker_loop(self):
        """Worker thread loop to batch and send logs."""
        batch: list[dict] = []
        last_flush = time.time()

        while not self._stop_event.is_set():
            try:
                # Wait up to 1 second for a new event
                event = self.queue.get(timeout=1.0)
                batch.append(event)
                self.queue.task_done()
            except queue.Empty:
                pass

            # Flush conditions: >= 25 events OR >= 2 seconds elapsed since last flush
            now = time.time()
            if batch and (len(batch) >= 25 or (now - last_flush) >= 2.0 or self._stop_event.is_set()):
                self._send_batch(batch)
                batch = []
                last_flush = now

        # Drain any remaining items
        while not self.queue.empty():
            try:
                batch.append(self.queue.get_nowait())
                self.queue.task_done()
            except queue.Empty:
                break
        if batch:
            self._send_batch(batch)

    def _send_batch(self, batch: list[dict]):
        """Send a batch of events to the Axiom Edge Ingest API."""
        if not self.api_key or not batch:
            return

        try:
            payload = json.dumps(batch).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "AutoApply-AxiomLogger/1.0",
            }
            req = urllib.request.Request(self.ingest_url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status not in (200, 201, 204):
                    console_logger.warning(f"Axiom ingest response status: {resp.status}")
        except Exception as e:
            console_logger.warning(f"Failed to ship batch of {len(batch)} logs to Axiom: {e}")

    def flush_and_stop(self):
        """Flush pending logs on process exit."""
        self._stop_event.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)


# Global singleton queue instance
_global_queue = AxiomIngestQueue()


class AxiomLogger:
    """Structured step-by-step logger for AutoApply runs."""

    def __init__(self, service: str = "pipeline", run_id: str | None = None):
        self.service = service
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"

    def new_run(self, run_id: str | None = None) -> str:
        """Assign a new run correlation ID."""
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"
        return self.run_id

    def log(
        self,
        level: str,
        step_id: str,
        message: str,
        status: str = "info",
        duration_ms: float | None = None,
        error: str | None = None,
        stack_trace: str | None = None,
        **context: Any,
    ):
        """Send a structured event to Axiom and local console."""
        timestamp = datetime.now(timezone.utc).isoformat()

        event = {
            "_time": timestamp,
            "run_id": self.run_id,
            "step_id": step_id,
            "service": self.service,
            "level": level.lower(),
            "status": status,
            "message": message,
        }

        if duration_ms is not None:
            event["duration_ms"] = round(duration_ms, 2)
        if error:
            event["error"] = str(error)
        if stack_trace:
            event["stack_trace"] = stack_trace
        if context:
            event["context"] = context

        # Print cleanly to console
        lvl_upper = level.upper()
        dur_str = f" ({duration_ms:.1f}ms)" if duration_ms is not None else ""
        msg_str = f"[{self.run_id}][{step_id}] {status.upper()}: {message}{dur_str}"
        
        if lvl_upper == "ERROR":
            console_logger.error(msg_str + (f"\n  Error: {error}" if error else ""))
        elif lvl_upper in ("WARN", "WARNING"):
            console_logger.warning(msg_str)
        else:
            console_logger.info(msg_str)

        # Enqueue for Axiom upload
        _global_queue.enqueue(event)

    def info(self, step_id: str, message: str, **context: Any):
        self.log("info", step_id, message, status="info", **context)

    def success(self, step_id: str, message: str, duration_ms: float | None = None, **context: Any):
        self.log("info", step_id, message, status="success", duration_ms=duration_ms, **context)

    def warn(self, step_id: str, message: str, **context: Any):
        self.log("warn", step_id, message, status="warning", **context)

    def warning(self, step_id: str, message: str, **context: Any):
        self.log("warn", step_id, message, status="warning", **context)

    def error(self, step_id: str, message: str, error: Any = None, stack_trace: str | None = None, **context: Any):
        err_msg = str(error) if error else message
        st = stack_trace or (traceback.format_exc() if sys.exc_info()[0] else None)
        self.log("error", step_id, message, status="error", error=err_msg, stack_trace=st, **context)

    @contextmanager
    def step(self, step_id: str, description: str = "", **context: Any) -> Generator[dict, None, None]:
        """
        Context manager wrapping a step with automated start/success/error lifecycle:
        
        with logger.step("01_SCRAPE", query="AI Engineer") as meta:
            data = scrape()
            meta["items_found"] = len(data)
        """
        step_meta: dict = dict(context)
        t0 = time.perf_counter()
        
        start_desc = f"{description} (started)" if description else "Started"
        self.log("info", step_id, start_desc, status="started", **step_meta)
        
        try:
            yield step_meta
            duration_ms = (time.perf_counter() - t0) * 1000
            finish_desc = f"{description} (completed successfully)" if description else "Completed successfully"
            self.success(step_id, finish_desc, duration_ms=duration_ms, **step_meta)
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            st = traceback.format_exc()
            err_desc = f"{description} failed: {exc}" if description else f"Failed: {exc}"
            self.error(step_id, err_desc, error=str(exc), stack_trace=st, duration_ms=duration_ms, **step_meta)
            raise exc


def get_logger(service: str = "pipeline", run_id: str | None = None) -> AxiomLogger:
    """Get a configured AxiomLogger instance."""
    return AxiomLogger(service=service, run_id=run_id)
