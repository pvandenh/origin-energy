"""Instant Charge control for EV iCharge vehicles.

Confirmed against a real start/stop cycle:

    POST   /ev-icharge/api/v2/vehicles/{vehicleId}/charge-override/active   -> 204
    DELETE /ev-icharge/api/v2/vehicles/{vehicleId}/charge-override/active   -> 204

Both empty-body. This bypasses the scheduled/cheapest-time charging plan
and charges immediately at full rate - Origin's own confirmation dialog
warns the EV Power Up rate won't apply and no credits are earned while
it's active, which is worth knowing before automating this.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OriginEnergyApiClient
from .const import CONF_ORIGIN_ACCOUNT_ID, DOMAIN
from .entity import (
    OriginEnergyVehicleEntity,
    async_add_vehicle_entities,
    dig,
    normalize_charge_status,
)

INSTANT_CHARGE_DESCRIPTION = SwitchEntityDescription(
    key="instant_charge",
    translation_key="instant_charge",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    origin_account_id = entry.data[CONF_ORIGIN_ACCOUNT_ID]

    def _make_vehicle_switches(vehicle_id: str) -> list[OriginEnergyInstantCharge]:
        return [
            OriginEnergyInstantCharge(
                coordinator,
                runtime.client,
                origin_account_id,
                vehicle_id,
                INSTANT_CHARGE_DESCRIPTION,
            )
        ]

    async_add_vehicle_entities(coordinator, async_add_entities, _make_vehicle_switches)


class OriginEnergyInstantCharge(OriginEnergyVehicleEntity, SwitchEntity):
    entity_description: SwitchEntityDescription

    def __init__(
        self,
        coordinator,
        client: OriginEnergyApiClient,
        origin_account_id: str,
        vehicle_id: str,
        description: SwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, origin_account_id, vehicle_id, description)
        self._client = client

    @property
    def is_on(self) -> bool | None:
        return dig(self._vehicle_data, "chargeOverride")

    @property
    def available(self) -> bool:
        # Instant Charge bypasses the scheduled/cheapest-time plan to
        # charge right now - meaningless while nothing's plugged in or
        # the charge is already complete, and not safe to assume
        # available when the status itself is unknown/unavailable - so
        # mark unavailable rather than letting it be toggled with no
        # effect (or an effect we can't predict).
        if not super().available:
            return False
        status = normalize_charge_status(dig(self._vehicle_data, "chargeState", "status"))
        return status not in (None, "unplugged", "complete")

    async def async_turn_on(self, **kwargs) -> None:
        await self._client.async_start_ev_instant_charge(self._vehicle_id)
        # No body comes back on success - refresh so is_on (and the
        # charge_status/charge target sensors, which can shift once
        # instant charge kicks in) reflect it promptly.
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._client.async_stop_ev_instant_charge(self._vehicle_id)
        await self.coordinator.async_request_refresh()
