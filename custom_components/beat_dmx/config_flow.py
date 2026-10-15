"""Config flow for Beat DMX."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (CONF_DEFAULT_BPM, CONF_FIXTURES, CONF_GETSONGBPM_KEY, CONF_MUSIC_FOLDER, DEFAULT_BPM,
                    DEFAULT_FIXTURES, DOMAIN)


def _schema(conf: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_FIXTURES, default=conf.get(CONF_FIXTURES, DEFAULT_FIXTURES)): str,
        vol.Optional(CONF_GETSONGBPM_KEY, default=conf.get(CONF_GETSONGBPM_KEY, "")): str,
        vol.Required(CONF_DEFAULT_BPM, default=conf.get(CONF_DEFAULT_BPM, DEFAULT_BPM)):
            vol.All(vol.Coerce(int), vol.Range(min=40, max=220)),
        vol.Optional(CONF_MUSIC_FOLDER, default=conf.get(CONF_MUSIC_FOLDER, "")): str,
    })


class BeatDmxConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries("music_assistant"):
            return self.async_abort(reason="no_music_assistant")
        if user_input is not None:
            return self.async_create_entry(title="Beat DMX", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return BeatDmxOptionsFlow()


class BeatDmxOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_schema({**self.config_entry.data, **self.config_entry.options}))
