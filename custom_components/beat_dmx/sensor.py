"""Current tempo and what it is following."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .entity import BeatEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    engine = entry.runtime_data
    add([BpmSensor(engine, "bpm", "Tempo"), TrackSensor(engine, "track", "Now following")])


class BpmSensor(BeatEntity, SensorEntity):
    _attr_icon = "mdi:metronome-tick"
    _attr_native_unit_of_measurement = "BPM"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float:
        return round(self.engine.bpm, 1)

    @property
    def extra_state_attributes(self) -> dict:
        return {"source": self.engine.source}


class TrackSensor(BeatEntity, SensorEntity):
    _attr_icon = "mdi:music-note"

    @property
    def native_value(self) -> str:
        now = self.engine.now
        if not now.title:
            return "Nothing playing"
        return f"{now.artist} - {now.title}" if now.artist else now.title

    @property
    def extra_state_attributes(self) -> dict:
        return {"player": self.engine.now.player or None}
