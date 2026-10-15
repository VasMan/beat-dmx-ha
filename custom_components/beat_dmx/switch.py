"""Beat sync on/off."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .entity import BeatEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    add([BeatSyncSwitch(entry.runtime_data, "enabled", "Beat sync")])


class BeatSyncSwitch(BeatEntity, SwitchEntity):
    _attr_icon = "mdi:metronome"

    @property
    def is_on(self) -> bool:
        return self.engine.enabled

    @property
    def extra_state_attributes(self) -> dict:
        return self.engine.status

    async def async_turn_on(self, **kwargs) -> None:
        self.engine.set("enabled", True)

    async def async_turn_off(self, **kwargs) -> None:
        self.engine.set("enabled", False)
