"""The Origin Energy (Kraken) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OriginEnergyApiClient, OriginEnergyApiError
from .auth import OriginEnergyAuth, OriginSessionExpired
from .const import (
    CONF_AUTH0_COOKIE,
    CONF_ORIGIN_ACCOUNT_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .entity import dig

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor", "time", "switch"]


@dataclass
class OriginEnergyRuntimeData:
    """What each platform needs: the shared coordinator and API client.

    The client is needed directly (not just the coordinator) by anything
    that writes - e.g. time.py setting a vehicle's target charge time -
    since writes go straight to the API rather than through the
    coordinator's read-only polling loop.
    """

    coordinator: DataUpdateCoordinator
    client: OriginEnergyApiClient


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    auth = OriginEnergyAuth(session, entry.data[CONF_AUTH0_COOKIE])
    client = OriginEnergyApiClient(
        session, auth, entry.data[CONF_ORIGIN_ACCOUNT_ID]
    )

    async def _async_update_data() -> dict:
        try:
            data = {
                "account_info": await client.async_get_account_info(),
                "billing_details": await client.async_get_billing_details(),
                "upcoming_bill": await client.async_get_upcoming_bill(),
                "transactions": await client.async_get_transactions(),
            }

            # Kept separate from the block above: EV iCharge is a
            # different subsystem entirely (see api.py), and not every
            # account will have it set up. A hiccup here shouldn't take
            # down the whole coordinator - account/billing data matters
            # more and shouldn't go unavailable over an EV-specific issue.
            try:
                ev_vehicles = await client.async_get_ev_vehicles()
                data["ev_vehicles"] = {v["vehicleId"]: v for v in ev_vehicles}
            except OriginEnergyApiError as err:
                _LOGGER.debug("EV iCharge fetch failed, skipping this round: %s", err)
                data["ev_vehicles"] = coordinator.data.get("ev_vehicles", {}) if coordinator.data else {}

            # Same "don't take down the whole coordinator" reasoning as
            # EV iCharge above: usage needs an agreementId pulled out of
            # billing_details, which may itself be absent/differently
            # shaped for some accounts.
            try:
                agreement_id = dig(
                    data,
                    "billing_details",
                    "accountDetails",
                    "services",
                    0,
                    "activeAgreement",
                    "id",
                )
                if agreement_id:
                    today_start = dt_util.start_of_local_day()
                    today_end = today_start.replace(
                        hour=23, minute=59, second=59, microsecond=0
                    )
                    data["daily_usage"] = await client.async_get_daily_usage(
                        agreement_id, today_start, today_end
                    )
                else:
                    data["daily_usage"] = None
            except OriginEnergyApiError as err:
                _LOGGER.debug("Usage fetch failed, skipping this round: %s", err)
                data["daily_usage"] = (
                    coordinator.data.get("daily_usage") if coordinator.data else None
                )

            # The auth0 cookie can get rotated server-side during a
            # refresh (see auth.py). If that happened, persist the new
            # value into the config entry so a HA restart doesn't go
            # back to using the now-superseded one.
            if auth.auth0_cookie != entry.data.get(CONF_AUTH0_COOKIE):
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_AUTH0_COOKIE: auth.auth0_cookie},
                )
            return data
        except OriginSessionExpired as err:
            # Surfaces as a "Reauthenticate" repair in the HA UI, which
            # routes to config_flow.async_step_reauth_confirm.
            raise ConfigEntryAuthFailed(str(err)) from err
        except OriginEnergyApiError as err:
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=_async_update_data,
        update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = OriginEnergyRuntimeData(
        coordinator=coordinator, client=client
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
