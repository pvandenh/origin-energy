"""Binary sensors for the Origin Energy (Kraken) integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_ORIGIN_ACCOUNT_ID, DOMAIN
from .entity import (
    OriginEnergyEntity,
    OriginEnergyVehicleEntity,
    async_add_vehicle_entities,
    dig,
)


@dataclass(frozen=True, kw_only=True)
class OriginEnergyBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None] = lambda data: None


@dataclass(frozen=True, kw_only=True)
class OriginEnergyVehicleBinarySensorDescription(BinarySensorEntityDescription):
    """value_fn takes just this one vehicle's dict."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda vehicle: None


BINARY_SENSOR_DESCRIPTIONS: tuple[OriginEnergyBinarySensorDescription, ...] = (
    OriginEnergyBinarySensorDescription(
        key="is_smart_meter",
        translation_key="is_smart_meter",
        value_fn=lambda d: dig(d, "account_info", "isSmartMeter"),
    ),
    OriginEnergyBinarySensorDescription(
        key="account_closed",
        translation_key="account_closed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        value_fn=lambda d: dig(
            d, "billing_details", "accountDetails", "isClosed"
        ),
    ),
    OriginEnergyBinarySensorDescription(
        key="bill_held",
        translation_key="bill_held",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: dig(
            d, "billing_details", "accountDetails", "hasHeldBill"
        ),
    ),
    OriginEnergyBinarySensorDescription(
        key="predicted_bill_error",
        translation_key="predicted_bill_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: dig(d, "account_info", "predictedBillError"),
    ),
    OriginEnergyBinarySensorDescription(
        key="usage_costs_missing",
        translation_key="usage_costs_missing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        # Explains why daily_usage_cost/daily_supply_cost/daily_earnings
        # sensors can be "unknown" even with real kWh figures present -
        # Origin's own cost reconciliation hasn't caught up yet, this
        # isn't a fetch failure.
        value_fn=lambda d: dig(d, "daily_usage", "has_missing_costs"),
    ),
)

VEHICLE_BINARY_SENSOR_DESCRIPTIONS: tuple[
    OriginEnergyVehicleBinarySensorDescription, ...
] = (
    OriginEnergyVehicleBinarySensorDescription(
        key="at_charge_location",
        translation_key="at_charge_location",
        value_fn=lambda v: dig(v, "atChargeLocation"),
    ),
    OriginEnergyVehicleBinarySensorDescription(
        key="solar_optimisation_enabled",
        translation_key="solar_optimisation_enabled",
        value_fn=lambda v: dig(v, "isSolarOptimisationEnabled"),
    ),
    OriginEnergyVehicleBinarySensorDescription(
        key="target_can_be_achieved",
        translation_key="target_can_be_achieved",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # PROBLEM semantics are inverted (on = problem), and "can the
        # target be met" is the opposite - False is the actual problem
        # state - so invert here rather than mislabel this as a plain
        # boolean sensor.
        value_fn=lambda v: (
            not dig(v, "activeSchedule", "targetCanBeAchieved")
            if dig(v, "activeSchedule", "targetCanBeAchieved") is not None
            else None
        ),
    ),
    OriginEnergyVehicleBinarySensorDescription(
        key="optimisation_pending",
        translation_key="optimisation_pending",
        entity_registry_enabled_default=False,
        value_fn=lambda v: dig(v, "optimisationPending"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    origin_account_id = entry.data[CONF_ORIGIN_ACCOUNT_ID]

    async_add_entities(
        OriginEnergyBinarySensor(
            coordinator, entry.entry_id, origin_account_id, description
        )
        for description in BINARY_SENSOR_DESCRIPTIONS
    )

    def _make_vehicle_binary_sensors(
        vehicle_id: str,
    ) -> list[OriginEnergyVehicleBinarySensor]:
        return [
            OriginEnergyVehicleBinarySensor(coordinator, origin_account_id, vehicle_id, d)
            for d in VEHICLE_BINARY_SENSOR_DESCRIPTIONS
        ]

    async_add_vehicle_entities(
        coordinator, async_add_entities, _make_vehicle_binary_sensors
    )


class OriginEnergyBinarySensor(OriginEnergyEntity, BinarySensorEntity):
    entity_description: OriginEnergyBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)


class OriginEnergyVehicleBinarySensor(OriginEnergyVehicleEntity, BinarySensorEntity):
    entity_description: OriginEnergyVehicleBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        vehicle = self._vehicle_data
        return self.entity_description.value_fn(vehicle) if vehicle else None
