"""Base entity for Beat DMX."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .engine import BeatEngine


class BeatEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: BeatEngine, key: str, name: str) -> None:
        self.engine = engine
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "beat_dmx")}, name="Beat DMX",
                                            manufacturer="Beat DMX", model="Music Assistant tempo to Art-Net")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.engine.add_listener(self.async_write_ha_state))
