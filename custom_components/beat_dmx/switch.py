"""Beat sync, manual DMX, blackout and blackout on silence."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .entity import BeatEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    engine = entry.runtime_data
    add([BeatSyncSwitch(engine, "enabled", "Beat sync"),
         ManualSwitch(engine, "manual", "Manual DMX"),
         SettingSwitch(engine, "blackout", "Blackout", "mdi:lightbulb-off"),
         SettingSwitch(engine, "blackout_on_silence", "Blackout on silence", "mdi:volume-off"),
         SettingSwitch(engine, "strobe", "Strobe", "mdi:flash"),
         SettingSwitch(engine, "section_show", "Section show", "mdi:chart-timeline-variant")])


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


class ManualSwitch(BeatEntity, SwitchEntity):
    """Holds the values set with beat_dmx.set_channel (or the live card's sliders), music or not."""
    _attr_icon = "mdi:tune-vertical"

    @property
    def is_on(self) -> bool:
        return self.engine.manual

    async def async_turn_on(self, **kwargs) -> None:
        self.engine.set_manual(True)

    async def async_turn_off(self, **kwargs) -> None:
        self.engine.set_manual(False)


class SettingSwitch(BeatEntity, SwitchEntity):
    """An on/off setting of the engine (Blackout: every channel 0; Blackout on silence: dark while nothing plays)."""

    def __init__(self, engine, key: str, name: str, icon: str) -> None:
        super().__init__(engine, key, name)
        self._key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.engine, self._key))

    async def async_turn_on(self, **kwargs) -> None:
        self.engine.set(self._key, True)

    async def async_turn_off(self, **kwargs) -> None:
        self.engine.set(self._key, False)
