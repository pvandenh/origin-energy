"""Shared base entity for the Origin Energy (Kraken) integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN


def dig(data: dict, *path: str | int, default=None):
    """Safely walk a chain of dict keys / list indices, or return `default`.

    The account used to build this only has an empty/new account, so most
    of the "real" data (actual bills, non-zero balances, etc.) has never
    actually been observed - this makes every lookup tolerant of fields
    being absent/null, or a list being empty, rather than assuming the
    shape seen so far is final.
    """
    current = data
    for key in path:
        if isinstance(current, dict):
            if key not in current:
                return default
            current = current[key]
        elif isinstance(current, list):
            if not isinstance(key, int) or not -len(current) <= key < len(current):
                return default
            current = current[key]
        else:
            return default
    return current if current is not None else default


def normalize_charge_status(raw: str | None) -> str | None:
    """Collapse Origin's raw chargeState.status values to a small set of states.

    Only a handful of raw values have actually been observed
    (plugged_in_stopped, plugged_in_charging, plugged_in_completed,
    unplugged) - not necessarily the full set Origin can return. This
    classifies by substring rather than an exhaustive lookup table:
    explicit unavailable/unknown values, and anything else unrecognised,
    return None (HA's standard "unknown" state) rather than being
    guessed into one of the known buckets; unplugged/disconnected
    reports as "unplugged"; a finished charge reports as "complete"
    (distinct from "paused" - the vehicle isn't waiting to resume, it's
    done); an actively-drawing state reports as "charging"; and
    plugged_in_stopped (or anything else explicitly stopped/paused)
    reports as "paused".
    """
    if not raw:
        return None
    value = raw.lower()
    if "unavail" in value or "unknown" in value:
        return None
    if "complet" in value:
        return "complete"
    if "unplug" in value or "disconnect" in value or "not_connected" in value:
        return "unplugged"
    if "charg" in value and "pause" not in value and "stop" not in value:
        return "charging"
    if "stop" in value or "pause" in value:
        return "paused"
    return None


class OriginEnergyEntity(CoordinatorEntity):
    """Base entity - every account-level entity represents the account itself."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        origin_account_id: str,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{origin_account_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, origin_account_id)},
            name="Origin Energy",
            manufacturer="Origin Energy",
            model="Kraken (unofficial)",
        )


class OriginEnergyVehicleEntity(CoordinatorEntity):
    """Base entity for one EV iCharge vehicle.

    Each vehicle is modelled as its own HA device (not lumped under the
    account device), since that's what it actually is - a separate
    physical thing with its own make/model - and it's what makes a house
    with multiple EVs show up as separate cards/areas in HA.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        origin_account_id: str,
        vehicle_id: str,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

        vehicle = self._vehicle_data or {}
        make = vehicle.get("make")
        model = vehicle.get("model")
        # vehicle["name"] is effectively the VIN in Origin's data, not a
        # human-friendly label - prefer make/model, falling back to
        # whichever of the two is present, and only to name/VIN or "EV"
        # if neither make nor model came through.
        if make and model:
            device_name = f"{make} {model}"
        else:
            device_name = model or make or vehicle.get("name") or "EV"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle_id)},
            name=device_name,
            manufacturer=make,
            model=model,
            via_device=(DOMAIN, origin_account_id),
        )

    @property
    def _vehicle_data(self) -> dict[str, Any] | None:
        """This entity's vehicle data, or None if it's since disappeared.

        Vehicles are matched by id, not list position, since a vehicle
        can be unlinked or the list re-ordered between polls.
        """
        return dig(self.coordinator.data, "ev_vehicles", self._vehicle_id)

    @property
    def available(self) -> bool:
        return super().available and self._vehicle_data is not None


def async_add_vehicle_entities(
    coordinator: DataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    make_entities_for_vehicle: Callable[[str], list],
) -> None:
    """Add entities for every vehicle known now, and for any that appear later.

    ev_vehicles can change between polls (a car added/removed in the
    portal) without Home Assistant being reloaded, so this doesn't just
    run once at startup - it also subscribes to the coordinator and adds
    entities for any newly-seen vehicle id on a later refresh.
    """
    known_vehicle_ids: set[str] = set()

    def _add_for_current_data() -> None:
        vehicles = dig(coordinator.data, "ev_vehicles", default={})
        new_ids = set(vehicles) - known_vehicle_ids
        if not new_ids:
            return
        known_vehicle_ids.update(new_ids)
        new_entities = [
            entity
            for vehicle_id in new_ids
            for entity in make_entities_for_vehicle(vehicle_id)
        ]
        async_add_entities(new_entities)

    _add_for_current_data()
    coordinator.async_add_listener(_add_for_current_data)
