"""Device registry helpers for Ambrogio Mower Commands."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_NAME


async def ensure_device(hass: HomeAssistant, entry: ConfigEntry, *, imei: str, client_name: str) -> None:
    """Create or update the mower device in the registry."""
    dev_reg = dr.async_get(hass)

    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, imei)},
        manufacturer="ZCS / Ambrogio",
        model="Ambrogio",
        name=entry.data.get(CONF_NAME, f"Ambrogio Mower {imei}"),
        suggested_area="Garden",
        via_device=None,
    )
