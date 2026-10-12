"""Integracja eSzkoła24 – plan lekcji, prace domowe i oceny."""

from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .api import ESzkolaClient
from .const import CONF_HOST, DEFAULT_HOST
from .coordinator import ESzkolaCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

type ESzkolaConfigEntry = ConfigEntry[ESzkolaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ESzkolaConfigEntry) -> bool:
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = ESzkolaClient(
        session,
        entry.data.get(CONF_HOST, DEFAULT_HOST),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        dt_util.get_default_time_zone(),
    )
    coordinator = ESzkolaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ESzkolaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ESzkolaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
