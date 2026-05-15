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
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SECTION_COMMUNITY, enabled_sections_from_config
from .coordinator import AptnerDataUpdateCoordinator
from .sensor import (
    _device_info_for_key,
    _latest_board_article_id,
    _latest_board_first_image_url,
    _latest_board_image_urls,
    _latest_board_title,
)

_LOGGER = logging.getLogger(__name__)

GET_IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_IMAGE_BYTES = 100 * 1024 * 1024
IMAGE_READ_CHUNK_BYTES = 64 * 1024

IMAGE_CONTENT_TYPES_BY_EXTENSION = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

IMAGE_CONTENT_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-citrix-jpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/x-citrix-png": "image/png",
}

IMAGE_SIGNATURE_CONTENT_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@dataclass(frozen=True, kw_only=True)
class AptnerImageDescription(EntityDescription):
    payload_key: str
    icon: str = "mdi:image"
    suggested_object_id: str

    def __post_init__(self) -> None:
        if self.translation_key is None:
            object.__setattr__(self, "translation_key", self.key)


IMAGES: tuple[AptnerImageDescription, ...] = (
    AptnerImageDescription(
        key="latest_notice_image",
        name="최신 공지 이미지",
        payload_key="board_notice",
        suggested_object_id="latest_notice_image",
    ),
    AptnerImageDescription(
        key="queued_notice_image",
        name="지연중계 공지 이미지",
        payload_key="notice_replay",
        suggested_object_id="queued_notice_image",
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


def _normalize_image_content_type(content_type: str | None) -> str | None:
    if content_type:
        content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
        content_type = IMAGE_CONTENT_TYPE_ALIASES.get(content_type, content_type)
        if content_type.startswith("image/"):
            return content_type
    return None


def _valid_response_content_type(content_type: str | None, fallback: str) -> str | None:
    return _normalize_image_content_type(
        content_type,
    ) or _normalize_image_content_type(fallback)


def _content_type_from_image_bytes(image: bytes) -> str | None:
    for signature, content_type in IMAGE_SIGNATURE_CONTENT_TYPES:
        if image.startswith(signature):
            return content_type
    if len(image) >= 12 and image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    return None


def _content_type_for_image(image: bytes, fallback: str) -> str | None:
    return _content_type_from_image_bytes(image) or _normalize_image_content_type(
        fallback,
    )


def _image_content_type_or_default(image: bytes | None, fallback: str | None) -> str:
    if image is not None:
        content_type = _content_type_for_image(image, fallback or "image/jpeg")
        if content_type is not None:
            return content_type
    return _normalize_image_content_type(fallback) or "image/jpeg"


def _board_payload_for_image(payload: object) -> object:
    if isinstance(payload, dict):
        active = payload.get("active")
        if isinstance(active, dict):
            return {"latest": active}
    return payload


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

    _attr_has_entity_name = False
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
        self._attr_name = description.name
        self._attr_suggested_object_id = description.suggested_object_id
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._image_url = self._current_image_url()
        self._image: bytes | None = None
        self._attr_content_type = _content_type_from_url(self._image_url)
        self._update_extra_state_attributes()
        self._attr_image_last_updated = (
            datetime.now(timezone.utc) if self._image_url else None
        )

    def _current_image_url(self) -> str | None:
        return _latest_board_first_image_url(
            _board_payload_for_image(
                self.coordinator.data.get(self.entity_description.payload_key)
            )
        )

    @property
    def available(self) -> bool:
        return super().available and self._image_url is not None

    @property
    def content_type(self) -> str:
        content_type = _image_content_type_or_default(
            self._image,
            self._attr_content_type,
        )
        self._attr_content_type = content_type
        return content_type

    @property
    def image_last_updated(self) -> datetime | None:
        return self._attr_image_last_updated

    async def async_image(self) -> bytes | None:
        """Return bytes of image."""
        image_url = self._current_image_url()
        if not image_url:
            if self._image_url is not None:
                self._set_image_url(None)
            return None
        if image_url != self._image_url:
            self._set_image_url(image_url)
        if self._image is not None:
            self._attr_content_type = _image_content_type_or_default(
                self._image,
                self._attr_content_type,
            )
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
                image_buffer = bytearray()
                async for chunk in response.content.iter_chunked(
                    IMAGE_READ_CHUNK_BYTES
                ):
                    image_buffer.extend(chunk)
                    if len(image_buffer) > MAX_IMAGE_BYTES:
                        _LOGGER.warning(
                            "%s image is too large to load",
                            self.entity_description.key,
                        )
                        return None
                image = bytes(image_buffer)
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

        content_type = _content_type_for_image(image, content_type)
        if content_type is None:
            _LOGGER.warning(
                "%s image response did not contain supported image content",
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
        self._update_extra_state_attributes()
        self._attr_image_last_updated = (
            datetime.now(timezone.utc) if image_url else None
        )

    def _update_extra_state_attributes(self) -> None:
        payload = _board_payload_for_image(
            self.coordinator.data.get(self.entity_description.payload_key)
        )
        attributes = {}
        article_id = _latest_board_article_id(payload)
        title = _latest_board_title(payload)
        image_urls = _latest_board_image_urls(payload)
        if article_id is not None:
            attributes["article_id"] = article_id
        if title:
            attributes["article_title"] = title
        if self._image_url:
            attributes["image_url"] = self._image_url
            attributes["content_type"] = self.content_type
        if image_urls:
            attributes["image_urls"] = image_urls
        self._attr_extra_state_attributes = attributes

    def _handle_coordinator_update(self) -> None:
        image_url = self._current_image_url()
        if image_url != self._image_url:
            self._set_image_url(image_url)
        else:
            self._update_extra_state_attributes()
        super()._handle_coordinator_update()
