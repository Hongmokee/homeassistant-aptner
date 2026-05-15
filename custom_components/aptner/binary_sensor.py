from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SECTION_PARKING, enabled_sections_from_config
from .coordinator import AptnerDataUpdateCoordinator
from .sensor import (
    _device_info_for_key,
    _guest_parking_active_count,
    _guest_parking_latest_event,
    _parking_vehicle_history_payload,
    _visit_today_item,
    _visit_valid_items,
)

SENSITIVE_BINARY_SENSOR_KEYS_DISABLED_BY_DEFAULT = {
    "parking_vehicle_inside",
    "parking_vehicle_last_event_entry",
    "parking_vehicle_last_event_exit",
    "parking_vehicle_inside_all",
    "parking_vehicle_last_event_entry_all",
    "parking_vehicle_last_event_exit_all",
    "visit_vehicle_alert",
    "visit_vehicle_today",
}


def _visit_vehicle_alert_on(data: dict[str, Any]) -> bool:
    payload = data.get("visit_vehicle_usage")
    if not isinstance(payload, dict):
        return False
    return len(_visit_valid_items(payload)) > 0


def _visit_vehicle_today_on(data: dict[str, Any]) -> bool:
    payload = data.get("visit_vehicle_usage")
    if not isinstance(payload, dict):
        return False
    return _visit_today_item(payload) is not None


def _parking_vehicle_inside_on(
    data: dict[str, Any],
    *,
    include_resident: bool | None = None,
) -> bool:
    payload = _parking_vehicle_history_payload(data)
    if not isinstance(payload, dict):
        return False
    active_count = _guest_parking_active_count(payload, include_resident=include_resident)
    if active_count is None:
        return False
    return active_count > 0


def _parking_vehicle_last_event_entry_on(
    data: dict[str, Any],
    *,
    include_resident: bool | None = None,
) -> bool:
    payload = _parking_vehicle_history_payload(data)
    if not isinstance(payload, dict):
        return False
    latest_event = _guest_parking_latest_event(payload, include_resident=include_resident)
    if latest_event is None:
        return False
    return latest_event.get("type") == "entry"


def _parking_vehicle_last_event_exit_on(
    data: dict[str, Any],
    *,
    include_resident: bool | None = None,
) -> bool:
    payload = _parking_vehicle_history_payload(data)
    if not isinstance(payload, dict):
        return False
    latest_event = _guest_parking_latest_event(payload, include_resident=include_resident)
    if latest_event is None:
        return False
    return latest_event.get("type") == "exit"


@dataclass(frozen=True, kw_only=True)
class AptnerBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool]

    def __post_init__(self) -> None:
        if self.translation_key is None:
            object.__setattr__(self, "translation_key", self.key)
        object.__setattr__(self, "name", None)


BINARY_SENSORS: tuple[AptnerBinarySensorDescription, ...] = (
    AptnerBinarySensorDescription(
        key="parking_vehicle_inside",
        icon="mdi:garage-open-variant",
        is_on_fn=lambda data: _parking_vehicle_inside_on(
            data,
            include_resident=False,
        ),
    ),
    AptnerBinarySensorDescription(
        key="parking_vehicle_last_event_entry",
        icon="mdi:car-arrow-left",
        is_on_fn=lambda data: _parking_vehicle_last_event_entry_on(
            data,
            include_resident=False,
        ),
    ),
    AptnerBinarySensorDescription(
        key="parking_vehicle_last_event_exit",
        icon="mdi:car-arrow-right",
        is_on_fn=lambda data: _parking_vehicle_last_event_exit_on(
            data,
            include_resident=False,
        ),
    ),
    AptnerBinarySensorDescription(
        key="parking_vehicle_inside_all",
        icon="mdi:garage-open-variant",
        is_on_fn=lambda data: _parking_vehicle_inside_on(
            data,
            include_resident=None,
        ),
    ),
    AptnerBinarySensorDescription(
        key="parking_vehicle_last_event_entry_all",
        icon="mdi:car-arrow-left",
        is_on_fn=lambda data: _parking_vehicle_last_event_entry_on(
            data,
            include_resident=None,
        ),
    ),
    AptnerBinarySensorDescription(
        key="parking_vehicle_last_event_exit_all",
        icon="mdi:car-arrow-right",
        is_on_fn=lambda data: _parking_vehicle_last_event_exit_on(
            data,
            include_resident=None,
        ),
    ),
    AptnerBinarySensorDescription(
        key="visit_vehicle_alert",
        icon="mdi:car-connected",
        is_on_fn=_visit_vehicle_alert_on,
    ),
    AptnerBinarySensorDescription(
        key="visit_vehicle_today",
        icon="mdi:car-clock",
        is_on_fn=_visit_vehicle_today_on,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AptnerDataUpdateCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    if SECTION_PARKING not in enabled_sections_from_config(entry.data, entry.options):
        return
    async_add_entities(
        AptnerBinarySensor(coordinator, entry, description) for description in BINARY_SENSORS
    )


class AptnerBinarySensor(CoordinatorEntity[AptnerDataUpdateCoordinator], BinarySensorEntity):
    """Representation of an Aptner binary sensor."""

    _attr_has_entity_name = True

    entity_description: AptnerBinarySensorDescription

    def __init__(
        self,
        coordinator: AptnerDataUpdateCoordinator,
        entry: ConfigEntry,
        description: AptnerBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        if description.key in SENSITIVE_BINARY_SENSOR_KEYS_DISABLED_BY_DEFAULT:
            self._attr_entity_registry_enabled_default = False

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data)

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info_for_key(
            self.coordinator.hass,
            self._entry.entry_id,
            self.coordinator.data,
            self.entity_description.key,
        )
