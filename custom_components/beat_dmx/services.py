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
    vol.Required("point"): vol.In(["up", "level", "reverse_pan", "reset"]),
})


def _engine(hass: HomeAssistant):
    entry = next(iter(hass.config_entries.async_loaded_entries(DOMAIN)), None)
    if entry is None:
        raise HomeAssistantError("Beat DMX is not loaded")
    return entry.runtime_data


@callback
def async_register(hass: HomeAssistant) -> None:
    def _call(fn):
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
