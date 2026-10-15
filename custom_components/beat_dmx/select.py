"""Program, step speed, movement shape and how the heads move together."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeatConfigEntry
from .analysis import SECTIONS
from .const import (COLOUR_MIX, COLOUR_MODES, COLOUR_WHEEL, PEAK_ACTIONS, PROGRAMS, SECTION_ACTIONS, SPEEDS,
                    STROBE_PROGRAM_COLOUR)
from .entity import BeatEntity
from .motion import COORDINATION, MIX, SHAPES


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry, add: AddEntitiesCallback) -> None:
    engine = entry.runtime_data
    add([BeatSelect(engine, "program", "Program", PROGRAMS, "mdi:animation-play"),
         BeatSelect(engine, "speed", "Step speed", list(SPEEDS), "mdi:speedometer"),
         BeatSelect(engine, "shape", "Movement shape", SHAPES, "mdi:vector-curve"),
         BeatSelect(engine, "coordination", "Coordination", [*COORDINATION, MIX], "mdi:account-multiple"),
         BeatSelect(engine, "colour_mode", "Colours", [*COLOUR_MODES, COLOUR_MIX], "mdi:palette"),
         BeatSelect(engine, "strobe_colour", "Strobe colour", [STROBE_PROGRAM_COLOUR, *(n for n, _ in COLOUR_WHEEL)],
                    "mdi:flash-outline"),
         BeatSelect(engine, "peak_action", "Section: Peaks", PEAK_ACTIONS, "mdi:chart-bell-curve"),
         *(SectionSelect(engine, s) for s in SECTIONS)])


class BeatSelect(BeatEntity, SelectEntity):
    def __init__(self, engine, key: str, name: str, options: list[str], icon: str) -> None:
        super().__init__(engine, key, name)
        self._key = key
        self._attr_options = options
        self._attr_icon = icon

    @property
    def current_option(self) -> str:
        return getattr(self.engine, self._key)

    async def async_select_option(self, option: str) -> None:
        self.engine.set(self._key, option)


class SectionSelect(BeatEntity, SelectEntity):
    """What one section of a track does under Section show."""
    _attr_options = SECTION_ACTIONS
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, engine, section: str) -> None:
        super().__init__(engine, f"section_{section.lower().replace(' ', '_').replace('-', '_')}", f"Section: {section}")
        self._section = section

    @property
    def current_option(self) -> str:
        return self.engine.section_actions.get(self._section, "No change")

    async def async_select_option(self, option: str) -> None:
        self.engine.set("section_actions", {**self.engine.section_actions, self._section: option})
