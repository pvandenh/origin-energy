"""Sensors for the Origin Energy (Kraken) integration.

Every field here has actually been observed in a captured API response.
`daily_usage` sensors come from a GraphQL query and are unit-converted
to kWh/AUD regardless of what unit Origin reports internally (see
api.py's _convert) - so these are always kWh/AUD, never Wh/cents.

usage_cost_aud/supply_cost_aud/earnings_aud can legitimately show
"unknown" even once consumed_kwh/feed_in_kwh have real numbers - Origin
itself flags this via has_missing_costs when its own cost reconciliation
hasn't caught up yet (observed for several days on a brand-new account).
That's Origin's own data lag, not a bug in this integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import BALANCE_RAW_POSITIVE_IS_CREDIT, CONF_ORIGIN_ACCOUNT_ID, DOMAIN
from .entity import (
    OriginEnergyEntity,
    OriginEnergyVehicleEntity,
    async_add_vehicle_entities,
    dig,
    normalize_charge_status,
)


@dataclass(frozen=True, kw_only=True)
class OriginEnergySensorDescription(SensorEntityDescription):
    """Adds a value_fn that pulls this sensor's value out of coordinator data."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda data: None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _cents_to_aud(raw: Any) -> float | None:
    """Origin's billing figures are reported in CENTS (1457 == $14.57)."""
    if raw is None:
        return None
    try:
        return round(float(raw) / 100, 2)
    except (TypeError, ValueError):
        return None


def _balance_to_aud(raw: Any) -> float | None:
    """`balance` (cents) -> AUD, credit positive / owing negative.

    Confirmed from a captured account-billing-details response: balance,
    eligibleBalanceForRefund and eligibleBalanceForTransfer were all 1457
    while the portal said "$14.57 in credit". So a positive raw value is
    a credit, and the API has no separate credit/debit flag - the sign IS
    the indicator.
    """
    dollars = _cents_to_aud(raw)
    if dollars is None:
        return None
    return dollars if BALANCE_RAW_POSITIVE_IS_CREDIT else -dollars


def _raw_balance(d: dict[str, Any]) -> Any:
    """Prefer billing-details' balance (the field confirmed in a capture,
    alongside eligibleBalanceForRefund), falling back to account-info's."""
    value = dig(d, "billing_details", "balance")
    return value if value is not None else dig(d, "account_info", "balance")


def _balance_status(raw: Any) -> str | None:
    """Plain-English credit/owing/settled state, independent of sign display."""
    dollars = _cents_to_aud(raw)
    if dollars is None:
        return None
    if dollars == 0:
        return "settled"
    return "credit" if dollars > 0 else "owing"


SENSOR_DESCRIPTIONS: tuple[OriginEnergySensorDescription, ...] = (
    # -- account_info --------------------------------------------------
    OriginEnergySensorDescription(
        key="balance",
        translation_key="balance",
        device_class=SensorDeviceClass.MONETARY,
        # Currency is not returned by the API - inferred from Origin being
        # an AU-only retailer. Value is converted from cents and signed so
        # that positive = in credit, negative = amount owing (see
        # _balance_to_aud).
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: _balance_to_aud(_raw_balance(d)),
    ),
    OriginEnergySensorDescription(
        key="balance_status",
        translation_key="balance_status",
        device_class=SensorDeviceClass.ENUM,
        options=["credit", "owing", "settled"],
        value_fn=lambda d: _balance_status(_raw_balance(d)),
    ),
    OriginEnergySensorDescription(
        key="bill_period_start",
        translation_key="bill_period_start",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: _parse_date(
            dig(d, "account_info", "billPeriodStartDate")
        ),
    ),
    OriginEnergySensorDescription(
        key="bill_period_end",
        translation_key="bill_period_end",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: _parse_date(dig(d, "account_info", "billPeriodEndDate")),
    ),
    OriginEnergySensorDescription(
        key="cost_to_date",
        translation_key="cost_to_date",
        device_class=SensorDeviceClass.MONETARY,
        # account-info returns these already in dollars (unlike the
        # GraphQL usage endpoint, which is explicit about CENT/WATT_HOUR
        # and needs conversion) - no unitsOfMeasure field here to confirm
        # against, inferred from the numbers being sensible as dollars.
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: dig(d, "account_info", "costToDate"),
    ),
    OriginEnergySensorDescription(
        key="cost_per_day",
        translation_key="cost_per_day",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: dig(d, "account_info", "costPerDay"),
    ),
    OriginEnergySensorDescription(
        key="predicted_bill",
        translation_key="predicted_bill",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: dig(d, "account_info", "predictedBill"),
    ),
    # -- billing_details: account -----------------------------------
    OriginEnergySensorDescription(
        key="account_number",
        translation_key="account_number",
        entity_registry_enabled_default=True,
        value_fn=lambda d: dig(
            d, "billing_details", "accountDetails", "accountNumber"
        ),
    ),
    OriginEnergySensorDescription(
        key="account_state",
        translation_key="account_state",
        value_fn=lambda d: dig(d, "billing_details", "accountDetails", "accountState"),
    ),
    OriginEnergySensorDescription(
        key="move_in_date",
        translation_key="move_in_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: _parse_date(
            dig(d, "billing_details", "accountDetails", "moveInDate")
        ),
    ),
    OriginEnergySensorDescription(
        key="active_product_code",
        translation_key="active_product_code",
        entity_registry_enabled_default=False,  # diagnostic / rarely needed
        value_fn=lambda d: dig(
            d,
            "billing_details",
            "accountDetails",
            "services",
            0,
            "activeAgreement",
            "productCode",
        ),
    ),
    OriginEnergySensorDescription(
        key="active_agreement_valid_to",
        translation_key="active_agreement_valid_to",
        device_class=SensorDeviceClass.DATE,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _parse_date(
            dig(
                d,
                "billing_details",
                "accountDetails",
                "services",
                0,
                "activeAgreement",
                "validTo",
            )
        ),
    ),
    # -- billing_details: balances ------------------------------------
    OriginEnergySensorDescription(
        key="eligible_balance_for_refund",
        translation_key="eligible_balance_for_refund",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _cents_to_aud(
            dig(d, "billing_details", "eligibleBalanceForRefund")
        ),
    ),
    OriginEnergySensorDescription(
        key="eligible_balance_for_transfer",
        translation_key="eligible_balance_for_transfer",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _cents_to_aud(
            dig(d, "billing_details", "eligibleBalanceForTransfer")
        ),
    ),
    # -- billing_details: payment schedule ------------------------------
    OriginEnergySensorDescription(
        key="payment_amount",
        translation_key="payment_amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: dig(
            d, "billing_details", "paymentSchedule", "paymentAmount"
        ),
    ),
    OriginEnergySensorDescription(
        key="payment_frequency",
        translation_key="payment_frequency",
        entity_registry_enabled_default=False,
        value_fn=lambda d: dig(
            d, "billing_details", "paymentSchedule", "paymentFrequency"
        ),
    ),
    # -- billing_details: due bills - empty on this account so far ------
    OriginEnergySensorDescription(
        key="due_bills_count",
        translation_key="due_bills_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(dig(d, "billing_details", "dueBills", default=[]) or []),
    ),
    # -- upcoming_bill ---------------------------------------------------
    OriginEnergySensorDescription(
        key="upcoming_bill_period_end",
        translation_key="upcoming_bill_period_end",
        device_class=SensorDeviceClass.DATE,
        entity_registry_enabled_default=False,  # duplicates account_info's for now
        value_fn=lambda d: _parse_date(
            dig(d, "upcoming_bill", "billPeriodEndDate")
        ),
    ),
    # -- transactions -----------------------------------------------------
    OriginEnergySensorDescription(
        key="transaction_count",
        translation_key="transaction_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: len(dig(d, "transactions", default=[]) or []),
    ),
    # -- daily_usage (GraphQL, unit-converted to kWh/AUD - see api.py) ----
    OriginEnergySensorDescription(
        key="daily_consumed",
        translation_key="daily_consumed",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement="kWh",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: dig(d, "daily_usage", "consumed_kwh"),
    ),
    OriginEnergySensorDescription(
        key="daily_feed_in",
        translation_key="daily_feed_in",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement="kWh",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: dig(d, "daily_usage", "feed_in_kwh"),
    ),
    OriginEnergySensorDescription(
        key="daily_total_cost",
        translation_key="daily_total_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: dig(d, "daily_usage", "total_cost_aud"),
    ),
    OriginEnergySensorDescription(
        key="daily_usage_cost",
        translation_key="daily_usage_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=False,  # often null - see docstring
        value_fn=lambda d: dig(d, "daily_usage", "usage_cost_aud"),
    ),
    OriginEnergySensorDescription(
        key="daily_supply_cost",
        translation_key="daily_supply_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=False,
        value_fn=lambda d: dig(d, "daily_usage", "supply_cost_aud"),
    ),
    OriginEnergySensorDescription(
        key="daily_earnings",
        translation_key="daily_earnings",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=False,  # often null - see docstring
        value_fn=lambda d: dig(d, "daily_usage", "earnings_aud"),
    ),
)


@dataclass(frozen=True, kw_only=True)
class OriginEnergyVehicleSensorDescription(SensorEntityDescription):
    """Same idea as OriginEnergySensorDescription, but value_fn takes just
    this one vehicle's dict (not the whole coordinator.data)."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda vehicle: None


VEHICLE_SENSOR_DESCRIPTIONS: tuple[OriginEnergyVehicleSensorDescription, ...] = (
    OriginEnergyVehicleSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v: dig(v, "chargeState", "soc"),
    ),
    OriginEnergyVehicleSensorDescription(
        key="vehicle_range",
        translation_key="vehicle_range",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement="km",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v: dig(v, "chargeState", "vehicleRange"),
    ),
    OriginEnergyVehicleSensorDescription(
        key="charge_status",
        translation_key="charge_status",
        device_class=SensorDeviceClass.ENUM,
        options=["unplugged", "charging", "paused", "complete"],
        # Raw API values (e.g. "plugged_in_stopped") aren't friendly HA
        # states - normalize_charge_status collapses them to one of the
        # three options above, which strings.json then translates to
        # "Unplugged" / "Charging" / "Paused" for display.
        value_fn=lambda v: normalize_charge_status(dig(v, "chargeState", "status")),
    ),
    OriginEnergyVehicleSensorDescription(
        key="account_status",
        translation_key="ev_account_status",
        entity_registry_enabled_default=False,
        value_fn=lambda v: dig(v, "accountStatus"),
    ),
    OriginEnergyVehicleSensorDescription(
        key="target_battery_level",
        translation_key="target_battery_level",
        native_unit_of_measurement="%",
        entity_registry_enabled_default=False,
        value_fn=lambda v: dig(v, "activeSchedule", "targetBatteryLevel"),
    ),
    OriginEnergyVehicleSensorDescription(
        key="minimum_charge_limit",
        translation_key="minimum_charge_limit",
        native_unit_of_measurement="%",
        entity_registry_enabled_default=False,
        value_fn=lambda v: dig(v, "chargeState", "minimumChargeLimit"),
    ),
    OriginEnergyVehicleSensorDescription(
        key="maximum_charge_limit",
        translation_key="maximum_charge_limit",
        native_unit_of_measurement="%",
        entity_registry_enabled_default=False,
        value_fn=lambda v: dig(v, "chargeState", "maximumChargeLimit"),
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
        OriginEnergySensor(coordinator, entry.entry_id, origin_account_id, description)
        for description in SENSOR_DESCRIPTIONS
    )

    def _make_vehicle_sensors(vehicle_id: str) -> list[OriginEnergyVehicleSensor]:
        return [
            OriginEnergyVehicleSensor(coordinator, origin_account_id, vehicle_id, d)
            for d in VEHICLE_SENSOR_DESCRIPTIONS
        ]

    async_add_vehicle_entities(coordinator, async_add_entities, _make_vehicle_sensors)


class OriginEnergySensor(OriginEnergyEntity, SensorEntity):
    entity_description: OriginEnergySensorDescription

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)


class OriginEnergyVehicleSensor(OriginEnergyVehicleEntity, SensorEntity):
    entity_description: OriginEnergyVehicleSensorDescription

    @property
    def native_value(self) -> Any:
        vehicle = self._vehicle_data
        return self.entity_description.value_fn(vehicle) if vehicle else None