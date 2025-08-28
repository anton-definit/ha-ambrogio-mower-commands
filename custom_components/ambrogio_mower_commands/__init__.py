"""Ambrogio Mower Commands integration (single mower)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import (
    DOMAIN,
    CONF_IMEI,
    CONF_CLIENT_KEY,
    CONF_CLIENT_NAME,
    API_BASE_URI,
    API_APP_TOKEN,
    KEY_CLIENT,
    KEY_IMEI,
    KEY_CLIENT_NAME,
    KEY_QUEUE,
    KEY_STATE,
)
from .api_client import AmbrogioClient, AmbroClientError, AmbroAuthError
from .services import async_register_services, async_unregister_services
from .queue import CommandQueue

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up from YAML (not used)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ambrogio Mower Commands from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    imei: str = entry.data[CONF_IMEI]
    client_key: str = entry.data[CONF_CLIENT_KEY]
    client_name: str = entry.data.get(CONF_CLIENT_NAME, "Home Assistant")

    session = aiohttp_client.async_get_clientsession(hass)
    client = AmbrogioClient(session=session, endpoint=API_BASE_URI)

    # Authenticate using our convention: app_id = client_key, thing_key = client_key
    try:
        await client.authenticate_app(
            app_id=client_key,
            app_token=API_APP_TOKEN,
            thing_key=client_key,
        )
    except AmbroAuthError as exc:
        _LOGGER.error("Ambrogio auth failed: %s", exc)
        return False
    except AmbroClientError as exc:
        _LOGGER.error("Ambrogio client error during setup: %s", exc)
        return False

    # Per-entry command queue with re-auth callback
    async def _reauth() -> bool:
        try:
            await client.authenticate_app(
                app_id=client_key,
                app_token=API_APP_TOKEN,
                thing_key=client_key,
            )
            _LOGGER.debug("Ambrogio session re-authenticated")
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Ambrogio re-authentication failed: %s", exc)
            return False

    queue = CommandQueue(client, on_reauth=_reauth)

    # Shared per-entry runtime data + state for sensors
    hass.data[DOMAIN][entry.entry_id] = {
        KEY_CLIENT: client,
        KEY_IMEI: imei,
        KEY_CLIENT_NAME: client_name,
        KEY_QUEUE: queue,
        KEY_STATE: {
            "latitude": None,
            "longitude": None,
            "connected": None,
            "loc_updated": None,
            "info": None,  # full info blob
            "source": None,  # "thing.find" | "thing.list"
        },
        "services_registered": False,  # marker used by services.py
    }

    # Register domain services (idempotent inside services.py)
    await async_register_services(hass)

    # Forward sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Ambrogio Mower Commands set up for IMEI %s (%s)", imei, client_name)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    data = domain_data.get(entry.entry_id)

    # Stop queue worker (if present)
    if data and (queue := data.get(KEY_QUEUE)) is not None:
        try:
            await queue.stop()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Queue stop encountered an error; continuing unload",
                exc_info=True,
            )

    # Unload platforms
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove entry data
    domain_data.pop(entry.entry_id, None)

    # If no entries remain, unregister domain-wide services and clear data
    if not domain_data:
        await async_unregister_services(hass)
        hass.data.pop(DOMAIN, None)

    _LOGGER.info("Ambrogio Mower Commands unloaded for entry %s", entry.entry_id)
    return ok
