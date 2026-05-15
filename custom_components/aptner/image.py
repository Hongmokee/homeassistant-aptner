from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SECTION_COMMUNITY, enabled_sections_from_config
from .coordinator import AptnerDataUpdateCoordinator
from .sensor import (
    _device_info_for_key,
    _latest_board_first_image_url,
)

_LOGGER = logging.getLogger(__name__)

GET_IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_IMAGE_BYTES = 10 * 1024 * 1024

IMAGE_CONTENT_TYPES_BY_EXTENSION = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


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


def _content_type_from_url(url: str | None) -> str:
    if not url:
        return "image/jpeg"
    path = urlparse(url).path.lower()
    for extension, content_type in IMAGE_CONTENT_TYPES_BY_EXTENSION.items():
        if path.endswith(extension):
            return content_type
    return "image/jpeg"


def _valid_response_content_type(content_type: str | None, fallback: str) -> str | None:
    if content_type:
        content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
        if content_type.startswith("image/"):
            return content_type
    if fallback.startswith("image/"):
        return fallback
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AptnerDataUpdateCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    if SECTION_COMMUNITY not in enabled_sections_from_config(entry.data, entry.options):
        return
    entities = [AptnerImage(coordinator, entry, description) for description in IMAGES]
    async_add_entities(entities)


class AptnerImage(CoordinatorEntity[AptnerDataUpdateCoordinator], ImageEntity):
    """Representation of an Aptner board image."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: AptnerDataUpdateCoordinator,
        entry: ConfigEntry,
        description: AptnerImageDescription,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self.entity_description = description
        self._entry = entry
        self._session = async_get_clientsession(coordinator.hass)
        self._attr_icon = description.icon
        self._attr_name = None
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}_image"
        self._image_url = self._current_image_url()
        self._image: bytes | None = None
        self._attr_content_type = _content_type_from_url(self._image_url)
        self._attr_image_last_updated = (
            datetime.now(timezone.utc) if self._image_url else None
        )

    def _current_image_url(self) -> str | None:
        return _latest_board_first_image_url(
            self.coordinator.data.get(self.entity_description.payload_key)
        )

    @property
    def content_type(self) -> str:
        return self._attr_content_type

    @property
    def image_last_updated(self) -> datetime | None:
        return self._attr_image_last_updated

    async def async_image(self) -> bytes | None:
        """Return bytes of image."""
        image_url = self._current_image_url()
        if not image_url:
            return None
        if image_url != self._image_url:
            self._set_image_url(image_url)
        if self._image is not None:
            return self._image

        fallback_content_type = _content_type_from_url(image_url)
        try:
            async with self._session.get(
                image_url,
                timeout=GET_IMAGE_TIMEOUT,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                if (
                    response.content_length is not None
                    and response.content_length > MAX_IMAGE_BYTES
                ):
                    _LOGGER.warning(
                        "%s image is too large to load",
                        self.entity_description.key,
                    )
                    return None
                content_type = _valid_response_content_type(
                    response.headers.get("Content-Type"),
                    fallback_content_type,
                )
                if content_type is None:
                    _LOGGER.warning(
                        "%s image response did not contain image content",
                        self.entity_description.key,
                    )
                    return None
                image = await response.content.read(MAX_IMAGE_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            _LOGGER.warning(
                "Failed to load %s image: %s",
                self.entity_description.key,
                err,
            )
            return None

        if len(image) > MAX_IMAGE_BYTES:
            _LOGGER.warning(
                "%s image is too large to load",
                self.entity_description.key,
            )
            return None

        self._image = image
        self._attr_content_type = content_type
        return image

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info_for_key(
            self.coordinator.hass,
            self._entry.entry_id,
            self.coordinator.data,
            self.entity_description.key,
        )

    def _set_image_url(self, image_url: str | None) -> None:
        self._image_url = image_url
        self._image = None
        self._attr_content_type = _content_type_from_url(image_url)
        self._attr_image_last_updated = (
            datetime.now(timezone.utc) if image_url else None
        )

    def _handle_coordinator_update(self) -> None:
        image_url = self._current_image_url()
        if image_url != self._image_url:
            self._set_image_url(image_url)
        super()._handle_coordinator_update()
