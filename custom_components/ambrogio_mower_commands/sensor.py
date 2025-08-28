"""Sensors for Ambrogio Mower Commands."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Tuple

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    KEY_STATE,
    KEY_IMEI,
    SIGNAL_STATE_UPDATED,
    UNIQUE_SUFFIX_LOCATION,
    UNIQUE_SUFFIX_INFO,
)
from .mappings import (
    ROBOT_MODELS,
    ROBOT_STATES,
    ROBOT_STATES_WORKING,
    ROBOT_ERRORS,
    DATA_THRESHOLD_STATES,
    INFINITY_PLAN_STATES,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    imei: str = data[KEY_IMEI]

    entities: list[SensorEntity] = [
        AmbrogioLocationSensor(hass, entry.entry_id, imei),
        AmbrogioInfoSensor(hass, entry.entry_id, imei),
    ]
    async_add_entities(entities, update_before_add=True)


class _BaseAmbrogioSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry_id: str, imei: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._imei = imei
        self._unsub: Any = None

    @property
    def device_info(self) -> dict[str, Any] | None:
        # Intentionally no device object (services-only integration)
        return None

    async def async_added_to_hass(self) -> None:
        @callback
        def _state_updated(changed_entry_id: str) -> None:
            if changed_entry_id == self._entry_id:
                self._refresh_from_store()
                self.async_write_ha_state()

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_STATE_UPDATED, _state_updated)
        self._refresh_from_store()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _refresh_from_store(self) -> None:
        raise NotImplementedError


class AmbrogioLocationSensor(_BaseAmbrogioSensor):
    _attr_name = "Ambrogio Mower Location"
    _attr_icon = "mdi:map-marker"

    @property
    def unique_id(self) -> str:
        return f"{self._imei}_{UNIQUE_SUFFIX_LOCATION}"

    def _refresh_from_store(self) -> None:
        store = self.hass.data[DOMAIN][self._entry_id][KEY_STATE]
        lat = store.get("latitude")
        lng = store.get("longitude")

        # Pretty state
        if lat is not None and lng is not None:
            try:
                slat = f"{float(lat):.6f}"
                slng = f"{float(lng):.6f}"
            except Exception:
                slat, slng = str(lat), str(lng)
            self._attr_native_value = f"{slat},{slng}"
        else:
            self._attr_native_value = "unknown"

        # Optional richer info from last response
        info = store.get("info") or {}
        loc = info.get("loc") or {}
        addr = loc.get("addr") or {}
        fix_type = loc.get("fixType")

        # Base icon from fix type
        if fix_type == "gps":
            self._attr_icon = "mdi:crosshairs-gps"
        elif fix_type == "network":
            self._attr_icon = "mdi:signal"
        else:
            self._attr_icon = "mdi:map-marker"

        # >>> Place your override right here <<<
        pos_src = store.get("position_source")
        if pos_src == "alarms.robot_state":
            self._attr_icon = "mdi:robot-mower-outline"

        def _fmt_addr() -> str | None:
            parts = [addr.get("street"), addr.get("city"), addr.get("state"), addr.get("country")]
            parts = [p for p in parts if p]
            return ", ".join(parts) if parts else None

        self._attr_extra_state_attributes = {
            "latitude": lat,
            "longitude": lng,
            "loc_updated": store.get("loc_updated"),
            "source": store.get("source"),
            "position_source": pos_src,  # show where coords came from
            "address": _fmt_addr(),
            "maps_url": (f"https://maps.google.com/?q={lat},{lng}"
                        if lat is not None and lng is not None else None),
        }

class AmbrogioInfoSensor(_BaseAmbrogioSensor):
    _attr_name = "Ambrogio Mower Info"
    _attr_icon = "mdi:information-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._imei}_{UNIQUE_SUFFIX_INFO}"

    def _refresh_from_store(self) -> None:
        store = self.hass.data[DOMAIN][self._entry_id][KEY_STATE]
        info = store.get("info") or {}

        # State: connected / disconnected / unknown
        connected = store.get("connected")
        if connected is True:
            state = "connected"
        elif connected is False:
            state = "disconnected"
        else:
            state = "unknown"
        self._attr_native_value = state

        # ---- Derivations / translations (inspired by your sample) ----

        # Model from serial prefix (e.g. "AM040L")
        model_code, model_name = _extract_model(info)
        serial_number = ((info.get("attrs") or {}).get("robot_serial") or {}).get("value")

        # Program version → rNNN
        program_version = ((info.get("attrs") or {}).get("program_version") or {}).get("value")
        sw_version = f"r{program_version}" if program_version else None

        # Robot state (index into ROBOT_STATES)
        robot_state_code, robot_state_name, robot_state_icon, robot_state_color = _map_robot_state(info)
        self._attr_icon = robot_state_icon or "mdi:information-outline"
        working = bool(robot_state_code in ROBOT_STATES_WORKING) if robot_state_code is not None else None
        available = bool((robot_state_code or 0) > 0) if robot_state_code is not None else None

        # Error code: prefer properties.robot_error.value, fall back to alarms.robot_state.msg (if numeric)
        robot_error_code, robot_error_name = _map_robot_error(info)

        # Data threshold (alarms.data_th.state)
        data_threshold_name, data_threshold_color = _map_data_threshold(info)

        # Infinity plan status + expiration
        ips_code, ips_name, ips_color = _map_infinity_status(info)
        infinity_expiration_raw = ((info.get("attrs") or {}).get("infinity_expiration_date") or {}).get("value")
        infinity_expiration = _as_local_iso(infinity_expiration_raw)

        # Connection expiration: prefer attrs.expiration_date; else derive from created_on + 730 days
        connect_expiration_raw = ((info.get("attrs") or {}).get("expiration_date") or {}).get("value")
        created_on_raw = ((info.get("attrs") or {}).get("created_on") or {}).get("value")
        if connect_expiration_raw:
            connect_expiration = _as_local_iso(connect_expiration_raw)
        elif created_on_raw:
            created = dt_util.parse_datetime(created_on_raw)
            connect_expiration = dt_util.as_local(created + timedelta(days=730)).isoformat(timespec="seconds") if created else None
        else:
            connect_expiration = None

        # Mirror of location & metadata
        lat = store.get("latitude")
        lng = store.get("longitude")
        loc_updated = store.get("loc_updated")

        # Helpful raw fields
        last_seen = info.get("lastSeen")
        last_communication = info.get("lastCommunication")
        firmware_current = (info.get("firmware") or {}).get("currentVersion")

        # Build attributes
        self._attr_extra_state_attributes = {
            # mirrored basics
            "latitude": lat,
            "longitude": lng,
            "loc_updated": loc_updated,
            "source": store.get("source"),
            "maps_url": (
                f"https://maps.google.com/?q={lat},{lng}"
                if lat is not None and lng is not None else None
            ),
            # raw info blob
            "info": info,
            # model/identification
            "serial_number": serial_number,
            "model_code": model_code,
            "model_name": model_name,
            "sw_version": sw_version,
            # translated state
            "robot_state_code": robot_state_code,
            "robot_state_name": robot_state_name,
            "robot_state_icon": robot_state_icon,
            "robot_state_color": robot_state_color,
            "working": working,
            "available": available,
            # error mapping
            "robot_error_code": robot_error_code,
            "robot_error_name": robot_error_name,
            # data threshold
            "data_threshold_name": data_threshold_name,
            "data_threshold_color": data_threshold_color,
            # infinity plan
            "infinity_plan_status_code": ips_code,
            "infinity_plan_status_name": ips_name,
            "infinity_plan_status_color": ips_color,
            "infinity_expiration_date": infinity_expiration,
            "connect_expiration_date": connect_expiration,
            # raw handy fields
            "last_seen": last_seen,
            "last_communication": last_communication,
            "firmware_current": firmware_current,
        }


# -----------------
# Helper functions
# -----------------

_MODEL_PREFIX = re.compile(r"^[A-Z]{2}\d{3}[A-Z]")

def _extract_model(info: dict[str, Any]) -> Tuple[str | None, str | None]:
    """Derive model code from attrs.robot_serial.value, map to human-readable name."""
    serial = ((info.get("attrs") or {}).get("robot_serial") or {}).get("value") or ""
    m = _MODEL_PREFIX.match(serial)
    code = m.group(0) if m else None
    name = ROBOT_MODELS.get(code) if code else None
    return code, name

def _map_robot_state(info: dict[str, Any]) -> Tuple[int | None, str | None, str | None, str | None]:
    alarms = info.get("alarms") or {}
    state = (alarms.get("robot_state") or {}).get("state")
    if isinstance(state, int) and 0 <= state < len(ROBOT_STATES):
        st = ROBOT_STATES[state]
        return state, st.get("name"), st.get("icon"), st.get("color")
    # out-of-range or missing -> unknown
    return state if isinstance(state, int) else None, None, None, None

def _map_robot_error(info: dict[str, Any]) -> Tuple[int | None, str | None]:
    """Prefer properties.robot_error.value; fallback to alarms.robot_state.msg if numeric."""
    props = info.get("properties") or {}
    code = (props.get("robot_error") or {}).get("value")
    if isinstance(code, int):
        return code, ROBOT_ERRORS.get(code)

    msg = ((info.get("alarms") or {}).get("robot_state") or {}).get("msg")
    try:
        code2 = int(msg) if msg is not None else None
        if code2 is not None:
            return code2, ROBOT_ERRORS.get(code2)
    except Exception:
        pass
    return None, None

def _map_data_threshold(info: dict[str, Any]) -> Tuple[str | None, str | None]:
    alarms = info.get("alarms") or {}
    dt_state = (alarms.get("data_th") or {}).get("state")
    if isinstance(dt_state, int) and 0 <= dt_state < len(DATA_THRESHOLD_STATES):
        st = DATA_THRESHOLD_STATES[dt_state]
        return st.get("name"), st.get("color")
    return None, None

def _map_infinity_status(info: dict[str, Any]) -> Tuple[int | None, str | None, str | None]:
    alarms = info.get("alarms") or {}
    code = (alarms.get("infinity_plan_status") or {}).get("state")
    if isinstance(code, int) and 0 <= code < len(INFINITY_PLAN_STATES):
        st = INFINITY_PLAN_STATES[code]
        return code, st.get("name"), st.get("color")
    return code if isinstance(code, int) else None, None, None

def _as_local_iso(iso_str: str | None) -> str | None:
    """Parse an ISO string and return local ISO (seconds precision)."""
    if not iso_str:
        return None
    dt = dt_util.parse_datetime(iso_str)
    return dt_util.as_local(dt).isoformat(timespec="seconds") if dt else None
