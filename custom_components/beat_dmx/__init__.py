"""Beat DMX: drives Art-Net fixtures to the tempo of whatever Music Assistant is playing."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import services, websocket
from .const import (CONF_DEFAULT_BPM, CONF_FIXTURES, CONF_GETSONGBPM_KEY, CONF_MUSIC_FOLDER, DEFAULT_BPM,
                    DEFAULT_FIXTURES,
                    DOMAIN)
from .engine import BeatEngine

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
# the card is copied to <config>/www/beat_dmx/ and loaded as a Lovelace resource: both exist from the
# moment Home Assistant starts, so a dashboard opened during start-up still finds the card
CARD_URL = f"/local/{DOMAIN}/beat-dmx-card.js"
CARD_VERSION = 12

PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]

BeatConfigEntry = ConfigEntry[BeatEngine]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The live-values websocket, the services and the dashboard card (custom:beat-dmx-card)."""
    websocket.async_register(hass)
    services.async_register(hass)
    await hass.async_add_executor_job(_copy_card, hass.config.path("www", DOMAIN))
    try:
        await _register_card(hass, f"{CARD_URL}?v={CARD_VERSION}")
    except Exception as err:  # the card can still be added as a resource by hand
        _LOGGER.warning("Could not add the Beat DMX card as a Lovelace resource (%s); add %s (JavaScript module) "
                        "under Settings > Dashboards > Resources", err, CARD_URL)
    return True


def _copy_card(folder: str) -> None:
    source, target = Path(__file__).parent / "www" / "beat-dmx-card.js", Path(folder) / "beat-dmx-card.js"
    if not target.exists() or target.read_bytes() != source.read_bytes():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


async def _register_card(hass: HomeAssistant, url: str) -> None:
    """Add the card to the Lovelace resources, or point the existing entry at this version."""
    resources = hass.data["lovelace"].resources
    if not hasattr(resources, "async_create_item"):
        raise RuntimeError("Lovelace resources are in YAML mode")
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    mine = [r for r in resources.async_items() if r["url"].split("?")[0] == CARD_URL]
    if not mine:
        await resources.async_create_item({"res_type": "module", "url": url})
    elif mine[0]["url"] != url:
        await resources.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry) -> bool:
    conf = {**entry.data, **entry.options}
    engine = BeatEngine(
        hass,
        fixtures=str(conf.get(CONF_FIXTURES, DEFAULT_FIXTURES)).split(","),
        getsongbpm_key=conf.get(CONF_GETSONGBPM_KEY, "").strip(),
        default_bpm=float(conf.get(CONF_DEFAULT_BPM, DEFAULT_BPM)),
        music_folder=str(conf.get(CONF_MUSIC_FOLDER, "")))
    entry.runtime_data = engine
    await engine.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BeatConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _reload(hass: HomeAssistant, entry: BeatConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
