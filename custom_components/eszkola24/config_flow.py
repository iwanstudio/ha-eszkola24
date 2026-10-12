"""Konfiguracja integracji eSzkoła24 z poziomu interfejsu."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import ESzkolaAuthError, ESzkolaClient, ESzkolaError
from .const import (
    CONF_EXCLUDE,
    CONF_HOST,
    CONF_SCAN_HOURS,
    DEFAULT_EXCLUDE,
    DEFAULT_HOST,
    DEFAULT_SCAN_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> list[str]:
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    try:
        client = ESzkolaClient(
            session,
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            dt_util.get_default_time_zone(),
        )
        children = await client.async_login()
    finally:
        await session.close()
    return [c.name for c in children]


class ESzkolaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip().removeprefix("https://").strip("/")
            user_input[CONF_HOST] = host
            await self.async_set_unique_id(f"{host}|{user_input[CONF_USERNAME].lower()}")
            self._abort_if_unique_id_configured()
            try:
                children = await _validate(self.hass, user_input)
            except ESzkolaAuthError:
                errors["base"] = "invalid_auth"
            except ESzkolaError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Nieoczekiwany błąd przy logowaniu do eSzkoła24")
                errors["base"] = "unknown"
            else:
                if not children:
                    errors["base"] = "no_children"
                else:
                    return self.async_create_entry(
                        title=f"eSzkoła24 ({', '.join(n.split()[0] for n in children)})",
                        data=user_input,
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=(user_input or {}).get(CONF_HOST, DEFAULT_HOST)
                ): str,
                vol.Required(
                    CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")
                ): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _validate(self.hass, data)
            except ESzkolaAuthError:
                errors["base"] = "invalid_auth"
            except ESzkolaError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ESzkolaOptionsFlow()


class ESzkolaOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_EXCLUDE, default=opts.get(CONF_EXCLUDE, DEFAULT_EXCLUDE)
                ): str,
                vol.Optional(
                    CONF_SCAN_HOURS, default=opts.get(CONF_SCAN_HOURS, DEFAULT_SCAN_HOURS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
