"""Intensity, a manual tempo override and the movement zone."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .entity import BeatEntity

NUMBERS = [
    # key, name, min, max, step, unit, icon, config
    ("intensity", "Intensity", 0, 100, 1, "%", "mdi:brightness-6", False),
    ("bpm_override", "Tempo override", 0, 220, 1, "BPM", "mdi:metronome", False),
    ("zone_width", "Zone width", 0, 170, 1, "°", "mdi:pan-horizontal", True),
    ("zone_low", "Lowest beam angle", 0, 90, 1, "°", "mdi:angle-acute", True),
    ("zone_high", "Highest beam angle", 0, 90, 1, "°", "mdi:angle-obtuse", True),
]


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    add([BeatNumber(entry.runtime_data, *n) for n in NUMBERS])


class BeatNumber(BeatEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(self, engine, key, name, low, high, step, unit, icon, config) -> None:
        super().__init__(engine, key, name)
        self._key = key
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = low, high, step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        if config:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> float:
        return getattr(self.engine, self._key)

    async def async_set_native_value(self, value: float) -> None:
        self.engine.set(self._key, value)
