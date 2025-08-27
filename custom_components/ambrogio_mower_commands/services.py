"""Service registration for Ambrogio Mower Commands (single mower) with queued execution."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    # config keys
    CONF_IMEI,
    CONF_CLIENT_NAME,
    # service names
    SERVICE_SET_PROFILE,
    SERVICE_WORK_NOW,
    SERVICE_BORDER_CUT,
    SERVICE_CHARGE_NOW,
    SERVICE_CHARGE_UNTIL,
    SERVICE_TRACE_POSITION,
    SERVICE_KEEP_OUT,
    SERVICE_WAKE_UP,
    SERVICE_THING_FIND,
    SERVICE_THING_LIST,
    # attrs
    ATTR_PROFILE,
    ATTR_HOURS,
    ATTR_MINUTES,
    ATTR_WEEKDAY,
    ATTR_LOCATION,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_RADIUS,
    ATTR_INDEX,
)
from .api_client import AmbrogioClient, AmbroClientError, AmbroAuthError
from .queue import Command  # <-- queue command envelope

_LOGGER = logging.getLogger(__name__)

# ----------------
# Schemas (simple)
# ----------------
SET_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.entity_ids_or_uuids,
        vol.Required(ATTR_PROFILE): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
    }
)
WORK_NOW_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})
BORDER_CUT_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})
CHARGE_NOW_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})
CHARGE_UNTIL_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.entity_ids_or_uuids,
        vol.Required(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Required(ATTR_WEEKDAY): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
    }
)
TRACE_POSITION_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})
KEEP_OUT_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.entity_ids_or_uuids,
        vol.Required(ATTR_LOCATION): vol.Schema(
            {
                vol.Required(ATTR_LATITUDE): float,
                vol.Required(ATTR_LONGITUDE): float,
                vol.Optional(ATTR_RADIUS): vol.Coerce(int),
            }
        ),
        vol.Optional(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Optional(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Optional(ATTR_INDEX): vol.Coerce(int),
    }
)
WAKE_UP_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})
THING_FIND_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})
THING_LIST_SCHEMA = vol.Schema({vol.Required("device_id"): cv.entity_ids_or_uuids})

# ------------------------
# Registration entrypoints
# ------------------------
_FLAG = "services_registered"


async def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services (idempotent)."""
    if hass.data.get(DOMAIN, {}).get(_FLAG):
        return

    async def _resolve_single() -> tuple[AmbrogioClient, str, Any]:
        """Get (client, imei, queue) from the single config entry."""
        domain_data = hass.data.get(DOMAIN, {})
        for entry_id, blob in domain_data.items():
            if entry_id == _FLAG:
                continue
            client: AmbrogioClient = blob["client"]
            imei: str = blob["imei"]
            queue = blob["queue"]
            return client, imei, queue
        raise vol.Invalid("Ambrogio Mower Commands is not initialized")

    # ---- Handlers (queued) ----
    async def _srv_set_profile(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        profile = int(call.data[ATTR_PROFILE])
        params = {
            "method": "set_profile",
            "params": {"profile": profile - 1},
            "ackTimeout": client.ack_timeout,
            "singleton": True,
        }
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="set_profile")), "set_profile")

    async def _srv_work_now(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        params = {"method": "work_now", "ackTimeout": client.ack_timeout, "singleton": True}
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="work_now")), "work_now")

    async def _srv_border_cut(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        params = {"method": "border_cut", "ackTimeout": client.ack_timeout, "singleton": True}
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="border_cut")), "border_cut")

    async def _srv_charge_now(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        params = {"method": "charge_now", "ackTimeout": client.ack_timeout, "singleton": True}
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="charge_now")), "charge_now")

    async def _srv_charge_until(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        hours = int(call.data[ATTR_HOURS])
        minutes = int(call.data[ATTR_MINUTES])
        weekday = int(call.data[ATTR_WEEKDAY])  # 1..7 -> API 0..6
        params = {
            "method": "charge_until",
            "params": {"hh": hours, "mm": minutes, "weekday": weekday - 1},
            "ackTimeout": client.ack_timeout,
            "singleton": True,
        }
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="charge_until")), "charge_until")

    async def _srv_trace_position(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        params = {"method": "trace_position", "ackTimeout": client.ack_timeout, "singleton": True}
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="trace_position")), "trace_position")

    async def _srv_keep_out(call: ServiceCall) -> None:
        client, imei, queue = await _resolve_single()
        loc = call.data[ATTR_LOCATION]
        keep_params: dict[str, Any] = {
            "latitude": float(loc[ATTR_LATITUDE]),
            "longitude": float(loc[ATTR_LONGITUDE]),
        }
        if ATTR_RADIUS in loc:
            keep_params["radius"] = int(loc[ATTR_RADIUS])
        if ATTR_HOURS in call.data:
            keep_params["hh"] = int(call.data[ATTR_HOURS])
        if ATTR_MINUTES in call.data:
            keep_params["mm"] = int(call.data[ATTR_MINUTES])
        if ATTR_INDEX in call.data:
            keep_params["index"] = int(call.data[ATTR_INDEX])

        params = {
            "method": "keep_out",
            "params": keep_params,
            "ackTimeout": client.ack_timeout,
            "singleton": True,
        }
        await _safe(queue.submit(Command(op="method.exec", imei=imei, params=params, label="keep_out")), "keep_out")

    async def _srv_wake_up(call: ServiceCall) -> None:
        _client, imei, queue = await _resolve_single()
        params = {"coding": "SEVEN_BIT", "message": "UP"}
        await _safe(queue.submit(Command(op="sms.send", imei=imei, params=params, label="wake_up")), "wake_up")

    async def _srv_thing_find(call: ServiceCall) -> None:
        _client, imei, queue = await _resolve_single()
        await _safe(queue.submit(Command(op="thing.find", imei=imei, params={}, label="thing_find")), "thing_find")

    async def _srv_thing_list(call: ServiceCall) -> None:
        _client, imei, queue = await _resolve_single()
        list_params = {
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
            "keys": [imei],
        }
        await _safe(queue.submit(Command(op="thing.list", imei=imei, params=list_params, label="thing_list")), "thing_list")

    # ---- Register ----
    hass.services.async_register(DOMAIN, SERVICE_SET_PROFILE, _srv_set_profile, schema=SET_PROFILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_WORK_NOW, _srv_work_now, schema=WORK_NOW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_BORDER_CUT, _srv_border_cut, schema=BORDER_CUT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_NOW, _srv_charge_now, schema=CHARGE_NOW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_UNTIL, _srv_charge_until, schema=CHARGE_UNTIL_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_TRACE_POSITION, _srv_trace_position, schema=TRACE_POSITION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_KEEP_OUT, _srv_keep_out, schema=KEEP_OUT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_WAKE_UP, _srv_wake_up, schema=WAKE_UP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_THING_FIND, _srv_thing_find, schema=THING_FIND_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_THING_LIST, _srv_thing_list, schema=THING_LIST_SCHEMA)

    hass.data[DOMAIN][_FLAG] = True
    _LOGGER.debug("Ambrogio Mower Commands: services registered.")


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister all services for the domain."""
    for name in (
        SERVICE_SET_PROFILE,
        SERVICE_WORK_NOW,
        SERVICE_BORDER_CUT,
        SERVICE_CHARGE_NOW,
        SERVICE_CHARGE_UNTIL,
        SERVICE_TRACE_POSITION,
        SERVICE_KEEP_OUT,
        SERVICE_WAKE_UP,
        SERVICE_THING_FIND,
        SERVICE_THING_LIST,
    ):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
    if DOMAIN in hass.data and _FLAG in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(_FLAG, None)
    _LOGGER.debug("Ambrogio Mower Commands: services unregistered.")


# -------------
# Small helper
# -------------
async def _safe(awaitable, op_name: str) -> None:
    """Execute queued API call and log errors without raising to HA."""
    try:
        await awaitable
        _LOGGER.debug("Command %s executed successfully", op_name)
    except AmbroAuthError as exc:
        _LOGGER.error("Auth error during %s: %s", op_name, exc)
    except AmbroClientError as exc:
        _LOGGER.error("API error during %s: %s", op_name, exc)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Unexpected error during %s: %s", op_name, exc)
