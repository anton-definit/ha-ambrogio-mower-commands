"""Service registration for Ambrogio Mower Commands (single mower) with queued execution + optional responses."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
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
    # shared keys/signals
    KEY_CLIENT,
    KEY_IMEI,
    KEY_QUEUE,
    KEY_STATE,
    SIGNAL_STATE_UPDATED,
)
from .api_client import AmbrogioClient, AmbroClientError, AmbroAuthError
from .queue import Command  # queue command envelope

_LOGGER = logging.getLogger(__name__)

# Extra flags (available on ALL services)
ATTR_RETURN_RESPONSE = "return_response"  # default True
ATTR_LOG_RESPONSE = "log_response"        # default False

# ----------------
# Schemas (simple)
# ----------------
_COMMON_FLAGS = {
    vol.Optional(ATTR_RETURN_RESPONSE, default=True): vol.Boolean(),
    vol.Optional(ATTR_LOG_RESPONSE, default=False): vol.Boolean(),
}

SET_PROFILE_SCHEMA = vol.Schema({
    vol.Required(ATTR_PROFILE): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
    **_COMMON_FLAGS,
})
WORK_NOW_SCHEMA = vol.Schema({**_COMMON_FLAGS})
BORDER_CUT_SCHEMA = vol.Schema({**_COMMON_FLAGS})
CHARGE_NOW_SCHEMA = vol.Schema({**_COMMON_FLAGS})
CHARGE_UNTIL_SCHEMA = vol.Schema({
    vol.Required(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
    vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
    vol.Required(ATTR_WEEKDAY): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
    **_COMMON_FLAGS,
})
TRACE_POSITION_SCHEMA = vol.Schema({**_COMMON_FLAGS})
KEEP_OUT_SCHEMA = vol.Schema({
    vol.Required(ATTR_LOCATION): vol.Schema({
        vol.Required(ATTR_LATITUDE): float,
        vol.Required(ATTR_LONGITUDE): float,
        vol.Optional(ATTR_RADIUS): vol.Coerce(int),
    }),
    vol.Optional(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
    vol.Optional(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
    vol.Optional(ATTR_INDEX): vol.Coerce(int),
    **_COMMON_FLAGS,
})
WAKE_UP_SCHEMA = vol.Schema({**_COMMON_FLAGS})

# Diagnostic services: allow returning/logging the API response
THING_FIND_SCHEMA = vol.Schema({
    vol.Optional(ATTR_RETURN_RESPONSE, default=True): vol.Boolean(),
    vol.Optional(ATTR_LOG_RESPONSE, default=True): vol.Boolean(),
})
THING_LIST_SCHEMA = vol.Schema({
    vol.Optional(ATTR_RETURN_RESPONSE, default=True): vol.Boolean(),
    vol.Optional(ATTR_LOG_RESPONSE, default=True): vol.Boolean(),
})

# ------------------------
# Registration entrypoints
# ------------------------
_FLAG = "services_registered"


async def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services (idempotent)."""
    if hass.data.get(DOMAIN, {}).get(_FLAG):
        return

    async def _resolve_single() -> tuple[str, AmbrogioClient, str, Any, dict[str, Any]]:
        """Get (entry_id, client, imei, queue, state_store) from the single config entry."""
        domain_data = hass.data.get(DOMAIN, {})
        for entry_id, blob in domain_data.items():
            if entry_id == _FLAG:
                continue
            client: AmbrogioClient = blob[KEY_CLIENT]
            imei: str = blob[KEY_IMEI]
            queue = blob[KEY_QUEUE]
            state_store: dict[str, Any] = blob[KEY_STATE]
            return entry_id, client, imei, queue, state_store
        raise vol.Invalid("Ambrogio Mower Commands is not initialized")

    # ---- Helpers: flags + executor that always returns a dict ----
    def _flags(call: ServiceCall) -> tuple[bool, bool]:
        return bool(call.data.get(ATTR_RETURN_RESPONSE, True)), bool(call.data.get(ATTR_LOG_RESPONSE, False))

    async def _exec(awaitable, op_name: str, *, return_response: bool, log_response: bool) -> dict[str, Any]:
        """
        Execute queued API call, unified logging, and return a dict for HA.
        - If return_response is False, return {} (empty dict).
        - If True, return the API response dict or {"success": True} as a fallback.
        """
        try:
            resp = await awaitable
            if log_response and resp is not None:
                try:
                    _LOGGER.debug("%s response: %s", op_name, json.dumps(resp, ensure_ascii=False))
                except Exception:
                    _LOGGER.debug("%s response: %s", op_name, resp)
            _LOGGER.debug("Command %s executed successfully", op_name)
            if not return_response:
                return {}
            return resp if isinstance(resp, dict) else {"success": True}
        except AmbroAuthError as exc:
            _LOGGER.error("Auth error during %s: %s", op_name, exc)
            return {"success": False, "error": f"auth: {exc}"}
        except AmbroClientError as exc:
            _LOGGER.error("API error during %s: %s", op_name, exc)
            return {"success": False, "error": f"api: {exc}"}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during %s: %s", op_name, exc)
            return {"success": False, "error": f"unexpected: {exc}"}

    # ---- State helpers (apply thing.find / thing.list to sensors) ----
    def _update_location_from_find(entry_id: str, store: dict[str, Any], resp: dict[str, Any]) -> bool:
        params = (resp.get("data") or {}).get("params") or {}
        loc = params.get("loc") or {}
        corr = loc.get("corrId")
        loc_lat, loc_lng = loc.get("lat"), loc.get("lng")
        loc_ts = params.get("locUpdated") or loc.get("since")

        rs = (params.get("alarms") or {}).get("robot_state") or {}
        rs_lat, rs_lng = rs.get("lat"), rs.get("lng")
        rs_ts = rs.get("ts") or rs.get("since")

        # 1) Prefer immediate trace fix
        if corr == "trace" and loc_lat is not None and loc_lng is not None:
            lat, lng, when, pos_src = loc_lat, loc_lng, loc_ts, "params.loc(trace)"
        # 2) Otherwise prefer robot_state
        elif rs_lat is not None and rs_lng is not None:
            lat, lng, when, pos_src = rs_lat, rs_lng, rs_ts, "alarms.robot_state"
        # 3) Fallback to regular loc
        elif loc_lat is not None and loc_lng is not None:
            lat, lng, when, pos_src = loc_lat, loc_lng, loc_ts, "params.loc"
        else:
            lat = lng = when = pos_src = None

        connected = params.get("connected")
        return _apply_state(entry_id, store, lat, lng, connected, when, "thing.find", info=params, position_source=pos_src)

    def _update_location_from_list(entry_id: str, store: dict[str, Any], resp: dict[str, Any]) -> bool:
        params = (resp.get("data") or {}).get("params") or {}
        first = (params.get("result") or [{}])[0]

        loc = first.get("loc") or {}
        corr = loc.get("corrId")
        loc_lat, loc_lng = loc.get("lat"), loc.get("lng")
        loc_ts = first.get("locUpdated") or loc.get("since")

        rs = (first.get("alarms") or {}).get("robot_state") or {}
        rs_lat, rs_lng = rs.get("lat"), rs.get("lng")
        rs_ts = rs.get("ts") or rs.get("since")

        if corr == "trace" and loc_lat is not None and loc_lng is not None:
            lat, lng, when, pos_src = loc_lat, loc_lng, loc_ts, "result.loc(trace)"
        elif rs_lat is not None and rs_lng is not None:
            lat, lng, when, pos_src = rs_lat, rs_lng, rs_ts, "alarms.robot_state"
        elif loc_lat is not None and loc_lng is not None:
            lat, lng, when, pos_src = loc_lat, loc_lng, loc_ts, "result.loc"
        else:
            lat = lng = when = pos_src = None

        connected = first.get("connected")
        return _apply_state(entry_id, store, lat, lng, connected, when, "thing.list", info=first, position_source=pos_src)

    def _apply_state(
        entry_id: str,
        store: dict[str, Any],
        lat: Any,
        lng: Any,
        connected: Any,
        loc_updated: Any,
        source: str,
        info: dict[str, Any],
        position_source: str | None = None,
    ) -> bool:
        """Write to store only on change; fire dispatcher if changed."""
        changed = False

        def _norm(v):
            try:
                return round(float(v), 6)
            except Exception:
                return None

        nlat = _norm(lat)
        nlng = _norm(lng)

        if nlat is not None and nlng is not None:
            if store.get("latitude") != nlat or store.get("longitude") != nlng:
                store["latitude"] = nlat
                store["longitude"] = nlng
                changed = True

        if connected is not None and store.get("connected") != bool(connected):
            store["connected"] = bool(connected)
            changed = True

        if loc_updated is not None and store.get("loc_updated") != loc_updated:
            store["loc_updated"] = loc_updated
            changed = True

        if position_source is not None and store.get("position_source") != position_source:
            store["position_source"] = position_source
            changed = True

        prev_info = store.get("info")
        if info and (prev_info != info):
            store["info"] = info
            changed = True

        if changed:
            store["source"] = source  # which service updated us
            async_dispatcher_send(hass, SIGNAL_STATE_UPDATED, entry_id)

        return changed

    # ---- Handlers (queued) ----
    async def _srv_set_profile(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        params = {
            "method": "set_profile",
            "params": {"profile": int(call.data[ATTR_PROFILE]) - 1},
            "ackTimeout": client.ack_timeout,
            "singleton": True,
        }
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="set_profile")),
            "set_profile", return_response=return_response, log_response=log_response
        )

    async def _srv_work_now(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        params = {"method": "work_now", "ackTimeout": client.ack_timeout, "singleton": True}
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="work_now")),
            "work_now", return_response=return_response, log_response=log_response
        )

    async def _srv_border_cut(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        params = {"method": "border_cut", "ackTimeout": client.ack_timeout, "singleton": True}
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="border_cut")),
            "border_cut", return_response=return_response, log_response=log_response
        )

    async def _srv_charge_now(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        params = {"method": "charge_now", "ackTimeout": client.ack_timeout, "singleton": True}
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="charge_now")),
            "charge_now", return_response=return_response, log_response=log_response
        )

    async def _srv_charge_until(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        hours = int(call.data[ATTR_HOURS])
        minutes = int(call.data[ATTR_MINUTES])
        weekday = int(call.data[ATTR_WEEKDAY])  # 1..7 -> API 0..6
        params = {
            "method": "charge_until",
            "params": {"hh": hours, "mm": minutes, "weekday": weekday - 1},
            "ackTimeout": client.ack_timeout,
            "singleton": True,
        }
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="charge_until")),
            "charge_until", return_response=return_response, log_response=log_response
        )

    async def _srv_trace_position(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        params = {"method": "trace_position", "ackTimeout": client.ack_timeout, "singleton": True}
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="trace_position")),
            "trace_position", return_response=return_response, log_response=log_response
        )

    async def _srv_keep_out(call: ServiceCall) -> dict[str, Any]:
        _entry_id, client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
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
        return await _exec(
            queue.submit(Command(op="method.exec", imei=imei, params=params, label="keep_out")),
            "keep_out", return_response=return_response, log_response=log_response
        )

    async def _srv_wake_up(call: ServiceCall) -> dict[str, Any]:
        _entry_id, _client, imei, queue, _state = await _resolve_single()
        return_response, log_response = _flags(call)
        params = {"coding": "SEVEN_BIT", "message": "UP"}
        return await _exec(
            queue.submit(Command(op="sms.send", imei=imei, params=params, label="wake_up")),
            "wake_up", return_response=return_response, log_response=log_response
        )

    async def _srv_thing_find(call: ServiceCall) -> dict[str, Any]:
        entry_id, client, imei, _queue, state_store = await _resolve_single()
        try:
            # Prefer key already known from last info, otherwise our IMEI
            imei_to_query = (state_store.get("info") or {}).get("key") or imei
            resp = await client.find_thing_by_imei(imei_to_query, as_raw=True) or {}

            changed = _update_location_from_find(entry_id, state_store, resp)

            if call.data.get(ATTR_LOG_RESPONSE, True):
                _LOGGER.debug("thing.find response: %s", json.dumps(resp, ensure_ascii=False))
            if changed:
                _LOGGER.debug("thing.find applied new location/info to sensors")

            return resp if call.data.get(ATTR_RETURN_RESPONSE, True) else {}
        except AmbroAuthError as exc:
            _LOGGER.error("Auth error during thing_find: %s", exc)
            return {"success": False, "error": "auth_error", "message": str(exc)}
        except AmbroClientError as exc:
            _LOGGER.error("API error during thing_find: %s", exc)
            return {"success": False, "error": "api_error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during thing_find: %s", exc)
            return {"success": False, "error": "unexpected_error", "message": str(exc)}

    async def _srv_thing_list(call: ServiceCall) -> dict[str, Any]:
        entry_id, client, imei, _queue, state_store = await _resolve_single()
        try:
            resp = await client.list_things([imei], as_raw=True) or {}

            changed = _update_location_from_list(entry_id, state_store, resp)

            if call.data.get(ATTR_LOG_RESPONSE, True):
                _LOGGER.debug("thing.list response: %s", json.dumps(resp, ensure_ascii=False))
            if changed:
                _LOGGER.debug("thing.list applied new location/info to sensors")

            return resp if call.data.get(ATTR_RETURN_RESPONSE, True) else {}
        except AmbroAuthError as exc:
            _LOGGER.error("Auth error during thing_list: %s", exc)
            return {"success": False, "error": "auth_error", "message": str(exc)}
        except AmbroClientError as exc:
            _LOGGER.error("API error during thing_list: %s", exc)
            return {"success": False, "error": "api_error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during thing_list: %s", exc)
            return {"success": False, "error": "unexpected_error", "message": str(exc)}

    # ---- Register ----
    hass.services.async_register(DOMAIN, SERVICE_SET_PROFILE, _srv_set_profile, schema=SET_PROFILE_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_WORK_NOW, _srv_work_now, schema=WORK_NOW_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_BORDER_CUT, _srv_border_cut, schema=BORDER_CUT_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_NOW, _srv_charge_now, schema=CHARGE_NOW_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_UNTIL, _srv_charge_until, schema=CHARGE_UNTIL_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_TRACE_POSITION, _srv_trace_position, schema=TRACE_POSITION_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_KEEP_OUT, _srv_keep_out, schema=KEEP_OUT_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_WAKE_UP, _srv_wake_up, schema=WAKE_UP_SCHEMA, supports_response=True)

    # Diagnostic services return payloads and update sensors
    hass.services.async_register(DOMAIN, SERVICE_THING_FIND, _srv_thing_find, schema=THING_FIND_SCHEMA, supports_response=True)
    hass.services.async_register(DOMAIN, SERVICE_THING_LIST, _srv_thing_list, schema=THING_LIST_SCHEMA, supports_response=True)

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
