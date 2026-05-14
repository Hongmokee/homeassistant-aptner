from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import AptnerApiClient, AptnerApiError
from .const import (
    CONF_PAGE_LIMIT,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_PAGE_LIMIT,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_PAGE_LIMIT,
    MIN_SCAN_INTERVAL_MINUTES,
)


class AptnerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Aptner config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow."""
        return AptnerOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = AptnerApiClient(
                session=session,
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )

            try:
                await client.async_initialize()
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except AptnerApiError:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()

                title = (
                    (client.user or {}).get("apartment", {}).get("name")
                    or user_input[CONF_USERNAME]
                )
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )


class AptnerOptionsFlow(config_entries.OptionsFlow):
    """Handle Aptner options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL_MINUTES: int(
                        user_input[CONF_SCAN_INTERVAL_MINUTES]
                    ),
                    CONF_PAGE_LIMIT: int(user_input[CONF_PAGE_LIMIT]),
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL_MINUTES,
                        default=self._config_entry.options.get(
                            CONF_SCAN_INTERVAL_MINUTES,
                            DEFAULT_SCAN_INTERVAL_MINUTES,
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=MAX_SCAN_INTERVAL_MINUTES,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Required(
                        CONF_PAGE_LIMIT,
                        default=self._config_entry.options.get(
                            CONF_PAGE_LIMIT,
                            DEFAULT_PAGE_LIMIT,
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_PAGE_LIMIT,
                            max=MAX_PAGE_LIMIT,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        ),
                    ),
                }
            ),
        )
