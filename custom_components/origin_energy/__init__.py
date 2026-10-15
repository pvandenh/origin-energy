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
    ACCOUNT_SCAN_INTERVAL_HOURS,
    CONF_AUTH0_COOKIE,
    CONF_ORIGIN_ACCOUNT_ID,
    DOMAIN,
    EV_SCAN_INTERVAL_MINUTES,
)
from .entity import dig

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor", "time", "switch"]


@dataclass
class OriginEnergyRuntimeData:
    """What each platform needs: the coordinators and the shared API client.

    Two coordinators on purpose, polling at different rates:
      - coordinator:    account, billing and daily usage (slow - every few hours)
      - ev_coordinator: EV iCharge vehicles (fast - every few minutes)

    Vehicle entities (and the writable time/switch entities) hang off
    ev_coordinator, so a time/switch write only triggers a cheap
    vehicles-only refresh rather than re-fetching all the billing data.

    The client is needed directly (not just the coordinators) by anything
    that writes - e.g. time.py setting a vehicle's target charge time -
    since writes go straight to the API rather than through the
    coordinators' read-only polling loops.
    """

    coordinator: DataUpdateCoordinator
    ev_coordinator: DataUpdateCoordinator
    client: OriginEnergyApiClient


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    auth = OriginEnergyAuth(session, entry.data[CONF_AUTH0_COOKIE])
    client = OriginEnergyApiClient(
        session, auth, entry.data[CONF_ORIGIN_ACCOUNT_ID]
    )

    def _persist_rotated_cookie() -> None:
        # The auth0 cookie can get rotated server-side during a refresh
        # (see auth.py). If that happened, persist the new value into the
        # config entry so a HA restart doesn't go back to using the
        # now-superseded one. Both coordinators share one auth object, so
        # whichever refresh triggers the rotation saves it.
        if auth.auth0_cookie != entry.data.get(CONF_AUTH0_COOKIE):
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_AUTH0_COOKIE: auth.auth0_cookie},
            )

    async def _async_update_account_data() -> dict:
        try:
            data = {
                "account_info": await client.async_get_account_info(),
                "billing_details": await client.async_get_billing_details(),
                "upcoming_bill": await client.async_get_upcoming_bill(),
                "transactions": await client.async_get_transactions(),
            }

            # Usage is kept separate from the block above: it needs an
            # agreementId pulled out of billing_details, which may itself
            # be absent/differently shaped for some accounts, and a hiccup
            # here shouldn't take down the whole coordinator - account and
            # billing data matters more and shouldn't go unavailable over
            # a usage-specific issue.
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

            _persist_rotated_cookie()
            return data
        except OriginSessionExpired as err:
            # Surfaces as a "Reauthenticate" repair in the HA UI, which
            # routes to config_flow.async_step_reauth_confirm.
            raise ConfigEntryAuthFailed(str(err)) from err
        except OriginEnergyApiError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_update_ev_data() -> dict:
        # EV iCharge is a different subsystem entirely (see api.py), and
        # not every account will have it set up - which is why it has its
        # own coordinator: a failure here only affects vehicle entities,
        # never account/billing data.
        try:
            vehicles = await client.async_get_ev_vehicles()
            _persist_rotated_cookie()
            return {"ev_vehicles": {v["vehicleId"]: v for v in vehicles}}
        except OriginSessionExpired as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OriginEnergyApiError as err:
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=f"{DOMAIN}_account",
        update_method=_async_update_account_data,
        update_interval=timedelta(hours=ACCOUNT_SCAN_INTERVAL_HOURS),
    )
    ev_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=f"{DOMAIN}_ev",
        update_method=_async_update_ev_data,
        update_interval=timedelta(minutes=EV_SCAN_INTERVAL_MINUTES),
    )

    await coordinator.async_config_entry_first_refresh()
    # Deliberately NOT async_config_entry_first_refresh: an account with no
    # EV iCharge set up (or an EV-side outage) must not stop the integration
    # from loading. If this first fetch fails, vehicle entities simply start
    # unavailable/absent, and appear once a later poll succeeds.
    await ev_coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = OriginEnergyRuntimeData(
        coordinator=coordinator, ev_coordinator=ev_coordinator, client=client
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
