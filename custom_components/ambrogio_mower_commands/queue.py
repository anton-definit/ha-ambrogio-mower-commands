"""Lightweight per-IMEI command queue (no implicit delays)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .api_client import AmbrogioClient, AmbroAuthError, AmbroClientError

_LOGGER = logging.getLogger(__name__)


@dataclass
class Command:
    op: str                           # "method.exec" | "sms.send" | "thing.find" | "thing.list" | "delay"
    imei: str
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""                   # human-friendly tag for logs
    result: Any = None
    future: asyncio.Future | None = None
    timeout: float | None = None      # optional per-command completion timeout


class CommandQueue:
    """
    Serial executor per IMEI with optional re-auth and (optional) retry backoff.
    Defaults are tuned for *no unintentional delays*:
      - rate_delay_sec = 0.0  (no spacing between commands)
      - retry_backoff_base = 0.0 (no sleep between retries)
    """

    def __init__(
        self,
        client: AmbrogioClient,
        *,
        rate_delay_sec: float = 0.0,            # no spacing by default
        max_retries: int = 2,
        retry_backoff_base: float = 0.0,        # no backoff sleep by default
        on_reauth: Optional[Callable[[], "asyncio.Future | bool"]] = None,
    ) -> None:
        self._client = client
        self._rate_delay = float(rate_delay_sec)
        self._max_retries = int(max_retries)
        self._retry_backoff_base = float(retry_backoff_base)
        self._on_reauth = on_reauth
        self._queues: Dict[str, asyncio.Queue[Command]] = {}
        self._workers: Dict[str, asyncio.Task] = {}
        self._stopped = asyncio.Event()

    def ensure_worker(self, imei: str) -> None:
        if imei in self._workers and not self._workers[imei].done():
            return
        q: asyncio.Queue[Command] = self._queues.setdefault(imei, asyncio.Queue())
        self._workers[imei] = asyncio.create_task(self._worker(imei, q), name=f"ambroq:{imei}")

    async def stop(self) -> None:
        """Stop all workers gracefully."""
        self._stopped.set()
        for imei, q in self._queues.items():
            # sentinel
            q.put_nowait(Command(op="__stop__", imei=imei))
        # Let workers drain
        tasks = list(self._workers.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def submit(self, cmd: Command, *, wait: bool = True, timeout: float | None = 30.0) -> Any:
        """Enqueue command; optionally wait for completion and return result."""
        self.ensure_worker(cmd.imei)
        if wait:
            loop = asyncio.get_running_loop()
            cmd.future = loop.create_future()
        await self._queues[cmd.imei].put(cmd)
        if not wait:
            return None
        # Prefer per-command timeout if provided
        eff_timeout = cmd.timeout if cmd.timeout is not None else timeout
        return await asyncio.wait_for(cmd.future, timeout=eff_timeout)

    async def _worker(self, imei: str, q: asyncio.Queue[Command]) -> None:
        try:
            while not self._stopped.is_set():
                cmd = await q.get()
                try:
                    if cmd.op == "__stop__":
                        if cmd.future and not cmd.future.done():
                            cmd.future.set_result(None)
                        return
                    res = await self._run(cmd)
                    cmd.result = res
                    if cmd.future and not cmd.future.done():
                        cmd.future.set_result(res)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.error("Command failed (%s %s): %s", cmd.op, cmd.label or imei, exc)
                    if cmd.future and not cmd.future.done():
                        cmd.future.set_exception(exc)
                finally:
                    q.task_done()
                    # Optional light pacing between calls (default 0.0 => no sleep)
                    if self._rate_delay > 0.0:
                        await asyncio.sleep(self._rate_delay)
        except asyncio.CancelledError:
            _LOGGER.debug("Worker cancelled for %s", imei)
            raise

    async def _run(self, cmd: Command) -> Any:
        # Explicit delay op for scripts that need it
        if cmd.op == "delay":
            await asyncio.sleep(float(cmd.params.get("seconds", 0)))
            return None

        attempt = 0
        while True:
            attempt += 1
            try:
                if cmd.op == "method.exec":
                    return await self._client.call("method.exec", cmd.params | {"imei": cmd.imei})
                if cmd.op == "sms.send":
                    return await self._client.call("sms.send", cmd.params | {"imei": cmd.imei})
                if cmd.op == "thing.find":
                    return await self._client.call("thing.find", {"imei": cmd.imei})
                if cmd.op == "thing.list":
                    return await self._client.call("thing.list", cmd.params)
                # Fallback: raw pass-through
                return await self._client.call(cmd.op, cmd.params)

            except AmbroAuthError:
                # Try to re-auth once per attempt; no extra delay unless the caller configured it
                if self._on_reauth:
                    try:
                        ok = await self._on_reauth()
                    except Exception:  # noqa: BLE001
                        ok = False
                    if ok and attempt <= self._max_retries + 1:
                        continue
                raise
            except AmbroClientError:
                # Retry up to max_retries with optional backoff (default 0.0 => no sleep)
                if attempt <= self._max_retries + 1:
                    if self._retry_backoff_base > 0.0:
                        await asyncio.sleep(self._retry_backoff_base * attempt)
                    continue
                raise
