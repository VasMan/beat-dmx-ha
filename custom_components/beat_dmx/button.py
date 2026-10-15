"""Tap tempo: four or more taps set the tempo for the current track, every tap resets the beat."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .entity import BeatEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    add([TapButton(entry.runtime_data, "tap", "Tap tempo")])


class TapButton(BeatEntity, ButtonEntity):
    _attr_icon = "mdi:gesture-tap"

    async def async_press(self) -> None:
        self.engine.tap()
