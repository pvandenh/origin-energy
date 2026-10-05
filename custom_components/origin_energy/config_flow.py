"""Config flow for Origin Energy (Kraken).

There is no automatable login step (see auth.py for why), so setup asks
the user to paste the `auth0` session cookie value they copy out of their
browser's DevTools after logging in normally, plus their `originAccountId`
(the UUID visible in the portal's own API calls - NOT the human-readable
account number like A-XXXXXXX).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OriginEnergyApiClient, OriginEnergyApiError
from .auth import OriginAuthError, OriginEnergyAuth, OriginSessionExpired
from .const import CONF_AUTH0_COOKIE, CONF_ORIGIN_ACCOUNT_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AUTH0_COOKIE): str,
        vol.Required(CONF_ORIGIN_ACCOUNT_ID): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_AUTH0_COOKIE): str})


class OriginEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and reauth."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self._async_validate(user_input)
            except OriginSessionExpired:
                errors["base"] = "invalid_cookie"
            except (OriginAuthError, OriginEnergyApiError) as err:
                _LOGGER.debug("Origin Energy setup validation failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_ORIGIN_ACCOUNT_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Origin Energy", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            merged = {**reauth_entry.data, **user_input}
            try:
                await self._async_validate(merged)
            except OriginSessionExpired:
                errors["base"] = "invalid_cookie"
            except (OriginAuthError, OriginEnergyApiError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data=merged
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> None:
        """Confirm the cookie + account id actually work together.

        This deliberately makes one real, silent-auth-backed API call
        rather than just checking the cookie is non-empty, since a stale
        or copy-pasted-wrong cookie should fail here, not three seconds
        after the entry is created.
        """
        session = async_get_clientsession(self.hass)
        auth = OriginEnergyAuth(session, user_input[CONF_AUTH0_COOKIE])
        client = OriginEnergyApiClient(
            session, auth, user_input[CONF_ORIGIN_ACCOUNT_ID]
        )
        await client.async_get_account_info()
