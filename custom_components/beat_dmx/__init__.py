"""Beat DMX: drives Art-Net fixtures to the tempo of whatever Music Assistant is playing."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import services, websocket
from .const import (CONF_DEFAULT_BPM, CONF_FIXTURES, CONF_GETSONGBPM_KEY, DEFAULT_BPM, DEFAULT_FIXTURES,
                    DOMAIN)
from .engine import BeatEngine

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = f"/{DOMAIN}/beat-dmx-card.js"
CARD_VERSION = 5

PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]

BeatConfigEntry = ConfigEntry[BeatEngine]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The live-values websocket and the dashboard card (custom:beat-dmx-card) are served by the integration."""
    websocket.async_register(hass)
    services.async_register(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(Path(__file__).parent / "www" / "beat-dmx-card.js"), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={CARD_VERSION}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BeatConfigEntry) -> bool:
    conf = {**entry.data, **entry.options}
    engine = BeatEngine(
        hass,
        fixtures=str(conf.get(CONF_FIXTURES, DEFAULT_FIXTURES)).split(","),
        getsongbpm_key=conf.get(CONF_GETSONGBPM_KEY, "").strip(),
        default_bpm=float(conf.get(CONF_DEFAULT_BPM, DEFAULT_BPM)))
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
