from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACTED = "**REDACTED**"

REDACT_KEYS = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "accessToken",
    "refreshToken",
    "Authorization",
    "phone",
    "visitor_phone",
    "car_no",
    "address",
    "profileUrl",
    "name",
    "nick",
    "writer",
    "mbId",
    "mbIdx",
    "userIdx",
    "id",
    "tel",
}


def _dashboard_error_keys(data: object) -> list[str]:
    """Return top-level dashboard keys that currently contain API errors."""
    if not isinstance(data, dict):
        return []
    return sorted(
        key
        for key, value in data.items()
        if isinstance(key, str)
        and isinstance(value, dict)
        and isinstance(value.get("_error"), str)
    )


def _dashboard_data_keys(data: object) -> list[str]:
    """Return top-level dashboard keys without exposing payload values."""
    if not isinstance(data, dict):
        return []
    return sorted(str(key) for key in data)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    data = coordinator.data
    return async_redact_data(
        {
            "entry": {
                "entry_id": entry.entry_id,
                "title": REDACTED,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "data_keys": _dashboard_data_keys(data),
                "error_keys": _dashboard_error_keys(data),
            },
        },
        REDACT_KEYS,
    )
