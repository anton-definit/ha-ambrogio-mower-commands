"""Sensors for Ambrogio Mower Commands."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    KEY_STATE,
    KEY_IMEI,
    SIGNAL_STATE_UPDATED,
    UNIQUE_SUFFIX_LOCATION,
    UNIQUE_SUFFIX_INFO,
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
        # No device registry grouping intentionally (services-only integration)
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
        # state is a readable "lat,lng" if available, else "unknown"
        self._attr_native_value = f"{lat},{lng}" if lat is not None and lng is not None else "unknown"
        # expose attrs for templating
        self._attr_extra_state_attributes = {
            "latitude": lat,
            "longitude": lng,
            "loc_updated": store.get("loc_updated"),
            "source": store.get("source"),
        }


class AmbrogioInfoSensor(_BaseAmbrogioSensor):
    _attr_name = "Ambrogio Mower Info"
    _attr_icon = "mdi:information-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._imei}_{UNIQUE_SUFFIX_INFO}"

    def _refresh_from_store(self) -> None:
        store = self.hass.data[DOMAIN][self._entry_id][KEY_STATE]
        connected = store.get("connected")
        # state: "connected" / "disconnected" / "unknown"
        if connected is True:
            state = "connected"
        elif connected is False:
            state = "disconnected"
        else:
            state = "unknown"
        self._attr_native_value = state
        self._attr_extra_state_attributes = {
            "latitude": store.get("latitude"),
            "longitude": store.get("longitude"),
            "loc_updated": store.get("loc_updated"),
            "info": store.get("info"),  # complete info blob (thing.find/list params)
            "source": store.get("source"),
        }
