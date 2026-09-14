"""Trusted local extensions and a bounded, killable discovery worker.

The worker is a resource/liveness boundary, not a Python security sandbox.
Private modules run trusted code with the bot user's OS privileges.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

from feed_config import FeedConfigError, list_feeds, validate_feed
from providers.common import MAX_ENTRIES, error_code, health, normalize_item

SOURCE_DEADLINE = 25.0
CYCLE_DEADLINE = 120.0
MAX_WORKER_OUTPUT = 1024 * 1024
MAX_PRIVATE_PROVIDERS = 16
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def _private_descriptors() -> list[dict]:
    import config
    value = getattr(config, "HERALD_PRIVATE_PROVIDERS", [])
    if isinstance(value, str):
        if len(value) > 262144:
            raise FeedConfigError("Private provider config exceeds limit")
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            raise FeedConfigError("Private provider config JSON is invalid") from None
    if not isinstance(value, list) or len(value) > MAX_PRIVATE_PROVIDERS:
        raise FeedConfigError("Invalid private provider list")
    descriptors = []
    for descriptor in value:
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "module", "source"}:
            raise FeedConfigError("Invalid private provider fields")
        module = descriptor["module"]
        path = descriptor["path"]
        if not isinstance(module, str) or len(module) > 150 or not _MODULE.fullmatch(module):
            raise FeedConfigError("Invalid private provider module name")
        if not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute():
            raise FeedConfigError("Private provider path must be an absolute local directory")
        source = validate_feed(descriptor["source"])
        source = dict(source, source_id=source["id"], provider_id="local")
        descriptors.append(dict(source, _plugin={"path": path, "module": module}))
    return descriptors


def _configured_descriptors() -> list[dict]:
    from providers.gamerpower import configured_source
    sources = [configured_source()]
    sources.extend(dict(feed, source_id=feed["id"], provider_id="rss") for feed in list_feeds())
    sources.extend(_private_descriptors())
    if len({source["id"] for source in sources}) != len(sources):
        raise FeedConfigError("Duplicate source id across configured providers")
    for source in sources:
        canonical = json.dumps({k: v for k, v in source.items() if k != "policy_digest"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        source["policy_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return sources


def get_configured_sources() -> list[dict]:
    """Source policies for delivery, commands and role UI; excludes plugin paths."""
    return [{key: value for key, value in source.items() if not key.startswith("_")}
            for source in _configured_descriptors()]


def _load_local_module(descriptor: dict):
    root = Path(descriptor["path"]).resolve(strict=True)
    if not root.is_dir():
        raise FeedConfigError("Private provider path is not a directory")
    candidate = root.joinpath(*descriptor["module"].split(".")).with_suffix(".py").resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise FeedConfigError("Private provider module must be a Python file beneath configured path")
    spec = importlib.util.spec_from_file_location("_herald_local_provider", candidate)
    if spec is None or spec.loader is None:
        raise FeedConfigError("Private provider module cannot load")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "fetch_items", None)):
        raise FeedConfigError("Private provider must export fetch_items")
    return module


def _fetch_local(source: dict) -> dict:
    module = _load_local_module(source["_plugin"])
    payload = module.fetch_items()
    if not isinstance(payload, list):
        raise ValueError("Private provider must return a list")
    items, invalid = [], 0
    for raw in payload[:MAX_ENTRIES]:
        try:
            items.append(normalize_item(raw, source))
        except (ValueError, TypeError, AttributeError):
            invalid += 1
    error = "invalid_entries" if invalid else "entry_limit_reached" if len(payload) > MAX_ENTRIES else ""
    status = "degraded" if error and items else "failed" if error else "healthy" if items else "empty"
    return {"items": items, "health": health(source, status, len(items), error)}


def fetch_source(source: dict, *, preview: bool = False) -> dict:
    if not source["enabled"] and not preview:
        return {"items": [], "health": health(source, "disabled")}
    source = dict(source, enabled=True) if preview else source
    try:
        if source["provider_id"] == "local":
            return _fetch_local(source)
        if source["provider_id"] == "gamerpower":
            from providers.gamerpower import fetch_source as fetch
        else:
            from providers.rss import fetch_source as fetch
        return fetch(source)
    except Exception as exc:
        return {"items": [], "health": health(source, "failed", error=error_code(exc))}


class DiscoveryWorker:
    def __init__(self, *, source_deadline=SOURCE_DEADLINE, cycle_deadline=CYCLE_DEADLINE):
        self.source_deadline = source_deadline
        self.cycle_deadline = cycle_deadline
        self._lock = asyncio.Lock()
        self.last_health: list[dict] = []
        self.last_error = ""
        self.last_cycle_at = ""
        self.last_success_at = ""

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def _exchange(self, process, request: dict) -> dict:
        process.stdin.write(json.dumps(request).encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        output = bytearray()
        while True:
            chunk = await process.stdout.read(16384)
            if not chunk:
                break
            if len(output) + len(chunk) > MAX_WORKER_OUTPUT:
                raise ValueError("worker_output_limit_exceeded")
            output.extend(chunk)
        await process.wait()
        if process.returncode != 0:
            raise ValueError("worker_failed")
        result = json.loads(output)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list) or not isinstance(result.get("health"), dict):
            raise ValueError("invalid_worker_result")
        return result

    async def _run_one(self, source: dict, timeout: float, *, preview: bool = False) -> dict:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()), "--worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parent))
            return await asyncio.wait_for(self._exchange(process, {"source": source, "preview": preview}), timeout)
        except asyncio.TimeoutError:
            return {"items": [], "health": health(source, "failed", error="worker_deadline_exceeded")}
        except Exception as exc:
            return {"items": [], "health": health(source, "failed", error=error_code(exc))}
        finally:
            # Cancellation and timeout both kill and reap before lock is released.
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    async def run(self, source_id: str | None = None, *, preview: bool = False) -> dict:
        if self._lock.locked():
            return {"items": [], "health": [], "busy": True, "error": "discovery_already_running"}
        async with self._lock:
            try:
                sources = _configured_descriptors()
            except Exception as exc:
                self.last_error = error_code(exc)
                return {"items": [], "health": [{"source_id": "configuration", "name": "Source configuration", "status": "failed", "item_count": 0, "error": self.last_error}]}
            if source_id is not None:
                sources = [source for source in sources if source["id"] == source_id]
                if not sources:
                    return {"items": [], "health": [], "error": "source_not_found"}
            items, records = [], []
            deadline = time.monotonic() + self.cycle_deadline
            for source in sources:
                if not source["enabled"] and not preview:
                    records.append(health(source, "disabled"))
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    records.append(health(source, "failed", error="cycle_deadline_exceeded"))
                    continue
                result = await self._run_one(source, min(self.source_deadline, remaining), preview=preview)
                items.extend(result["items"])
                records.append(result["health"])
            if not preview:
                from datetime import datetime, timezone
                self.last_health = records
                self.last_cycle_at = datetime.now(timezone.utc).isoformat()
                self.last_error = ",".join(sorted({r["error"] for r in records if r["error"]}))[:160]
                if any(r["status"] in {"healthy", "empty"} for r in records):
                    self.last_success_at = self.last_cycle_at
            return {"items": items, "health": records}


discovery_worker = DiscoveryWorker()


async def fetch_source_preview(source_id: str) -> dict:
    """Fetch only: does not import storage or enqueue/post any content."""
    return await discovery_worker.run(source_id, preview=True)


def discover_sources() -> dict:
    """Compatibility for synchronous scripts; async callers use discovery_worker.run."""
    return asyncio.run(discovery_worker.run())


def _worker_main() -> None:
    if os.name == "posix":
        import resource
        # A parser cannot grow beyond a finite address space before its parent
        # deadline. These limits apply only to the disposable child.
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    request_bytes = sys.stdin.buffer.read(262145)
    if len(request_bytes) > 262144:
        raise ValueError("worker_request_limit_exceeded")
    request = json.loads(request_bytes)
    # Plugin/API debug output must never corrupt JSON or surface private strings.
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = fetch_source(request["source"], preview=request.get("preview", False))
    encoded = json.dumps(result, ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_WORKER_OUTPUT:
        result = {"items": [], "health": health(request["source"], "failed", error="worker_output_limit_exceeded")}
        encoded = json.dumps(result)
    sys.stdout.write(encoded)
    sys.stdout.flush()


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Internal provider worker only")
    _worker_main()
