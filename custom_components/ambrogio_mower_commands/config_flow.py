"""Config flow for Ambrogio Mower Commands (single mower)."""

from __future__ import annotations

import asyncio
import logging
import random
import string
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client

from .const import (
    DOMAIN,
    CONF_IMEI,
    CONF_CLIENT_KEY,
    CONF_CLIENT_NAME,
    API_APP_TOKEN,
    API_CLIENT_KEY_LENGTH,
)
from .api_client import AmbrogioClient, AmbroAuthError, AmbroClientError

_LOGGER = logging.getLogger(__name__)


def _valid_imei_format(imei: str) -> bool:
    return isinstance(imei, str) and len(imei) == 15 and imei.isdigit() and imei.startswith("35")


def _gen_client_key(length: int = API_CLIENT_KEY_LENGTH) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


class AmbrogioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ambrogio Mower Commands."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            imei = (user_input.get(CONF_IMEI) or "").strip()
            client_name = (user_input.get(CONF_CLIENT_NAME) or "Home Assistant").strip() or "Home Assistant"

            # Basic IMEI format guard
            if not _valid_imei_format(imei):
                errors[CONF_IMEI] = "invalid_imei"

            # Abort if entry for this IMEI already exists
            if not errors:
                await self.async_set_unique_id(imei)
                self._abort_if_unique_id_configured()

            if not errors:
                ok, data_or_err = await self._provision_client_and_validate(self.hass, imei, client_name)
                if ok:
                    client_key = data_or_err["client_key"]
                    # Create the entry
                    return self.async_create_entry(
                        title=f"Ambrogio {imei}",
                        data={
                            CONF_IMEI: imei,
                            CONF_CLIENT_KEY: client_key,
                            CONF_CLIENT_NAME: client_name,
                        },
                    )
                # Map specific failure reasons to form errors
                reason = data_or_err or "cannot_connect"
                if reason in ("auth_failed", "cannot_connect", "imei_not_found"):
                    errors["base"] = reason
                else:
                    errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_IMEI): str,
                vol.Optional(CONF_CLIENT_NAME, default="Home Assistant"): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # -----------------------
    # Provisioning helpers
    # -----------------------

    async def _provision_client_and_validate(
        self, hass: HomeAssistant, imei: str, client_name: str
    ) -> tuple[bool, dict[str, Any] | str]:
        """Generate client key, authenticate, publish client thing, validate IMEI, bind robot_client."""
        session = aiohttp_client.async_get_clientsession(hass)
        client = AmbrogioClient(session=session)

        # 1) Generate a working client key and authenticate (≤10 attempts)
        client_key: str | None = None
        for attempt in range(1, 11):
            candidate = _gen_client_key()
            try:
                await client.authenticate_app(app_id=candidate, app_token=API_APP_TOKEN, thing_key=candidate)
                client_key = candidate
                break
            except AmbroAuthError:
                _LOGGER.debug("Auth attempt %s failed with generated key; retrying", attempt)
                await asyncio.sleep(0.1 * attempt)
            except AmbroClientError as exc:
                _LOGGER.error("Auth transport error during setup: %s", exc)
                return False, "cannot_connect"

        if not client_key:
            _LOGGER.error("Failed to authenticate after multiple client key attempts")
            return False, "auth_failed"

        # 2) Publish/ensure client thing has a readable name (best-effort)
        try:
            # Try thing.find by key
            found = await client.call("thing.find", {"key": client_key})
            # Update or create
            if found:
                await client.call("thing.update", {"key": client_key, "name": client_name})
            else:
                await client.call("thing.create", {"defKey": "client", "key": client_key, "name": client_name})
        except Exception:
            _LOGGER.debug("Client thing publish skipped/failed (best-effort)", exc_info=True)

        # 3) Validate mower IMEI exists
        try:
            mower = await client.find_thing_by_imei(imei)
            if not mower:
                _LOGGER.error("IMEI not found via thing.find: %s", imei)
                return False, "imei_not_found"
        except AmbroClientError as exc:
            _LOGGER.error("IMEI validation error: %s", exc)
            return False, "cannot_connect"

        # 4) Best-effort: bind robot_clientX to our client_key if a slot is free or matches
        try:
            attrs = (mower or {}).get("attrs") or {}
            chosen_key: str | None = None
            for idx in range(1, 6):
                k = f"robot_client{idx}"
                if k not in attrs:
                    chosen_key = k
                    break
                if (attrs.get(k) or {}).get("value") == client_key:
                    chosen_key = k
                    break
            if chosen_key:
                await client.call("attribute.publish", {"thingKey": imei, "key": chosen_key, "value": client_key})
        except Exception:
            _LOGGER.debug("robot_client publish skipped/failed (best-effort)", exc_info=True)

        return True, {"client_key": client_key}
