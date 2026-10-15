"""Current tempo and what it is following."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .entity import BeatEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    engine = entry.runtime_data
    add([BpmSensor(engine, "bpm", "Tempo"), TrackSensor(engine, "track", "Now following"),
         NowSensor(engine, "coord_now", "Coordination now", "mdi:account-multiple"),
         NowSensor(engine, "colour_now", "Colours now", "mdi:palette"),
         SectionSensor(engine, "section", "Section"), AnalysedSensor(engine, "analysed", "Analysed tracks")])


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


class NowSensor(BeatEntity, SensorEntity):
    """What a Random mix setting is playing right now (or the fixed setting)."""

    def __init__(self, engine, key: str, name: str, icon: str) -> None:
        super().__init__(engine, key, name)
        self._key = key
        self._attr_icon = icon

    @property
    def native_value(self) -> str:
        return getattr(self.engine, self._key)


class SectionSensor(BeatEntity, SensorEntity):
    """The section of the playing track (from its analysis); attributes say what comes next."""
    _attr_icon = "mdi:chart-timeline-variant"

    @property
    def native_value(self) -> str | None:
        live = self.engine._section_live()
        if not live:
            return None
        if not live["source"]:
            return "Not analysed"
        return live["type"] or "–"

    @property
    def extra_state_attributes(self) -> dict:
        live = self.engine._section_live() or {}
        a = self.engine.analysis or {}
        return {"source": live.get("source"), "next": live.get("next"),
                "sections": [f"{int(s['start'])//60}:{int(s['start'])%60:02d} {s['type']}" for s in a.get("sections") or []]}


class AnalysedSensor(BeatEntity, SensorEntity):
    """How many tracks have a local analysis, and how the folder scan is getting on."""
    _attr_icon = "mdi:folder-music"
    _attr_native_unit_of_measurement = "tracks"

    @property
    def native_value(self) -> int:
        return len(self.engine.library.by_key) if self.engine.library else 0

    @property
    def extra_state_attributes(self) -> dict:
        scan = self.engine.scan
        total, done = scan.get("total") or 0, scan.get("done") or 0
        if scan.get("running"):
            progress = f"{done} / {total} ({round(100 * done / total) if total else 0}%)"
        else:
            progress = f"finished: {done} / {total}" + (f", {scan['errors']} failed" if scan.get("errors") else "") if total else "idle"
        return {"progress": progress, "analysing_now": scan.get("current") or "–", **scan,
                "music_folder": self.engine.music_folder or None}
