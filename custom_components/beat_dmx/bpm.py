"""Tempo lookup for a track: cache, Deezer (ISRC, then search) and optionally GetSongBPM."""
from __future__ import annotations

import logging
import re
import statistics
import unicodedata

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEEZER = "https://api.deezer.com"
GETSONGBPM = "https://api.getsong.co/search/"
TIMEOUT = aiohttp.ClientTimeout(total=8)
# releases whose tempo differs from the original are never used; same-tempo edits are used before live takes
DIFFERENT_TEMPO = ("remix", "rmx", "unplugged", "acoustic", "cover", "karaoke", "slowed", "sped up", "piano",
                   "reggae", "dub", "originally performed", "made famous", "tribute")
LIVE = ("live", "demo", "rough mix")


def track_key(artist: str, title: str) -> str:
    return f"{_norm(artist)}|{_norm(title)}"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.split(r" - | feat\.? | ft\.? ", text)[0]
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _same_artist(found: str, wanted: str) -> bool:
    """MA may list several artists ("calvin harris dua lipa"); Deezer gives the main one."""
    return bool(found) and (wanted.startswith(found) or found.startswith(wanted))


def _tier(title: str) -> int:
    """0 = the original, 1 = an edit/remaster of it, 2 = live or demo, 3 = a different tempo."""
    low = (title or "").lower()
    if any(w in low for w in DIFFERENT_TEMPO):
        return 3
    if any(w in low for w in LIVE):
        return 2
    return 1 if re.search(r"\(|\[| - ", low) else 0


class BpmLookup:
    def __init__(self, session: aiohttp.ClientSession, cache: dict, getsongbpm_key: str = "") -> None:
        self._session = session
        self.cache = cache  # track_key -> {"bpm": float, "source": str}
        self._key = getsongbpm_key
        self.last_note = ""

    async def lookup(self, artist: str, title: str, isrc: str | None) -> tuple[float | None, str]:
        key = track_key(artist, title)
        if key in self.cache:
            hit = self.cache[key]
            return hit["bpm"], hit["source"]
        for source, finder in (("deezer isrc", lambda: self._deezer_isrc(isrc)),
                               ("getsongbpm", lambda: self._getsongbpm(artist, title)),
                               ("deezer search", lambda: self._deezer_search(artist, title))):
            try:
                bpm = await finder()
            except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as err:
                _LOGGER.debug("%s lookup failed for %s - %s: %s", source, artist, title, err)
                continue
            if bpm:
                bpm = round(float(bpm), 1)
                if self.last_note:
                    source, self.last_note = f"{source} ({self.last_note})", ""
                self.cache[key] = {"bpm": bpm, "source": source}
                return bpm, source
        return None, "not found"

    async def _get(self, url: str, **params) -> dict:
        async with self._session.get(url, params=params, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _deezer_isrc(self, isrc: str | None) -> float | None:
        if not isrc:
            return None
        data = await self._get(f"{DEEZER}/track/isrc:{isrc}")
        return data.get("bpm") or None

    async def _getsongbpm(self, artist: str, title: str) -> float | None:
        if not self._key or not artist:
            return None
        data = await self._get(GETSONGBPM, api_key=self._key, type="both",
                               lookup=f"song:{title} artist:{artist}")
        results = data.get("search")
        if not isinstance(results, list):
            return None
        for item in results:
            if _norm(item.get("title", "")) == _norm(title) and \
                    _same_artist(_norm((item.get("artist") or {}).get("name", "")), _norm(artist)):
                return float(item["tempo"]) if item.get("tempo") else None
        return None

    async def _deezer_search(self, artist: str, title: str) -> float | None:
        """Median BPM over the releases whose artist and title match (Deezer only fills bpm on some)."""
        if not artist:
            return None
        data = await self._get(f"{DEEZER}/search", q=f"{artist} {title}", limit=15)
        want_title, want_artist = _norm(title), _norm(artist)
        candidates = [(_tier(t.get("title", "")), t["id"]) for t in data.get("data", [])
                      if _norm(t.get("title", "")) == want_title
                      and _same_artist(_norm((t.get("artist") or {}).get("name", "")), want_artist)]
        max_tier = 2 if _tier(title) <= 2 else 3
        by_tier: dict[int, list[float]] = {}
        for tier, track_id in sorted(c for c in candidates if c[0] <= max_tier)[:8]:
            bpm = (await self._get(f"{DEEZER}/track/{track_id}")).get("bpm")
            if bpm:
                by_tier.setdefault(tier, []).append(float(bpm))
        if not by_tier:
            return None
        best = min(by_tier)
        if best == 2 and _tier(title) < 2:
            self.last_note = "live takes only"
        return statistics.median(by_tier[best])
