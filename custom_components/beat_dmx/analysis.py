"""Track analysis: energy curves of audio files (ffmpeg), stored per track, and the sections found in them.

A profile is two curves at 10 frames a second, 0-255: `rms` (the whole signal) and `bass` (below
~150 Hz). Profiles of local files are made here with ffmpeg; for anything else Music Assistant's
waveform (1800 RMS bins over the track, no bass) is used when it has analysed the track.
"""
from __future__ import annotations

import array
import hashlib
import json
import logging
import math
import os
import subprocess
from pathlib import Path

from .bpm import track_key

_LOGGER = logging.getLogger(__name__)

FRAME = 0.1  # seconds per profile frame
RATE = 8000  # decode sample rate
AUDIO = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wma", ".alac")
SECTIONS = ["Intro", "Main", "Breakdown", "Build-up", "Quiet gap", "Drop", "Outro"]


# ---- making profiles (runs in an executor thread) -------------------------------------------
def _probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
                         capture_output=True, timeout=60, check=False).stdout
    fmt = (json.loads(out or b"{}").get("format") or {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    artist, title = tags.get("artist", ""), tags.get("title", "")
    if not title:  # "Artist - Title.mp3"
        stem = Path(path).stem
        artist, title = (stem.split(" - ", 1) + [""])[:2] if " - " in stem else ("", stem)
    return {"artist": artist.strip(), "title": title.strip(), "duration": float(fmt.get("duration") or 0)}


def profile_file(path: str) -> dict:
    """Decode a file to mono 8 kHz (and a bass-only copy) and measure both every 0.1 s."""
    info = _probe(path)
    graph = (f"[0:a:0]aresample={RATE},aformat=channel_layouts=mono,asplit[a][b];"
             "[b]lowpass=f=150,lowpass=f=150[lo];[a][lo]amerge=inputs=2[out]")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-filter_complex", graph,
                           "-map", "[out]", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                          capture_output=True, timeout=600, check=False)
    if proc.returncode or not proc.stdout:
        raise RuntimeError((proc.stderr or b"ffmpeg failed").decode(errors="ignore").strip()[-300:])
    raw = proc.stdout[: len(proc.stdout) // 4 * 4]
    step = int(RATE * FRAME)  # samples per frame (and channel)
    try:  # numpy when Home Assistant has it: many times faster
        import numpy as np  # noqa: PLC0415

        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float64).reshape(-1, 2)
        frames = len(pcm) // step
        sq = (pcm[: frames * step] ** 2).reshape(frames, step, 2).mean(axis=1)
        rms, bass = np.sqrt(sq[:, 0]).tolist(), np.sqrt(sq[:, 1]).tolist()
    except ImportError:
        pcm = array.array("h")
        pcm.frombytes(raw)
        rms, bass = [], []
        for i in range(0, len(pcm) - 2 * step + 1, 2 * step):  # interleaved: full, bass
            chunk = pcm[i:i + 2 * step]
            full, low = chunk[0::2], chunk[1::2]
            rms.append(math.sqrt(sum(x * x for x in full) / len(full)))
            bass.append(math.sqrt(sum(x * x for x in low) / len(low)))
    return {**info, "rms": _scale(rms), "bass": _scale(bass), "frame": FRAME, "source": "file"}


def _scale(values: list[float]) -> list[int]:
    """0-255 relative to the track's loud level (its 98th percentile)."""
    if not values:
        return []
    ref = sorted(values)[int(len(values) * 0.98)] or 1
    return [min(255, round(v / ref * 200)) for v in values]


# ---- the library of profiles ------------------------------------------------------------------
class Library:
    """Profiles on disk (<config>/beat_dmx/analysis/), found by artist|title."""

    def __init__(self, folder: str) -> None:
        self.folder = Path(folder)
        self.index: dict[str, dict] = {}  # file path -> {"key", "mtime", "file"}
        self.by_key: dict[str, str] = {}  # track key -> profile file name

    def load(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            self.index = json.loads((self.folder / "index.json").read_text())
        except (OSError, ValueError):
            self.index = {}
        self.by_key = {v["key"]: v["file"] for v in self.index.values() if v.get("key")}

    def save_index(self) -> None:
        tmp = self.folder / "index.json.tmp"
        tmp.write_text(json.dumps(self.index))
        os.replace(tmp, self.folder / "index.json")

    def todo(self, root: str) -> list[str]:
        """Audio files under root that are new or changed since they were analysed."""
        files = []
        for dirpath, _, names in os.walk(root):
            for name in names:
                if name.lower().endswith(AUDIO) and not name.startswith("."):
                    path = os.path.join(dirpath, name)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if self.index.get(path, {}).get("mtime") != mtime:
                        files.append(path)
        return sorted(files)

    def analyse(self, path: str) -> dict:
        prof = profile_file(path)
        name = hashlib.sha1(path.encode()).hexdigest()[:16] + ".json"
        (self.folder / name).write_text(json.dumps(prof))
        key = track_key(prof["artist"], prof["title"])
        self.index[path] = {"key": key, "mtime": os.path.getmtime(path), "file": name}
        self.by_key[key] = name
        return prof

    def find(self, artist: str, title: str) -> dict | None:
        name = self.by_key.get(track_key(artist, title))
        if not name:
            return None
        try:
            return json.loads((self.folder / name).read_text())
        except (OSError, ValueError):
            return None


def ma_profile(wave: list[float], duration: float) -> dict:
    """Music Assistant's waveform (RMS, 1800 bins over the track) as a profile without bass."""
    return {"rms": [min(255, round(v * 200)) for v in wave], "bass": None,
            "frame": duration / len(wave) if wave else FRAME, "duration": duration, "source": "music assistant"}


# ---- sections ---------------------------------------------------------------------------------
def _smooth(values: list[float], half: int) -> list[float]:
    out, n = [], len(values)
    pre = [0.0]
    for v in values:
        pre.append(pre[-1] + v)
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        out.append((pre[b] - pre[a]) / (b - a))
    return out


def _norm(values: list[float]) -> list[float]:
    """Relative to the curve's own loud level (its 95th percentile): kick-heavy tracks reach 1 too."""
    ref = sorted(values)[int(len(values) * 0.95)] or 1
    return [v / ref for v in values]


def find_sections(prof: dict) -> dict:
    """Sections ({start, end, type} in seconds) and peaks ([start, end]) of a profile.

    Drops are where the energy (bass when there is a bass curve) jumps back to full after at least
    8 s low; the low stretch before a drop is a breakdown, its rising last part the build-up, and a
    near-silence in the last 3 s before the drop the quiet gap. Full stretches that are not drops are
    Main; a low start / end is Intro / Outro. Peaks: moments well above the loud level around them;
    quiet moments: near-silences of 0.3 s or more anywhere but the first and last 2 s.
    """
    dt = prof["frame"]
    rms = [v / 200 for v in prof["rms"]]
    energy = [v / 200 for v in prof["bass"]] if prof.get("bass") else rms
    n = len(energy)
    if n < 20:
        return {"sections": [], "peaks": [], "quiet": []}
    e1 = _norm(_smooth(energy, max(1, int(1 / dt))))
    # full / low with hysteresis, then stretches of at least 6 s
    state, states = e1[0] > 0.55, []
    for v in e1:
        if state and v < 0.4:
            state = False
        elif not state and v > 0.6:
            state = True
        states.append(state)
    runs = []  # [start index, end index, full]
    for i, s in enumerate(states):
        if runs and runs[-1][2] == s:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, s])
    merged = []
    for r in runs:
        if merged and (r[1] - r[0]) * dt < 6:
            merged[-1][1] = r[1]  # too short: part of the stretch before
        elif merged and merged[-1][2] == r[2]:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    out = []
    for j, (a, b, full) in enumerate(merged):
        t0, t1 = a * dt, b * dt
        if full:
            dropped = j > 0 and not merged[j - 1][2] and (merged[j - 1][1] - merged[j - 1][0]) * dt >= 8
            out.append({"start": t0, "end": t1, "type": "Drop" if dropped else "Main"})
            continue
        if j == 0:
            out.append({"start": t0, "end": t1, "type": "Intro"})
            continue
        if j == len(merged) - 1:
            out.append({"start": t0, "end": t1, "type": "Outro"})
            continue
        if t1 - t0 < 8:  # a short dip: just a breakdown (what follows is no drop)
            out.append({"start": t0, "end": t1, "type": "Breakdown"})
            continue
        # a low stretch between full ones: breakdown, then build-up, then maybe a quiet gap
        gap_start = t1
        r1 = _norm(_smooth(rms, max(1, int(0.3 / dt))))
        k = b - 1
        while k > max(a, b - int(3 / dt)) and r1[k] < 0.12:
            k -= 1
        if (b - 1 - k) * dt >= 0.3:
            gap_start = (k + 1) * dt
        # build-up: from where the energy last started climbing (at most 30 s, at least 4 s)
        rs = _norm(_smooth(rms, max(1, int(2 / dt))))
        k = int(gap_start / dt) - 1
        lo = max(a, k - int(30 / dt))
        while k > lo and rs[k - int(1 / dt)] <= rs[k] + 0.01:
            k -= int(1 / dt)
        build_start = min(max(k * dt, t0), gap_start - 4) if gap_start - t0 > 8 else t0
        if build_start > t0 + 2:
            out.append({"start": t0, "end": build_start, "type": "Breakdown"})
        else:
            build_start = t0
        out.append({"start": build_start, "end": gap_start, "type": "Build-up"})
        if gap_start < t1:
            out.append({"start": gap_start, "end": t1, "type": "Quiet gap"})
    # peaks: short moments clearly above the loud level of the 8 s around them
    loud = sorted(rms)[int(n * 0.97)] or 1
    e8 = _smooth(rms, int(4 / dt))
    r03 = _smooth(rms, max(1, int(0.15 / dt)))
    peaks, start = [], None
    for i, v in enumerate(r03):
        hot = v >= loud * 1.02 and v > e8[i] * 1.25
        if hot and start is None:
            start = i
        elif not hot and start is not None:
            if (i - start) * dt >= 0.2:
                peaks.append([round(start * dt, 2), round(i * dt, 2)])
            start = None
    for s in out:
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    # quiet moments: the music all but stops (under 10% of its loud level over half a second) for
    # 0.4-8 s - not the gaps between kicks, not a whole quiet section, not in an intro or outro
    r05 = _norm(_smooth(rms, max(1, int(0.25 / dt))))
    ends = [(s["start"], s["end"]) for s in out if s["type"] in ("Intro", "Outro")]
    quiet, start = [], None
    for i, v in enumerate(r05 + [1.0]):
        hush = v < 0.1
        if hush and start is None:
            start = i
        elif not hush and start is not None:
            a, b = start * dt, i * dt
            if quiet and a - quiet[-1][1] < 0.5:  # (one moment, not two)
                a = quiet.pop()[0]
            if 0.4 <= b - a <= 8 and b < n * dt - 1 and not any(e0 <= a < e1_ for e0, e1_ in ends):
                quiet.append([round(a, 2), round(b, 2)])
            start = None
    return {"sections": out, "peaks": peaks[:200], "quiet": quiet[:200]}


def timeline(prof: dict, points: int = 300) -> list[int]:
    """The energy curve squeezed to `points` values for drawing (bass when there is one)."""
    curve = prof.get("bass") or prof["rms"]
    if not curve:
        return []
    step = len(curve) / points
    return [max(curve[int(i * step):max(int(i * step) + 1, int((i + 1) * step))]) for i in range(points)]


def section_at(sections: list[dict], t: float) -> dict | None:
    for s in sections:
        if s["start"] <= t < s["end"]:
            return s
    return None

