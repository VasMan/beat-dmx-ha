"""Live DMX values for the dashboard card, pushed over the websocket (never through the state machine)."""
from __future__ import annotations

import asyncio

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

PUSH_SECONDS = 0.2  # (the card animates the beat itself; fewer frames for tablets on flaky links)


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_set_channels)
    websocket_api.async_register_command(hass, ws_timeline)


def _engine(hass: HomeAssistant):
    entry = next(iter(hass.config_entries.async_loaded_entries(DOMAIN)), None)
    return entry.runtime_data if entry else None


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@websocket_api.async_response
async def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    async def _push() -> None:
        last = None
        while True:
            engine = _engine(hass)
            data = engine.live() if engine else {"error": "Beat DMX is not loaded"}
            # (the beat and the track position move on all the time; that alone is no reason to send -
            # the card counts them on itself)
            same = _still(data) == _still(last or {})
            if not same:
                connection.send_message(websocket_api.event_message(msg["id"], data))
                last = data
            await asyncio.sleep(PUSH_SECONDS)

    task = hass.async_create_background_task(_push(), f"{DOMAIN} live push")
    connection.subscriptions[msg["id"]] = task.cancel
    connection.send_result(msg["id"])


def _still(data: dict) -> dict:
    sec = data.get("section")
    return {**data, "beat": None, "section": {**sec, "t": None, "next_in": None} if sec else sec}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/timeline"})
@callback
def ws_timeline(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """The playing track's energy curve and sections, for drawing (fetched when the track changes)."""
    engine = _engine(hass)
    connection.send_result(msg["id"], engine.analysis if engine else None)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/set_channels",
    vol.Required("fixture"): str,
    vol.Required("values"): {vol.Coerce(int): vol.All(vol.Coerce(int), vol.Range(min=0, max=255))},
})
@callback
def ws_set_channels(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Manual DMX from the card: several channels (1-11) of one fixture in one message."""
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Beat DMX is not loaded")
        return
    try:
        for channel, value in msg["values"].items():
            engine.set_channel(msg["fixture"], channel, value)
    except (ValueError, IndexError) as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"])
