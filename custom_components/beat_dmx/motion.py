"""Where the heads point: movement shapes in a zone, how the heads move together, and aiming to DMX.

A point in the zone is (u, v), both -1..1: u = left/right across the zone, v = low/high.
The zone is an angle window seen from each head: `width` degrees either side of its forward
direction, and beam elevations from `low` to `high` degrees above level.
"""
from __future__ import annotations

import math
import random

SHAPES = ["Square", "Circle", "Figure 8", "Sweep", "Random"]
COORDINATION = ["Mirror", "Unison", "Opposite", "Follow"]
MIX = "Random mix"  # one of the four at a time, changing at random after MIX_SECONDS
MIX_SECONDS = (20, 45)

# square-then-cross corners, one per step
SQUARE = [(-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0)]
PAN_DEGREES = 540

# calibration of one head; DMX values (0-255, may be fractional)
DEFAULT_CAL = {"pan_forward": 128.0, "pan_reverse": False, "tilt_up": 128.0, "tilt_level": 43.0}


def _ease(frac: float) -> float:
    """Travel in the first 70% of a step, then hold."""
    return 0.5 - 0.5 * math.cos(math.pi * min(frac / 0.7, 1.0))


def _random_point(step: int) -> tuple[float, float]:
    rnd = random.Random(step * 7919)
    return rnd.uniform(-1, 1), rnd.uniform(-1, 1)


def shape_point(shape: str, steps: float) -> tuple[float, float]:
    step, frac = math.floor(steps), steps % 1
    if shape == "Circle":  # one turn every 8 steps
        a = 2 * math.pi * steps / 8
        return math.cos(a), math.sin(a)
    if shape == "Figure 8":
        a = 2 * math.pi * steps / 8
        return math.sin(a), math.sin(2 * a)
    if shape == "Sweep":  # side to side every step, low and high on alternate bars
        u = -1 if step % 2 == 0 else 1
        u_prev = -u
        v = -0.5 if (step // 4) % 2 == 0 else 0.5
        return u_prev + (u - u_prev) * _ease(frac), v
    if shape == "Random":
        (x0, y0), (x1, y1) = _random_point(step - 1), _random_point(step)
    else:
        (x0, y0), (x1, y1) = SQUARE[(step - 1) % len(SQUARE)], SQUARE[step % len(SQUARE)]
    e = _ease(frac)
    return x0 + (x1 - x0) * e, y0 + (y1 - y0) * e


def zone_point(shape: str, coordination: str, index: int, steps: float) -> tuple[float, float]:
    """The point head `index` aims at; even heads lead, odd heads follow the coordination."""
    odd = index % 2 == 1
    if odd and coordination == "Follow":  # one step behind the head before it
        return shape_point(shape, max(steps - 1, 0))
    u, v = shape_point(shape, steps)
    if odd and coordination == "Mirror":
        return -u, v
    if odd and coordination == "Opposite":
        return -u, -v
    return u, v


def zone_to_angles(u: float, v: float, width: float, low: float, high: float) -> tuple[float, float]:
    """(u, v) -> (azimuth left of forward, elevation above level), in degrees."""
    low, high = min(low, high), max(low, high)
    return -u * width, low + (v + 1) / 2 * (high - low)


def aim_to_dmx(cal: dict, azimuth: float, elevation: float) -> tuple[int, int]:
    """Azimuth (degrees left of the head's forward) and elevation -> 16-bit pan and tilt."""
    cal = {**DEFAULT_CAL, **(cal or {})}
    pan_deg = cal["pan_forward"] * PAN_DEGREES / 255 + (-azimuth if cal["pan_reverse"] else azimuth)
    while pan_deg < 0:
        pan_deg += 360
    while pan_deg > PAN_DEGREES:
        pan_deg -= 360
    # tilt: tilt_up = straight up, tilt_level = level and facing forward; in between is 90 degrees
    span = cal["tilt_level"] - cal["tilt_up"] or -85.0
    tilt_dmx = cal["tilt_up"] + span * (90 - elevation) / 90
    return _word(pan_deg / PAN_DEGREES * 65535), _word(tilt_dmx * 257)


def _word(value: float) -> int:
    return int(min(max(round(value), 0), 65535))
