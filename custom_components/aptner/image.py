from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AptnerDataUpdateCoordinator
from .sensor import (
    _device_info_for_key,
    _latest_board_first_image_url,
)


@dataclass(frozen=True, kw_only=True)
class AptnerImageDescription:
    key: str
    payload_key: str
    icon: str = "mdi:image"


IMAGES: tuple[AptnerImageDescription, ...] = (
    AptnerImageDescription(
        key="latest_notice_image",
        payload_key="board_notice",
    ),
    AptnerImageDescription(
        key="latest_community_image",
        payload_key="board_community",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AptnerDataUpdateCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(AptnerImage(coordinator, entry, description) for description in IMAGES)


class AptnerImage(CoordinatorEntity[AptnerDataUpdateCoordinator], ImageEntity):
    """Representation of an Aptner board image."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AptnerDataUpdateCoordinator,
        entry: ConfigEntry,
        description: AptnerImageDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_icon = description.icon
        self._attr_name = None
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}_image"
        self._image_url = self._current_image_url()
        self._image_last_updated = datetime.now(timezone.utc) if self._image_url else None

    def _current_image_url(self) -> str | None:
        return _latest_board_first_image_url(
            self.coordinator.data.get(self.entity_description.payload_key)
        )

    @property
    def image_url(self) -> str | None:
        return self._image_url

    @property
    def image_last_updated(self) -> datetime | None:
        return self._image_last_updated

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info_for_key(
            self.coordinator.hass,
            self._entry.entry_id,
            self.coordinator.data,
            self.entity_description.key,
        )

    def _handle_coordinator_update(self) -> None:
        image_url = self._current_image_url()
        if image_url != self._image_url:
            self._image_url = image_url
            self._image_last_updated = datetime.now(timezone.utc) if image_url else None
        super()._handle_coordinator_update()
