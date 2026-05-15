from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AptnerApiClient
from .const import (
    CONF_PAGE_LIMIT,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    PLATFORMS,
    enabled_sections_from_config,
)
from .coordinator import AptnerDataUpdateCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Aptner integration."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["logger"] = _LOGGER
    hass.data[DOMAIN].setdefault("entries", {})
    hass.data[DOMAIN].setdefault("services_registered", False)
    await async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aptner from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault("logger", _LOGGER)
    domain_data.setdefault("entries", {})

    session = async_get_clientsession(hass)
    client = AptnerApiClient(
        session=session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        enabled_sections=enabled_sections_from_config(entry.data, entry.options),
        page_limit=entry.options.get(CONF_PAGE_LIMIT, DEFAULT_PAGE_LIMIT),
    )
    coordinator = AptnerDataUpdateCoordinator(
        hass,
        client,
        update_interval=timedelta(
            minutes=entry.options.get(
                CONF_SCAN_INTERVAL_MINUTES,
                DEFAULT_SCAN_INTERVAL_MINUTES,
            )
        ),
    )
    await coordinator.async_config_entry_first_refresh()

    domain_data["entries"][entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload an Aptner config entry after updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Aptner config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).get("entries", {}).pop(entry.entry_id, None)
        await async_unregister_services(hass)
    return unload_ok
