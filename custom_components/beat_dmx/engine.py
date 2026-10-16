"""Follows Music Assistant, looks up the tempo and drives the fixtures' DMX channels on the beat."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .analysis import SECTIONS, Library, find_sections, ma_profile, section_at, timeline
from .bpm import BpmLookup, track_key
from .const import (CH_COLOUR, CH_DIM_MODE, CH_DIMMER, CH_FUNCTION, CH_GOBO, CH_PAN, CH_PAN_FINE,
                    CH_PT_SPEED, CH_SHUTTER, CH_TILT, CH_TILT_FINE, CHANNEL_COUNT, COLOUR_MIX, COLOUR_MODES,
                    COLOUR_OFFSET, COLOUR_WHEEL, COLOURS, DEFAULT_SECTION_ACTIONS, SILENCE_SECONDS,
                    STROBE_OVER, STROBE_PROGRAM_COLOUR,
                    DIM_MODE_STANDARD, DOMAIN, FUNCTION_NONE, GOBOS, PROGRAMS, PT_SPEED_FAST, SHUTTER_OPEN,
                    SPEEDS, STROBE_FAST, STROBE_SLOW)
from .motion import COORDINATION, DEFAULT_CAL, MIX, MIX_SECONDS, SHAPES, aim_to_dmx, zone_point, zone_to_angles

_LOGGER = logging.getLogger(__name__)

POLL_SECONDS = 2
FPS = 25
TAP_RESET_SECONDS = 2.0
SETTINGS = ("enabled", "program", "speed", "intensity", "bpm_override", "shape", "coordination", "colour_mode",
            "blackout", "blackout_on_silence", "strobe", "strobe_colour",
            "section_show", "section_actions", "peak_action", "quiet_action", "drop_burst_beats", "sync_offset",
            "zone_width", "zone_low", "zone_high", "calibration")


@dataclass
class NowPlaying:
    player: str = ""
    artist: str = ""
    title: str = ""
    isrc: str | None = None
    mapping: dict | None = None  # Music Assistant provider mapping of the item (for its waveform)
    duration: float = 0.0

    @property
    def key(self) -> str:
        return track_key(self.artist, self.title) if self.title else ""


@dataclass
class Fixture:
    prefix: str
    index: int
    channels: list = field(default_factory=list)  # pyartnet channels owned by artnet_led's entities
    entities: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    missing_logged: bool = False
    manual: list = field(default_factory=list)  # values held while manual DMX is on

    @property
    def name(self) -> str:
        return self.prefix.split(".", 1)[-1].removesuffix("_ch").replace("_", " ").title()


class BeatEngine:
    def __init__(self, hass: HomeAssistant, fixtures: list[str], getsongbpm_key: str, default_bpm: float,
                 music_folder: str = "") -> None:
        self.hass = hass
        self._store = Store(hass, 1, f"{DOMAIN}.state")
        self._lookup = BpmLookup(async_get_clientsession(hass), {}, getsongbpm_key)
        self._fixtures = [Fixture(p, i) for i, p in enumerate(p.strip() for p in fixtures if p.strip())]
        self.default_bpm = default_bpm
        self._listeners: list[Callable[[], None]] = []
        self._tasks: list[asyncio.Task] = []
        self._render_task: asyncio.Task | None = None
        self._taps: list[float] = []

        # settings (persisted)
        self.enabled = False
        self.program = PROGRAMS[0]
        self.speed = "Normal"
        self.intensity = 100.0
        self.bpm_override = 0.0
        self.shape = SHAPES[0]
        self.coordination = COORDINATION[0]
        self.zone_width = 60.0  # degrees either side of forward
        self.zone_low = 30.0  # beam elevation above level, degrees
        self.zone_high = 75.0
        self.calibration: dict[str, dict] = {}  # fixture prefix -> DEFAULT_CAL keys
        self.manual = False  # not persisted: manual DMX is for testing
        self.colour_mode = COLOUR_MODES[0]
        self.blackout = False  # every channel of every head at 0, whatever else is going on
        self.blackout_on_silence = False  # with beat sync on: dark while nothing plays
        self.strobe = False  # strobe on top of whatever runs: the heads keep moving, only the shutter (and colour) change
        self.strobe_colour = STROBE_PROGRAM_COLOUR  # or a wheel colour name

        # live state
        self.now = NowPlaying()
        self.looked_up_bpm: float | None = None
        self.bpm_source = "default"
        self.tap_bpm: float | None = None
        self._anchor = 0.0  # loop time of beat 0
        # Random mix: the mode playing now, the one before it (blended over a beat) and when it changes
        self.coord_now = COORDINATION[0]
        self._coord_prev: str | None = None
        self._coord_from_beat = 0.0
        self._mix_until: float | None = None
        # colours: Random mix swaps same/different the same way
        self.colour_now = COLOUR_MODES[0]
        self._colour_until: float | None = None
        self._silent_since = 0.0  # loop time the music stopped
        self._dark = False  # what the render loop put out last: all zeros or not
        self._lookup_task: asyncio.Task | None = None

        # sections: each one runs a program, the main program, a strobe over it, or goes dark
        self.section_show = False
        self.section_actions = dict(DEFAULT_SECTION_ACTIONS)
        self.peak_action = "Strobe"
        self.quiet_action = "Dark"  # quiet moments anywhere in a track
        self.drop_burst_beats = 4.0  # strobe on the first beats of every drop
        self.sync_offset = 0.0  # ms the speakers play behind (+) what Music Assistant reports: lights wait as long
        self.music_folder = music_folder.strip()
        self.library = Library(hass.config.path(DOMAIN, "analysis")) if hass else None
        self.scan = {"running": False, "done": 0, "total": 0, "current": None, "errors": 0}
        self.analysis: dict | None = None  # the playing track: {"id", "source", "duration", "sections", "peaks", "timeline"}
        self._track_t0 = 0.0  # loop time the playing track started (kept in step with Music Assistant)
        self.section_now: str | None = None

    # ---- lifecycle -------------------------------------------------------------------------
    async def async_start(self) -> None:
        saved = await self._store.async_load() or {}
        for name in SETTINGS:
            if name in saved:
                setattr(self, name, saved[name])
        if self.program not in PROGRAMS:
            self.program = PROGRAMS[0]
        if self.shape not in SHAPES:
            self.shape = SHAPES[0]
        if self.coordination not in (*COORDINATION, MIX):
            self.coordination = COORDINATION[0]
        if self.colour_mode not in (*COLOUR_MODES, COLOUR_MIX):
            self.colour_mode = COLOUR_MODES[0]
        if self.strobe_colour not in (STROBE_PROGRAM_COLOUR, *(n for n, _ in COLOUR_WHEEL)):
            self.strobe_colour = STROBE_PROGRAM_COLOUR
        self._silent_since = self.hass.loop.time()
        self.section_actions = {s: self.section_actions.get(s, DEFAULT_SECTION_ACTIONS[s]) for s in SECTIONS}
        self._lookup.cache.update(saved.get("bpm_cache") or {})
        await self.hass.async_add_executor_job(self.library.load)
        self._tasks.append(self.hass.async_create_background_task(self._poll_loop(), f"{DOMAIN} poll"))
        if self.music_folder:
            self._tasks.append(self.hass.async_create_background_task(self._analyse_later(), f"{DOMAIN} analyse"))

    async def async_stop(self) -> None:
        for task in [*self._tasks, self._render_task, self._lookup_task]:
            if task:
                task.cancel()
        self._stop_render()
        await self._store.async_save(self._snapshot())

    def _snapshot(self) -> dict:
        return {**{n: getattr(self, n) for n in SETTINGS}, "bpm_cache": self._lookup.cache}

    def _save(self) -> None:
        self._store.async_delay_save(self._snapshot, 5)

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ---- settings called by entities -------------------------------------------------------
    def set(self, name: str, value) -> None:
        setattr(self, name, value)
        if name == "sync_offset":
            self._align_beats()
        self._save()
        self._update_render()
        self._notify()

    @property
    def bpm(self) -> float:
        if self.bpm_override:
            return float(self.bpm_override)
        return self.tap_bpm or self.measured_bpm or self.looked_up_bpm or float(self.default_bpm)

    @property
    def measured_bpm(self) -> float | None:
        """The tempo measured from the playing track's own audio (its analysis), if there is one."""
        return (self.analysis or {}).get("bpm")

    @property
    def source(self) -> str:
        if self.bpm_override:
            return "manual"
        if self.tap_bpm:
            return "tap"
        if self.measured_bpm:
            return "analysis"
        if self.looked_up_bpm:
            return self.bpm_source
        return f"default ({self.bpm_source})" if self.playing else "default"

    @property
    def playing(self) -> bool:
        return bool(self.now.player)

    @property
    def active(self) -> bool:
        """Beat DMX owns the channels (otherwise the Art-Net light entities do)."""
        silence = self.enabled and self.blackout_on_silence
        return self.blackout or self.manual or self.strobe or (self.enabled and self.playing) or silence

    @property
    def dark(self) -> bool:
        """All channels at 0: Blackout, or Blackout on silence once nothing has played for a few seconds."""
        if self.blackout:
            return True
        if self.manual or not (self.enabled and self.blackout_on_silence) or self.playing:
            return False
        return asyncio.get_running_loop().time() - self._silent_since >= SILENCE_SECONDS

    # ---- manual DMX and calibration ------------------------------------------------------
    def set_manual(self, on: bool) -> None:
        if on and not self.manual:
            self._bind_fixtures()
            for fx in self._fixtures:  # start from what is going out now
                fx.manual = [v if v is not None else 0 for v in self._read(fx)]
        self.set("manual", on)

    def _fixture(self, ref: str) -> Fixture:
        for fx in self._fixtures:
            if ref in (fx.prefix, fx.name, fx.name.lower()):
                return fx
        raise ValueError(f"Unknown fixture {ref!r}; known: {', '.join(fx.prefix for fx in self._fixtures)}")

    def set_channel(self, ref: str, channel: int, value: int) -> None:
        """Manual DMX: channel 1-11 of a fixture (turns manual DMX on)."""
        fx = self._fixture(ref)
        if not self.manual:
            self.set_manual(True)
        if not fx.manual:
            raise ValueError(f"{fx.prefix} has no Art-Net channels yet")
        if not 1 <= channel <= CHANNEL_COUNT:
            raise ValueError(f"Channel {channel} is not 1-{CHANNEL_COUNT}")
        fx.manual[channel - 1] = int(min(max(value, 0), 255))
        self._notify()

    def calibrate(self, ref: str, point: str) -> None:
        """Calibrate a head from where it points now (Manual DMX), or move it for a calibration step.

        up / level: take the current pan/tilt as straight up / level and facing into the room
        reverse_pan: flip which way pan turns; reset: forget the calibration
        go_up / go_level / go_left: aim the head (Manual DMX) at straight up / level forward /
        45 degrees to the left and 20 up, as the calibration has it now - to adjust or check it
        """
        fx = self._fixture(ref)
        cal = {**DEFAULT_CAL, **self.calibration.get(fx.prefix, {})}
        if point.startswith("go_"):
            if not self.manual:
                self.set_manual(True)
            if not fx.manual:
                raise ValueError(f"{fx.prefix} has no Art-Net channels yet")
            if point == "go_up":
                tilt = round(cal["tilt_up"] * 257)
                fx.manual[CH_TILT], fx.manual[CH_TILT_FINE] = tilt >> 8, tilt & 0xFF
            else:
                pan, tilt = aim_to_dmx(cal, *((0, 0) if point == "go_level" else (45, 20)))
                fx.manual[CH_PAN], fx.manual[CH_PAN_FINE] = pan >> 8, pan & 0xFF
                fx.manual[CH_TILT], fx.manual[CH_TILT_FINE] = tilt >> 8, tilt & 0xFF
            # make sure there is a beam to look at
            if fx.manual[CH_SHUTTER] <= 7:
                fx.manual[CH_SHUTTER] = SHUTTER_OPEN
            if fx.manual[CH_DIMMER] < 64:
                fx.manual[CH_DIMMER] = 255
            fx.manual[CH_FUNCTION] = FUNCTION_NONE
            self._notify()
            return
        values = fx.manual if self.manual and fx.manual else self._read(fx)
        pan = (values[CH_PAN] or 0) + (values[CH_PAN_FINE] or 0) / 256
        tilt = (values[CH_TILT] or 0) + (values[CH_TILT_FINE] or 0) / 256
        if point == "up":
            cal["tilt_up"] = tilt
        elif point == "level":
            cal["pan_forward"], cal["tilt_level"] = pan, tilt
        elif point == "reverse_pan":
            cal["pan_reverse"] = not cal["pan_reverse"]
        elif point == "reset":
            cal = dict(DEFAULT_CAL)
        else:
            raise ValueError(f"Unknown calibration point {point!r}")
        self.calibration = {**self.calibration, fx.prefix: cal}
        _LOGGER.info("Calibration of %s: %s", fx.prefix, cal)
        self._save()
        self._notify()

    # ---- Music Assistant -------------------------------------------------------------------
    def _ma_connection(self) -> tuple[str, str] | None:
        for entry in self.hass.config_entries.async_entries("music_assistant"):
            if entry.data.get("url"):
                return entry.data["url"].rstrip("/"), entry.data.get("token", "")
        return None

    async def _ma(self, command: str, **args):
        conn = self._ma_connection()
        if conn is None:
            raise RuntimeError("Music Assistant integration is not set up")
        url, token = conn
        async with async_get_clientsession(self.hass).post(
                f"{url}/api", json={"command": command, "args": args, "message_id": "beat_dmx"},
                headers={"Authorization": f"Bearer {token}"}, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _poll_loop(self) -> None:
        failures = 0
        while True:
            try:
                await self._poll()
                failures = 0
            except Exception as err:  # keep following MA whatever goes wrong in one poll
                failures += 1
                if failures in (1, 30):
                    _LOGGER.warning("Could not read Music Assistant players: %s", err)
            await asyncio.sleep(POLL_SECONDS)

    async def _poll(self) -> None:
        players = await self._ma("players/all")
        playing = [p for p in players
                   if p.get("playback_state", p.get("state")) == "playing" and p.get("current_media")
                   and not p.get("synced_to")]
        # stay on the player we are following while it plays, otherwise take the first one playing
        player = next((p for p in playing if p.get("display_name", p.get("name")) == self.now.player),
                      playing[0] if playing else None)
        if player is None:
            if self.playing:
                self.now = NowPlaying()
                self._silent_since = asyncio.get_running_loop().time()
                self._update_render()
                self._notify()
            return

        media = player["current_media"]
        artist, title = (media.get("artist") or "").strip(), (media.get("title") or "").strip()
        if not artist and " - " in title:  # radio streams often send "Artist - Title"
            artist, title = (s.strip() for s in title.split(" - ", 1))
        name = player.get("display_name") or player.get("name") or player["player_id"]
        position = float(player.get("elapsed_time") or 0)
        if player.get("elapsed_time_last_updated"):
            position += max(0.0, time.time() - float(player["elapsed_time_last_updated"]))

        loop_now = asyncio.get_running_loop().time()
        if (artist, title) != (self.now.artist, self.now.title):
            self.now = NowPlaying(name, artist, title)
            self.analysis, self.section_now = None, None
            self._track_t0 = loop_now - position
            self.tap_bpm = None
            self._taps.clear()
            self.looked_up_bpm, self.bpm_source = None, "looking up"
            # assume beat 1 is at the start of the track; tap the button to correct the phase
            self._anchor = asyncio.get_running_loop().time() - position
            if self._lookup_task:
                self._lookup_task.cancel()
            self._lookup_task = self.hass.async_create_background_task(
                self._lookup_bpm(media.get("queue_id"), self.now), f"{DOMAIN} bpm lookup")
            _LOGGER.info("Following %s on %s", f"{artist} - {title}", name)
        else:
            if name != self.now.player:
                self.now.player = name
            # keep the track position in step (seeks, pauses, the odd hiccup)
            if abs((loop_now - self._track_t0) - position) > 1.5:
                self._track_t0 = loop_now - position
                self._align_beats()
        self.now.duration = float(media.get("duration") or 0)
        self._update_render()
        self._notify()

    async def _lookup_bpm(self, queue_id: str | None, now: NowPlaying) -> None:
        if queue_id:
            try:
                queue = await self._ma("player_queues/get", queue_id=queue_id)
                item = ((queue or {}).get("current_item") or {}).get("media_item") or {}
                ids = item.get("external_ids") or []
                now.isrc = next((v for k, v in ids if k == "isrc"), None)
                now.mapping = (item.get("provider_mappings") or [None])[0]
                now.duration = float(item.get("duration") or now.duration or 0)
            except Exception as err:  # the ISRC only improves the match
                _LOGGER.debug("No ISRC for %s: %s", now.title, err)
        self.hass.async_create_background_task(self._load_analysis(now), f"{DOMAIN} sections")
        bpm, source = await self._lookup.lookup(now.artist, now.title, now.isrc)
        if now is not self.now:
            return
        self.looked_up_bpm, self.bpm_source = bpm, source
        _LOGGER.info("Tempo for %s - %s: %s (%s)", now.artist, now.title, bpm, source)
        self._save()
        self._notify()

    # ---- track analysis: sections ---------------------------------------------------------
    async def _load_analysis(self, now: NowPlaying) -> None:
        """Sections of the playing track: from a local file's profile, else Music Assistant's
        waveform - which only exists once MA has analysed the track, so it is asked again a few times."""
        prof = await self.hass.async_add_executor_job(self.library.find, now.artist, now.title)
        for wait in (0, 15, 45, 90):
            if prof or now is not self.now:
                break
            await asyncio.sleep(wait)
            if now is not self.now:
                return
            m = now.mapping or {}
            if m.get("item_id"):
                try:
                    wave = await self._ma("audio_analysis/wave_form", item_id=m["item_id"],
                                          provider_instance_id_or_domain=m.get("provider_instance") or m.get("provider_domain"))
                except Exception as err:
                    _LOGGER.debug("No waveform for %s: %s", now.title, err)
                    wave = None
                if wave and now.duration:
                    prof = ma_profile(wave, now.duration)
        if now is not self.now:
            return
        if not prof:
            self.analysis = {"id": now.key, "source": None, "duration": now.duration, "sections": [], "peaks": [], "quiet": [], "timeline": []}
        else:
            found = await self.hass.async_add_executor_job(find_sections, prof)
            duration = prof.get("duration") or now.duration or len(prof["rms"]) * prof["frame"]
            self.analysis = {"id": now.key, "source": prof.get("source"), "duration": duration,
                             "timeline": timeline(prof), "bpm": prof.get("bpm"), "beat0": prof.get("beat0"), **found}
            self._align_beats()
            _LOGGER.info("Sections of %s - %s (%s): %s", now.artist, now.title, prof.get("source"),
                         ", ".join(f"{s['type']} {s['start']:.0f}s" for s in found["sections"]))
        self._notify()

    def _align_beats(self) -> None:
        """With a measured tempo the beat count starts on the track's real first beat (a tap wins)."""
        a = self.analysis or {}
        if a.get("bpm") and a.get("beat0") is not None and not self.tap_bpm and not self.bpm_override:
            self._anchor = self._track_t0 + a["beat0"] + self.sync_offset / 1000

    @property
    def position(self) -> float | None:
        """Where the speakers are in the playing track (seconds), allowing for the sync offset."""
        if not self.playing:
            return None
        return asyncio.get_running_loop().time() - self._track_t0 - self.sync_offset / 1000

    def _section(self) -> tuple[dict | None, bool]:
        """The section playing now, and whether a peak is."""
        t, a = self.position, self.analysis
        if t is None or not a or not a.get("sections"):
            return None, False
        peak = any(p0 <= t < p1 for p0, p1 in a.get("peaks") or [])
        return section_at(a["sections"], t), peak

    def _quiet(self) -> bool:
        """A quiet moment playing now (the music goes near-silent, anywhere in the track)."""
        t, a = self.position, self.analysis
        return t is not None and bool(a) and any(q0 <= t < q1 for q0, q1 in a.get("quiet") or [])

    async def _analyse_later(self) -> None:
        await asyncio.sleep(60)  # (not while Home Assistant is still starting)
        while self.scan["running"]:  # (a folder analysed on request goes first)
            await asyncio.sleep(30)
        await self.analyse_folder()

    async def analyse_folder(self, folder: str | None = None, full: bool = False) -> None:
        """Make profiles of every new or changed audio file under the folder (one at a time)."""
        folder = folder or self.music_folder
        if not folder or self.scan["running"]:
            return
        if full:
            await self.hass.async_add_executor_job(lambda: self.library.index.clear())
        files = await self.hass.async_add_executor_job(self.library.todo, folder)
        self.scan = {"running": True, "done": 0, "total": len(files), "current": None, "errors": 0, "folder": folder}
        self._notify()
        try:
            for i, path in enumerate(files):
                self.scan["current"] = os.path.relpath(path, folder)
                self._notify()  # (the Analysed tracks sensor shows which file and how far)
                try:
                    await self.hass.async_add_executor_job(self.library.analyse, path)
                except Exception as err:
                    self.scan["errors"] += 1
                    _LOGGER.warning("Could not analyse %s: %s", path, err)
                self.scan["done"] = i + 1
                if i % 10 == 9:
                    await self.hass.async_add_executor_job(self.library.save_index)
        finally:
            await self.hass.async_add_executor_job(self.library.save_index)
            self.scan.update(running=False, current=None)
            _LOGGER.info("Analysed %d of %d files in %s (%d errors)", self.scan["done"], len(files), folder, self.scan["errors"])
            self._notify()

    # ---- DMX output ------------------------------------------------------------------------
    def _bind_fixtures(self) -> bool:
        """Find the artnet_led channels of every fixture not bound yet; True if any fixture is usable."""
        component = self.hass.data.get("light")
        for fx in self._fixtures:
            if fx.channels:
                continue
            entities = [component.get_entity(f"{fx.prefix}{i}") if component else None
                        for i in range(1, CHANNEL_COUNT + 1)]
            if any(e is None or not hasattr(e, "_channel") for e in entities):
                if not fx.missing_logged:
                    _LOGGER.error("Fixture %s1..%s%d not found among the Art-Net lights",
                                  fx.prefix, fx.prefix, CHANNEL_COUNT)
                    fx.missing_logged = True
                continue
            fx.entities, fx.channels, fx.sent = entities, [e._channel for e in entities], [None] * CHANNEL_COUNT
        return any(fx.channels for fx in self._fixtures)

    def _read(self, fx: Fixture) -> list:
        values = []
        for channel in fx.channels:
            try:
                values.append(int(channel.get_values()[0]))
            except Exception:
                values.append(None)
        return values or [None] * CHANNEL_COUNT

    def _update_render(self) -> None:
        if self.active and self._render_task is None:
            if not self._bind_fixtures():
                return
            self._render_task = self.hass.async_create_background_task(self._render_loop(), f"{DOMAIN} render")
        elif not self.active and self._render_task is not None:
            self._render_task.cancel()
            self._render_task = None
            self._stop_render()

    def _stop_render(self) -> None:
        """Hand the channels back: put out whatever the Art-Net light entities say they are."""
        for fx in self._fixtures:
            for entity, channel in zip(fx.entities, fx.channels):
                try:
                    channel.set_values(entity.get_target_values())
                except Exception as err:
                    _LOGGER.debug("Could not restore %s: %s", entity.entity_id, err)
            fx.sent = [None] * CHANNEL_COUNT

    async def _render_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                beats = (loop.time() - self._anchor) * self.bpm / 60
                self._mix_tick(loop.time(), beats)
                self._colour_tick(loop.time(), beats)
                dark = self.dark
                if dark != self._dark:
                    self._dark = dark
                    self._notify()
                for fx in self._fixtures:
                    if not fx.channels:
                        continue
                    if dark:
                        frame = [0] * CHANNEL_COUNT
                    elif self.manual and fx.manual:
                        frame = fx.manual
                    elif self.enabled and (self.playing or self.blackout_on_silence):
                        frame = self._show_frame(beats, fx)
                    else:  # (only the strobe is on: it strobes wherever the light entities have the head)
                        frame = self._entity_frame(fx)
                    if self.strobe and not dark:
                        frame = self._strobe(frame)
                    for i, value in enumerate(frame):
                        if value != fx.sent[i]:
                            fx.channels[i].set_values([value])
                            fx.sent[i] = value
                await asyncio.sleep(1 / FPS)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Beat render stopped")
            self._render_task = None

    def _mix_tick(self, now: float, beats: float) -> None:
        """Random mix: after 20-45 s (at random) move on to another of the four modes, on a bar line."""
        if self.coordination != MIX:
            self.coord_now, self._mix_until = self.coordination, None
            return
        due = self._mix_until is None or (now >= self._mix_until and int(max(beats, 0)) % 4 == 0)
        if not due:
            return
        self._coord_prev = self.coord_now if self._mix_until is not None else None
        self.coord_now = random.choice([c for c in COORDINATION if c != self.coord_now])
        self._coord_from_beat = beats
        self._mix_until = now + random.uniform(*MIX_SECONDS)
        _LOGGER.debug("Random mix: %s for %.0f s", self.coord_now, self._mix_until - now)
        self._notify()

    def _show_frame(self, beats: float, fx: Fixture) -> list[int]:
        """The beat show, as the section playing now wants it (Section show), or the main program."""
        if not (self.section_show and self.manual is False and self.playing):
            return self._frame(beats, fx)
        sec, peak = self._section()
        kind = sec["type"] if sec else None
        if kind != self.section_now and fx.index == 0:
            self.section_now = kind
            self.hass.bus.async_fire(f"{DOMAIN}_section", {"section": kind, "track": f"{self.now.artist} - {self.now.title}"})
            self._notify()
        action = self.section_actions.get(kind, "No change") if kind else "No change"
        quiet = self._quiet()
        if (peak and self.peak_action == "Dark") or (quiet and self.quiet_action == "Dark") or action == "Dark":
            return [0] * CHANNEL_COUNT
        program = action if action in PROGRAMS else None
        ramp = 1.0
        if kind == "Build-up" and sec["end"] > sec["start"]:  # steps get faster towards the drop: x1 -> x2
            ramp = 1 + min(max((self.position - sec["start"]) / (sec["end"] - sec["start"]), 0), 1)
        frame = self._frame(beats, fx, program, ramp)
        burst = kind == "Drop" and self.position - sec["start"] < self.drop_burst_beats * 60 / self.bpm
        if action == STROBE_OVER or burst or (peak and self.peak_action == "Strobe") or (quiet and self.quiet_action == "Strobe"):
            frame = self._strobe(frame)
        return frame

    def _entity_frame(self, fx: Fixture) -> list[int]:
        values = []
        for entity in fx.entities:
            try:
                values.append(int(entity.get_target_values()[0]))
            except Exception:
                values.append(0)
        return values

    def _strobe(self, frame: list[int]) -> list[int]:
        """Strobe over a frame: fastest strobe, the beam lit, and the strobe colour if one is chosen."""
        frame = list(frame)
        frame[CH_SHUTTER] = STROBE_FAST
        frame[CH_FUNCTION] = FUNCTION_NONE
        if frame[CH_DIMMER] < 64:  # (Pulse dips the dimmer; a strobe should be seen)
            frame[CH_DIMMER] = _byte(255 * max(self.intensity, 25) / 100)
        names = [n for n, _ in COLOUR_WHEEL]
        if self.strobe_colour in names:
            frame[CH_COLOUR] = COLOURS[names.index(self.strobe_colour)]
        return frame

    def _colour_tick(self, now: float, beats: float) -> None:
        """Colours on Random mix: same or different in turn, each for 20-45 s, changing on a bar line."""
        if self.colour_mode != COLOUR_MIX:
            self.colour_now, self._colour_until = self.colour_mode, None
            return
        if self._colour_until is not None and not (now >= self._colour_until and int(max(beats, 0)) % 4 == 0):
            return
        if self._colour_until is None:
            self.colour_now = random.choice(COLOUR_MODES)
        else:
            self.colour_now = next(c for c in COLOUR_MODES if c != self.colour_now)
        self._colour_until = now + random.uniform(*MIX_SECONDS)
        self._notify()

    def _frame(self, beats: float, fx: Fixture, program: str | None = None, ramp: float = 1.0) -> list[int]:
        steps = max(beats, 0) * SPEEDS.get(self.speed, 1.0) * ramp
        step, frac = int(steps), steps % 1
        program = program or self.program
        moving = program in ("Full show", "Movement", "Pulse", "Strobe", "Fast strobe")  # (Colour & gobo holds still)
        chasing = program in ("Full show", "Colour & gobo", "Pulse", "Strobe", "Fast strobe")

        # aim inside the zone: moving along the shape, or held in the middle of it
        u, v = zone_point(self.shape, self.coord_now, fx.index, steps) if moving else (0.0, 0.0)
        # (Random mix: for a beat after a change the head glides from where the old mode had it)
        glide = beats - self._coord_from_beat
        if moving and self._coord_prev and 0 <= glide < 1:
            u0, v0 = zone_point(self.shape, self._coord_prev, fx.index, steps)
            k = 0.5 - 0.5 * math.cos(math.pi * glide)
            u, v = u0 + (u - u0) * k, v0 + (v - v0) * k
        azimuth, elevation = zone_to_angles(u, v, self.zone_width, self.zone_low, self.zone_high)
        pan, tilt = aim_to_dmx(self.calibration.get(fx.prefix), azimuth, elevation)

        slot, gobo = 0, GOBOS[0]
        if program == "Colour & gobo":
            slot, gobo = step % len(COLOURS), GOBOS[(step // 2) % len(GOBOS)]
        elif chasing:
            slot, gobo = (step // 4) % len(COLOURS), GOBOS[(step // 8) % len(GOBOS)]
        # (Different: every second head a few slots on round the wheel - never the same colour)
        if chasing and self.colour_now == "Different" and fx.index % 2 == 1:
            slot = (slot + COLOUR_OFFSET) % len(COLOURS)
        colour = COLOURS[slot]
        if program == "Pulse":
            gobo = GOBOS[0]

        level = self.intensity / 100
        if program == "Pulse":
            level *= 0.15 + 0.85 * (1 - frac) ** 2
        shutter = SHUTTER_OPEN
        if program == "Strobe":
            # 60 BPM -> slowest strobe, 180 BPM -> fastest
            shutter = round(STROBE_SLOW + (STROBE_FAST - STROBE_SLOW) * min(max((self.bpm - 60) / 120, 0), 1))
        elif program == "Fast strobe":
            shutter = STROBE_FAST

        values = [0] * CHANNEL_COUNT
        values[CH_PAN], values[CH_PAN_FINE] = pan >> 8, pan & 0xFF
        values[CH_TILT], values[CH_TILT_FINE] = tilt >> 8, tilt & 0xFF
        values[CH_COLOUR] = colour
        values[CH_GOBO] = gobo
        values[CH_SHUTTER] = shutter
        values[CH_DIMMER] = _byte(255 * level)
        values[CH_PT_SPEED] = PT_SPEED_FAST
        values[CH_FUNCTION] = FUNCTION_NONE
        values[CH_DIM_MODE] = DIM_MODE_STANDARD
        return values

    def live(self) -> dict:
        """What is going out on the wire right now, read back from the Art-Net channels."""
        self._bind_fixtures()
        fixtures = [{
            "name": fx.name, "prefix": fx.prefix, "index": fx.index,
            "start": fx.channels[0]._start if fx.channels else None,
            "values": self._read(fx),
            "calibration": {**DEFAULT_CAL, **self.calibration.get(fx.prefix, {})},
        } for fx in self._fixtures]
        loop = asyncio.get_running_loop()
        return {"active": self.active, "enabled": self.enabled, "manual": self.manual,
                "bpm": round(self.bpm, 1), "source": self.source,
                "beat": round((loop.time() - self._anchor) * self.bpm / 60, 3) if self.playing else None,
                "program": self.program, "speed": self.speed, "shape": self.shape, "coordination": self.coordination,
                "coordination_now": self.coord_now,
                "colour_mode": self.colour_mode, "colour_now": self.colour_now,
                "blackout": self.blackout, "blackout_on_silence": self.blackout_on_silence, "dark": self.dark,
                "strobe": self.strobe, "strobe_colour": self.strobe_colour,
                "section_show": self.section_show, "section": self._section_live(),
                "zone": {"width": self.zone_width, "low": self.zone_low, "high": self.zone_high},
                "colours": [{"name": n, "hex": h} for n, h in COLOUR_WHEEL],
                "player": self.now.player or None, "artist": self.now.artist or None, "title": self.now.title or None,
                "fixtures": fixtures}

    def _section_live(self) -> dict | None:
        a = self.analysis
        if not a:
            return None
        t = self.position
        sec, peak = self._section()
        nxt = next((s for s in a["sections"] if t is not None and s["start"] > t), None)
        return {"id": a["id"], "source": a["source"], "t": round(t, 2) if t is not None else None,
                "type": sec["type"] if sec else None, "peak": peak, "quiet": self._quiet(),
                "next": nxt["type"] if nxt else None, "next_in": round(nxt["start"] - t, 1) if nxt else None}

    @property
    def status(self) -> dict:
        return {"player": self.now.player or None, "artist": self.now.artist or None,
                "title": self.now.title or None, "source": self.source, "active": self.active,
                "coordination_now": self.coord_now, "colour_now": self.colour_now, "dark": self.dark,
                "fixtures": [fx.prefix for fx in self._fixtures]}


def _byte(value: float) -> int:
    return int(min(max(round(value), 0), 255))
