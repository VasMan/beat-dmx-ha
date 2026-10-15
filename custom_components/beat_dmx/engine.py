"""Follows Music Assistant, looks up the tempo and drives the fixtures' DMX channels on the beat."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .bpm import BpmLookup, track_key
from .const import (CH_COLOUR, CH_DIM_MODE, CH_DIMMER, CH_FUNCTION, CH_GOBO, CH_PAN, CH_PAN_FINE,
                    CH_PT_SPEED, CH_SHUTTER, CH_TILT, CH_TILT_FINE, CHANNEL_COUNT, COLOURS,
                    DIM_MODE_STANDARD, DOMAIN, FUNCTION_NONE, GOBOS, PROGRAMS, PT_SPEED_FAST, SHUTTER_OPEN,
                    SPEEDS, STROBE_FAST, STROBE_SLOW)
from .motion import COORDINATION, DEFAULT_CAL, SHAPES, aim_to_dmx, zone_point, zone_to_angles

_LOGGER = logging.getLogger(__name__)

POLL_SECONDS = 2
FPS = 25
TAP_RESET_SECONDS = 2.0
SETTINGS = ("enabled", "program", "speed", "intensity", "bpm_override", "shape", "coordination",
            "zone_width", "zone_low", "zone_high", "calibration")


@dataclass
class NowPlaying:
    player: str = ""
    artist: str = ""
    title: str = ""
    isrc: str | None = None

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
    def __init__(self, hass: HomeAssistant, fixtures: list[str], getsongbpm_key: str, default_bpm: float) -> None:
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

        # live state
        self.now = NowPlaying()
        self.looked_up_bpm: float | None = None
        self.bpm_source = "default"
        self.tap_bpm: float | None = None
        self._anchor = 0.0  # loop time of beat 0
        self._lookup_task: asyncio.Task | None = None

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
        if self.coordination not in COORDINATION:
            self.coordination = COORDINATION[0]
        self._lookup.cache.update(saved.get("bpm_cache") or {})
        self._tasks.append(self.hass.async_create_background_task(self._poll_loop(), f"{DOMAIN} poll"))

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
        self._save()
        self._update_render()
        self._notify()

    @property
    def bpm(self) -> float:
        if self.bpm_override:
            return float(self.bpm_override)
        return self.tap_bpm or self.looked_up_bpm or float(self.default_bpm)

    @property
    def source(self) -> str:
        if self.bpm_override:
            return "manual"
        if self.tap_bpm:
            return "tap"
        if self.looked_up_bpm:
            return self.bpm_source
        return f"default ({self.bpm_source})" if self.playing else "default"

    @property
    def playing(self) -> bool:
        return bool(self.now.player)

    @property
    def active(self) -> bool:
        return self.manual or (self.enabled and self.playing)

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
        fx.manual[channel - 1] = int(min(max(value, 0), 255))
        self._notify()

    def calibrate(self, ref: str, point: str) -> None:
        """Take the head's current manual pan/tilt as 'up' (straight up) or 'level' (level, facing
        into the room), or flip which way pan turns."""
        fx = self._fixture(ref)
        cal = {**DEFAULT_CAL, **self.calibration.get(fx.prefix, {})}
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

    def tap(self) -> None:
        t = asyncio.get_running_loop().time()
        if self._taps and t - self._taps[-1] > TAP_RESET_SECONDS:
            self._taps.clear()
        self._taps.append(t)
        self._anchor = t  # a tap always lands on a beat
        if len(self._taps) >= 4:
            self.tap_bpm = round(60 * (len(self._taps) - 1) / (self._taps[-1] - self._taps[0]), 1)
            if self.now.key:
                self._lookup.cache[self.now.key] = {"bpm": self.tap_bpm, "source": "tap"}
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

        if (artist, title) != (self.now.artist, self.now.title):
            self.now = NowPlaying(name, artist, title)
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
        elif name != self.now.player:
            self.now.player = name
        self._update_render()
        self._notify()

    async def _lookup_bpm(self, queue_id: str | None, now: NowPlaying) -> None:
        if queue_id:
            try:
                queue = await self._ma("player_queues/get", queue_id=queue_id)
                ids = (((queue or {}).get("current_item") or {}).get("media_item") or {}).get("external_ids") or []
                now.isrc = next((v for k, v in ids if k == "isrc"), None)
            except Exception as err:  # the ISRC only improves the match
                _LOGGER.debug("No ISRC for %s: %s", now.title, err)
        bpm, source = await self._lookup.lookup(now.artist, now.title, now.isrc)
        if now is not self.now:
            return
        self.looked_up_bpm, self.bpm_source = bpm, source
        _LOGGER.info("Tempo for %s - %s: %s (%s)", now.artist, now.title, bpm, source)
        self._save()
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
                for fx in self._fixtures:
                    if not fx.channels:
                        continue
                    frame = fx.manual if self.manual and fx.manual else self._frame(beats, fx)
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

    def _frame(self, beats: float, fx: Fixture) -> list[int]:
        steps = max(beats, 0) * SPEEDS.get(self.speed, 1.0)
        step, frac = int(steps), steps % 1
        program = self.program
        moving = program in ("Full show", "Movement")
        chasing = program in ("Full show", "Colour & gobo", "Pulse", "Strobe")

        # aim inside the zone: moving along the shape, or held in the middle of it
        u, v = zone_point(self.shape, self.coordination, fx.index, steps) if moving else (0.0, 0.0)
        azimuth, elevation = zone_to_angles(u, v, self.zone_width, self.zone_low, self.zone_high)
        pan, tilt = aim_to_dmx(self.calibration.get(fx.prefix), azimuth, elevation)

        colour, gobo = COLOURS[0], GOBOS[0]
        if program == "Colour & gobo":
            colour, gobo = COLOURS[step % len(COLOURS)], GOBOS[(step // 2) % len(GOBOS)]
        elif chasing:
            colour, gobo = COLOURS[(step // 4) % len(COLOURS)], GOBOS[(step // 8) % len(GOBOS)]
        if program == "Pulse":
            gobo = GOBOS[0]

        level = self.intensity / 100
        if program == "Pulse":
            level *= 0.15 + 0.85 * (1 - frac) ** 2
        shutter = SHUTTER_OPEN
        if program == "Strobe":
            # 60 BPM -> slowest strobe, 180 BPM -> fastest
            shutter = round(STROBE_SLOW + (STROBE_FAST - STROBE_SLOW) * min(max((self.bpm - 60) / 120, 0), 1))

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
                "zone": {"width": self.zone_width, "low": self.zone_low, "high": self.zone_high},
                "player": self.now.player or None, "artist": self.now.artist or None, "title": self.now.title or None,
                "fixtures": fixtures}

    @property
    def status(self) -> dict:
        return {"player": self.now.player or None, "artist": self.now.artist or None,
                "title": self.now.title or None, "source": self.source, "active": self.active,
                "fixtures": [fx.prefix for fx in self._fixtures]}


def _byte(value: float) -> int:
    return int(min(max(round(value), 0), 255))
