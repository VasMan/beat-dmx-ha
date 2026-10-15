"""beat_dmx.set_channel and beat_dmx.calibrate (used by the live card; usable from scripts too)."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import CHANNEL_COUNT, DOMAIN

SET_CHANNEL = vol.Schema({
    vol.Required("fixture"): cv.string,
    vol.Required("channel"): vol.All(vol.Coerce(int), vol.Range(min=1, max=CHANNEL_COUNT)),
    vol.Required("value"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
})
CALIBRATE = vol.Schema({
    vol.Required("fixture"): cv.string,
    vol.Required("point"): vol.In(["up", "level", "reverse_pan", "reset", "go_up", "go_level", "go_left"]),
})


def _engine(hass: HomeAssistant):
    entry = next(iter(hass.config_entries.async_loaded_entries(DOMAIN)), None)
    if entry is None:
        raise HomeAssistantError("Beat DMX is not loaded")
    return entry.runtime_data


ANALYSE_FOLDER = vol.Schema({vol.Optional("folder"): cv.string, vol.Optional("full", default=False): cv.boolean})


@callback
def async_register(hass: HomeAssistant) -> None:
    def _call(fn):
        @callback  # (a plain function would be run in a worker thread; the engine lives in the event loop)
        def handler(call: ServiceCall) -> None:
            try:
                fn(_engine(hass), call.data)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
        return handler

    hass.services.async_register(DOMAIN, "set_channel", _call(
        lambda e, d: e.set_channel(d["fixture"], d["channel"], d["value"])), SET_CHANNEL)
    hass.services.async_register(DOMAIN, "calibrate", _call(
        lambda e, d: e.calibrate(d["fixture"], d["point"])), CALIBRATE)

    @callback
    def analyse(call: ServiceCall) -> None:
        engine = _engine(hass)
        folder = call.data.get("folder") or engine.music_folder
        if not folder:
            raise ServiceValidationError("No folder given and no music folder set in the Beat DMX options")
        if engine.scan["running"]:
            raise ServiceValidationError("An analysis is already running")
        hass.async_create_background_task(engine.analyse_folder(folder, call.data["full"]), f"{DOMAIN} analyse")

    hass.services.async_register(DOMAIN, "analyse_folder", analyse, ANALYSE_FOLDER)
