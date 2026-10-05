"""Writable target-charge-time control for EV iCharge vehicles.

This is the one entity in the integration that writes back to Origin
(everything else is read-only). Confirmed working via a real PATCH:

    PATCH /ev-icharge/api/v2/vehicles/{vehicleId}/charge-schedule/{scheduleId}
    {"vehicleId": "...", "scheduleId": "...", "targetChargeTime": "HH:MM:SS"}
    -> 204 No Content
"""

from __future__ import annotations

import logging
from datetime import time as time_

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OriginEnergyApiClient, OriginEnergyApiError
from .const import CONF_ORIGIN_ACCOUNT_ID, DOMAIN
from .entity import OriginEnergyVehicleEntity, async_add_vehicle_entities, dig

_LOGGER = logging.getLogger(__name__)

TARGET_CHARGE_TIME_DESCRIPTION = TimeEntityDescription(
    key="target_charge_time",
    translation_key="target_charge_time",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    origin_account_id = entry.data[CONF_ORIGIN_ACCOUNT_ID]

    def _make_vehicle_time_entities(
        vehicle_id: str,
    ) -> list[OriginEnergyTargetChargeTime]:
        return [
            OriginEnergyTargetChargeTime(
                coordinator,
                runtime.client,
                origin_account_id,
                vehicle_id,
                TARGET_CHARGE_TIME_DESCRIPTION,
            )
        ]

    async_add_vehicle_entities(
        coordinator, async_add_entities, _make_vehicle_time_entities
    )


class OriginEnergyTargetChargeTime(OriginEnergyVehicleEntity, TimeEntity):
    entity_description: TimeEntityDescription

    def __init__(
        self,
        coordinator,
        client: OriginEnergyApiClient,
        origin_account_id: str,
        vehicle_id: str,
        description: TimeEntityDescription,
    ) -> None:
        super().__init__(coordinator, origin_account_id, vehicle_id, description)
        self._client = client

    @property
    def native_value(self) -> time_ | None:
        raw = dig(self._vehicle_data, "activeSchedule", "targetChargeTime")
        if not raw:
            return None
        try:
            return time_.fromisoformat(raw)
        except ValueError:
            _LOGGER.debug("Unparseable targetChargeTime %r", raw)
            return None

    async def async_set_value(self, value: time_) -> None:
        schedule_id = dig(self._vehicle_data, "activeSchedule", "scheduleId")
        if not schedule_id:
            raise OriginEnergyApiError(
                f"No active schedule found for vehicle {self._vehicle_id}; "
                "cannot set a target charge time."
            )
        await self._client.async_set_ev_target_charge_time(
            self._vehicle_id, schedule_id, value.strftime("%H:%M:%S")
        )
        # Origin returns 204 with no updated state - refresh so the new
        # value (and targetCanBeAchieved, which can change as a result)
        # shows up promptly rather than waiting for the next poll.
        await self.coordinator.async_request_refresh()
