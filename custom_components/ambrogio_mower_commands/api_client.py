"""Minimal async client for Ambrogio/ZCS commands (DeviceWise TR50)."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
import async_timeout

from .const import API_BASE_URI, API_ACK_TIMEOUT


# ----- Exceptions (simple + ours) --------------------------------------------

class AmbroClientError(Exception):
    """Base error for Ambrogio client."""


class AmbroAuthError(AmbroClientError):
    """Auth/session error."""


class AmbroTransportError(AmbroClientError):
    """Network/HTTP/timeout error."""


# ----- Client ----------------------------------------------------------------

class AmbrogioClient:
    """
    Super-thin TR50 client.
    - You call authenticate_app(...) once to get a session.
    - Use call(...) for generic TR50 commands.
    - Convenience methods cover the commands we’ll expose as services.
    """

    def __init__(self, session: aiohttp.ClientSession, endpoint: str | None = None) -> None:
        self._session = session
        self._endpoint = (endpoint or API_BASE_URI).rstrip("/")
        self._session_id: str | None = None
        self.ack_timeout = API_ACK_TIMEOUT

    # ---- Auth ----

    async def authenticate_app(self, app_id: str, app_token: str, thing_key: str) -> bool:
        """Authenticate app and store sessionId."""
        payload = {
            "auth": {
                "command": "api.authenticate",
                "params": {"appId": app_id, "appToken": app_token, "thingKey": thing_key},
            }
        }
        raw = await self._post(payload, expect_auth_envelope=True)
        # Session lives under auth.params.sessionId when successful
        try:
            self._session_id = raw["auth"]["params"]["sessionId"]
        except Exception as exc:  # noqa: BLE001
            raise AmbroAuthError("Missing sessionId in authentication response") from exc
        return True

    def _inject_session(self, data: dict[str, Any]) -> dict[str, Any]:
        if "auth" not in data:
            if not self._session_id:
                raise AmbroAuthError("No valid session. Call authenticate_app() first.")
            data["auth"] = {"sessionId": self._session_id}
        return data

    # ---- Core call ----

    async def call(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        as_raw: bool = False,
    ) -> dict[str, Any] | None:
        """
        Generic TR50 call.

        - When as_raw=False (default): return the common success payload (data.params) if present,
          otherwise the full response.
        - When as_raw=True: always return the full parsed response envelope.
        """
        payload: dict[str, Any] = {"data": {"command": command}}
        if params:
            payload["data"]["params"] = params
        raw = await self._post(payload)
        if as_raw:
            return raw
        return (raw.get("data") or {}).get("params") or raw

    async def _post(self, payload: dict[str, Any], *, expect_auth_envelope: bool = False) -> dict[str, Any]:
        """POST JSON with session injection and basic error handling."""
        if not expect_auth_envelope:
            payload = self._inject_session(payload)

        try:
            async with async_timeout.timeout(30):
                async with self._session.post(self._endpoint, json=payload) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise AmbroTransportError(f"HTTP {resp.status}: {text}")
                    try:
                        data = json.loads(text)
                    except Exception as exc:  # noqa: BLE001
                        raise AmbroTransportError("Invalid JSON from API") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AmbroTransportError("Network or timeout error") from exc

        # Basic success/error determination
        success = (
            data.get("success")
            or (data.get("data") or {}).get("success")
            or (data.get("auth") or {}).get("success")
        )
        if success:
            return data

        # If session invalid, surface as auth error (caller may choose to re-auth)
        errors = []
        if "errorMessages" in data:
            errors.extend(data["errorMessages"])
        if "data" in data and "errorMessages" in data["data"]:
            errors.extend(data["data"]["errorMessages"])
        msg = "; ".join(errors) if errors else "TR50 call failed"
        if "Authentication session is invalid" in msg:
            raise AmbroAuthError(msg)
        raise AmbroClientError(msg)

    # ---- Convenience commands we’ll expose as services ----

    async def method_exec(
        self,
        imei: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        as_raw: bool = False,
    ) -> dict[str, Any] | None:
        body: dict[str, Any] = {
            "method": method,
            "imei": imei,
            "ackTimeout": self.ack_timeout,
            "singleton": True,
        }
        if params:
            body["params"] = params
        return await self.call("method.exec", body, as_raw=as_raw)

    async def sms(self, imei: str, message: str, *, as_raw: bool = False) -> dict[str, Any] | None:
        return await self.call(
            "sms.send",
            {"coding": "SEVEN_BIT", "imei": imei, "message": message},
            as_raw=as_raw,
        )

    async def find_thing_by_imei(self, imei: str, *, as_raw: bool = False) -> dict[str, Any] | None:
        return await self.call("thing.find", {"imei": imei}, as_raw=as_raw)

    async def find_thing_by_key(self, key: str, *, as_raw: bool = False) -> dict[str, Any] | None:
        return await self.call("thing.find", {"key": key}, as_raw=as_raw)

    async def list_things(self, keys: list[str], *, as_raw: bool = False) -> dict[str, Any] | None:
        return await self.call(
            "thing.list",
            {
                "show": [
                    "id",
                    "key",
                    "name",
                    "connected",
                    "lastSeen",
                    "lastCommunication",
                    "loc",
                    "properties",
                    "alarms",
                    "attrs",
                    "createdOn",
                    "storage",
                    "varBillingPlanCode",
                ],
                "hideFields": True,
                "keys": keys,
            },
            as_raw=as_raw,
        )
