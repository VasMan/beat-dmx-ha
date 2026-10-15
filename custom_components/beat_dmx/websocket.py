"""Live DMX values for the dashboard card, pushed over the websocket (never through the state machine)."""
from __future__ import annotations

import asyncio

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

PUSH_SECONDS = 0.1


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)


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
            if data != last:
                connection.send_message(websocket_api.event_message(msg["id"], data))
                last = data
            await asyncio.sleep(PUSH_SECONDS)

    task = hass.async_create_background_task(_push(), f"{DOMAIN} live push")
    connection.subscriptions[msg["id"]] = task.cancel
    connection.send_result(msg["id"])
