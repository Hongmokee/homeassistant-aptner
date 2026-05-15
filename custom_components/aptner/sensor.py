from __future__ import annotations

import base64
import binascii
import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlparse

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SECTION_BILLING, enabled_sections_from_config
from .coordinator import AptnerDataUpdateCoordinator

DEVICE_GROUP_NAMES_BY_LANGUAGE: dict[str, dict[str, str]] = {
    "en": {
        "overview": "Overview",
        "community": "Community",
        "schedule": "Schedule",
        "parking": "Parking",
        "safety": "Safety",
        "household": "Household",
        "billing": "Billing",
    },
    "ko": {
        "overview": "개요",
        "community": "커뮤니티",
        "schedule": "일정",
        "parking": "주차",
        "safety": "안전",
        "household": "세대",
        "billing": "관리비",
    },
}

SENSOR_UNITS_BY_LANGUAGE: dict[str, dict[str, str]] = {
    "en": {
        "usage_services": "services",
        "notice_count": "items",
        "queued_notice_remaining": "items",
        "community_count": "items",
        "complaint_count": "items",
        "defect_count": "items",
        "region_count": "items",
        "schedule_count": "items",
        "schedule_next_day": "day",
        "vote_current_count": "items",
        "vote_closed_count": "items",
        "survey_ing_count": "items",
        "survey_done_count": "items",
        "aptner_notice_count": "items",
        "contacts_count": "items",
        "broadcast_count": "items",
        "guest_parking_count": "items",
        "guest_parking_history_count": "items",
        "guest_parking_household_limit": "mins",
        "guest_parking_remaining_free": "uses",
        "parking_discount_history_count": "items",
        "visit_vehicle_usage_count": "items",
        "visit_vehicle_valid_count": "items",
        "realtor_count": "items",
        "fire_inspection_history_count": "items",
        "household_member_count": "people",
        "household_verified_count": "people",
        "management_fee_area": "m²",
        "management_fee_breakdown_count": "items",
        "management_fee_history_count": "periods",
    },
    "ko": {
        "usage_services": "개",
        "notice_count": "건",
        "queued_notice_remaining": "건",
        "community_count": "건",
        "complaint_count": "건",
        "defect_count": "건",
        "region_count": "건",
        "schedule_count": "건",
        "schedule_next_day": "일",
        "vote_current_count": "건",
        "vote_closed_count": "건",
        "survey_ing_count": "건",
        "survey_done_count": "건",
        "aptner_notice_count": "건",
        "contacts_count": "건",
        "broadcast_count": "건",
        "guest_parking_count": "건",
        "guest_parking_history_count": "건",
        "guest_parking_household_limit": "분",
        "guest_parking_remaining_free": "회",
        "parking_discount_history_count": "건",
        "visit_vehicle_usage_count": "건",
        "visit_vehicle_valid_count": "건",
        "realtor_count": "건",
        "fire_inspection_history_count": "건",
        "household_member_count": "명",
        "household_verified_count": "명",
        "management_fee_area": "m²",
        "management_fee_breakdown_count": "건",
        "management_fee_history_count": "기",
    },
}

SENSOR_PRECISION_BY_KEY: dict[str, int] = {
    "usage_services": 0,
    "notice_count": 0,
    "queued_notice_remaining": 0,
    "community_count": 0,
    "complaint_count": 0,
    "defect_count": 0,
    "region_count": 0,
    "schedule_count": 0,
    "schedule_next_day": 0,
    "vote_current_count": 0,
    "vote_closed_count": 0,
    "survey_ing_count": 0,
    "survey_done_count": 0,
    "aptner_notice_count": 0,
    "contacts_count": 0,
    "broadcast_count": 0,
    "guest_parking_count": 0,
    "guest_parking_history_count": 0,
    "guest_parking_household_limit": 0,
    "guest_parking_remaining_free": 0,
    "parking_discount_history_count": 0,
    "visit_vehicle_usage_count": 0,
    "visit_vehicle_valid_count": 0,
    "realtor_count": 0,
    "fire_inspection_history_count": 0,
    "household_member_count": 0,
    "household_verified_count": 0,
    "management_fee": 0,
    "management_fee_average": 0,
    "management_fee_area": 2,
    "management_fee_current_late_fee": 0,
    "management_fee_delinquent_fee": 0,
    "management_fee_delinquent_late_fee": 0,
    "management_fee_breakdown_count": 0,
    "management_fee_previous": 0,
    "management_fee_change": 0,
    "management_fee_history_count": 0,
}

PARKING_HISTORY_DATETIME_FORMATS = (
    "%Y.%m.%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y.%m.%d",
    "%Y-%m-%d",
)

MAX_TEXT_SENSOR_STATE_LENGTH = 255
MAX_BOARD_IMAGE_URLS = 10
APTNER_KNOWN_IMAGE_HOSTS = {"d3fixtu11mscj5.cloudfront.net"}
BOARD_IMAGE_EXTENSIONS = "png|jpe?g|gif|webp"
HTML_BREAK_PATTERN = re.compile(r"(?i)<\s*(br|/p|/div|/li)\b[^>]*>")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
HTML_BLOCK_PATTERN = re.compile(r"(?is)<\s*(script|style)\b.*?<\s*/\s*\1\s*>")
HTML_HEAD_PATTERN = re.compile(r"(?is)<\s*head\b.*?<\s*/\s*head\s*>")
HTML_BODY_PATTERN = re.compile(r"(?is)<\s*body\b[^>]*>(.*?)<\s*/\s*body\s*>")
HTML_LIKE_PATTERN = re.compile(r"(?is)<\s*(html|body|div|p|span|br|img|table|ul|ol|li)\b")
HTML_IMAGE_SRC_PATTERN = re.compile(r"(?is)<img\b[^>]*\bsrc\s*=\s*[\"']?([^\"'\s>]+)")
IMAGE_URL_PATTERN = re.compile(
    rf"(?i)https?://[^\s\"'<>]+?\.(?:{BOARD_IMAGE_EXTENSIONS})(?:\?[^\s\"'<>]*)?"
)
IMAGE_RELATIVE_PATH_PATTERN = re.compile(
    rf"(?i)(?:^|[\"'(\s])(/?aptner/board/[^\s\"'<>)]*?\.(?:{BOARD_IMAGE_EXTENSIONS})(?:\?[^\s\"'<>)]*)?)"
)
ABSOLUTE_URL_PATTERN = re.compile(r"(?i)https?://[^\s\"'<>)]*")
IMAGE_FIELD_HINTS = ("image", "img", "thumbnail", "photo", "picture")
BASE64_DATA_PATTERN = re.compile(r"(?is)^\s*data:[^,;]+;base64,(.+)$")
BASE64_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9+/_-]+={0,2}$")
BOARD_CONTENT_FIELDS = (
    "content",
    "contents",
    "body",
    "bodyText",
    "articleContent",
    "description",
    "text",
)

OVERVIEW_KEYS = {
    "apartment",
    "apartment_phone",
    "usage_services",
}

COMMUNITY_KEYS = {
    "notice_count",
    "latest_notice_title",
    "latest_notice_date",
    "latest_notice_content",
    "latest_notice_image",
    "queued_notice_article_id",
    "queued_notice_title",
    "queued_notice_content",
    "queued_notice_remaining",
    "queued_notice_image",
    "community_count",
    "latest_community_title",
    "latest_community_content",
    "complaint_count",
    "latest_complaint_title",
    "latest_complaint_status",
    "defect_count",
    "region_count",
    "latest_region_title",
    "aptner_notice_count",
    "aptner_notice_latest_title",
    "contacts_count",
    "primary_contact",
    "broadcast_count",
    "broadcast_latest",
    "realtor_count",
}

SCHEDULE_KEYS = {
    "schedule_count",
    "schedule_next_day",
    "vote_current_count",
    "vote_closed_count",
    "survey_ing_count",
    "survey_done_count",
}

PARKING_KEYS = {
    "guest_parking_count",
    "guest_parking_history_count",
    "guest_parking_household_limit",
    "guest_parking_remaining_free",
    "parking_discount_history_count",
    "parking_vehicle_last_entry_at",
    "parking_vehicle_last_exit_at",
    "parking_vehicle_last_entry_at_all",
    "parking_vehicle_last_exit_at_all",
    "parking_vehicle_inside",
    "parking_vehicle_inside_all",
    "parking_vehicle_last_event_entry",
    "parking_vehicle_last_event_exit",
    "parking_vehicle_last_event_entry_all",
    "parking_vehicle_last_event_exit_all",
    "visit_vehicle_usage_count",
    "visit_vehicle_valid_count",
    "visit_vehicle_next_date",
    "visit_vehicle_next_car_no",
    "visit_vehicle_next_purpose",
    "visit_vehicle_alert",
    "visit_vehicle_today",
}

SAFETY_KEYS = {
    "fire_inspection",
    "fire_inspection_history_count",
}

HOUSEHOLD_KEYS = {
    "household_member_count",
    "household_verified_count",
}

SENSITIVE_SENSOR_KEYS_DISABLED_BY_DEFAULT = {
    "parking_vehicle_last_entry_at",
    "parking_vehicle_last_exit_at",
    "parking_vehicle_last_entry_at_all",
    "parking_vehicle_last_exit_at_all",
    "visit_vehicle_next_date",
    "visit_vehicle_next_car_no",
    "visit_vehicle_next_purpose",
}


def _device_group_for_key(entity_key: str) -> str:
    if entity_key in OVERVIEW_KEYS:
        return "overview"
    if entity_key in COMMUNITY_KEYS:
        return "community"
    if entity_key in SCHEDULE_KEYS:
        return "schedule"
    if entity_key in PARKING_KEYS:
        return "parking"
    if entity_key in SAFETY_KEYS:
        return "safety"
    if entity_key in HOUSEHOLD_KEYS:
        return "household"
    if entity_key.startswith("management_fee"):
        return "billing"
    return "overview"


def _preferred_language(hass: HomeAssistant) -> str:
    language = getattr(hass.config, "language", None)
    if not isinstance(language, str) or not language:
        return "en"
    language_code = language.split("-", maxsplit=1)[0].lower()
    if language_code in DEVICE_GROUP_NAMES_BY_LANGUAGE:
        return language_code
    return "en"


def _localized_group_name(hass: HomeAssistant, group_key: str) -> str:
    language = _preferred_language(hass)
    return DEVICE_GROUP_NAMES_BY_LANGUAGE[language].get(
        group_key,
        DEVICE_GROUP_NAMES_BY_LANGUAGE["en"][group_key],
    )


def _localized_sensor_unit(hass: HomeAssistant, entity_key: str) -> str | None:
    language = _preferred_language(hass)
    unit_map = SENSOR_UNITS_BY_LANGUAGE.get(language, SENSOR_UNITS_BY_LANGUAGE["en"])
    if entity_key in unit_map:
        return unit_map[entity_key]
    return SENSOR_UNITS_BY_LANGUAGE["en"].get(entity_key)


def _device_info_for_key(
    hass: HomeAssistant,
    entry_id: str,
    data: dict[str, Any],
    entity_key: str,
) -> DeviceInfo:
    group_key = _device_group_for_key(entity_key)
    group_name = _localized_group_name(hass, group_key)
    apartment_name = _apartment_name_value(data) or "Aptner"

    if group_key == "overview":
        return DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=apartment_name,
            model="Aptner",
        )

    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{group_key}")},
        name=f"{apartment_name} {group_name}",
        model=f"Aptner {group_name}",
        via_device=(DOMAIN, entry_id),
    )


def _error_text(payload: Any) -> str | None:
    if isinstance(payload, dict):
        error = payload.get("_error")
        if isinstance(error, str):
            return error
    return None


def _board_articles(payload: Any) -> list[dict[str, Any]]:
    listing = _board_list_payload(payload)
    articles = listing.get("articleList")
    return articles if isinstance(articles, list) else []


def _board_list_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    listing = payload.get("list")
    if isinstance(listing, dict):
        return listing
    return payload


def _unwrap_board_detail_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for key in ("data", "article", "articleDetail", "boardArticle", "detail"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return _unwrap_board_detail_payload(nested) or nested
    return payload


def _latest_board_detail_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    latest = payload.get("latest")
    if isinstance(latest, dict):
        detail = _unwrap_board_detail_payload(latest)
        if detail is not None:
            return detail
    articles = _board_articles(payload)
    if articles and isinstance(articles[0], dict):
        return articles[0]
    return None


def _latest_board_article_id(payload: Any) -> Any:
    detail = _latest_board_detail_payload(payload)
    if detail is None:
        return None
    for key in ("articleId", "id", "articleNo", "idx"):
        value = detail.get(key)
        if value is not None:
            return value
    return None


def _html_to_text(value: str) -> str:
    value = _decode_possible_base64_html(value)
    body_match = HTML_BODY_PATTERN.search(value)
    cleaned = body_match.group(1) if body_match else value
    cleaned = HTML_HEAD_PATTERN.sub("", cleaned)
    cleaned = HTML_BLOCK_PATTERN.sub("", cleaned)
    cleaned = HTML_BREAK_PATTERN.sub("\n", cleaned)
    cleaned = HTML_TAG_PATTERN.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [" ".join(line.split()) for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


def _decode_base64_text(value: str) -> str | None:
    value = html.unescape(value).strip().strip("\"'")
    data_match = BASE64_DATA_PATTERN.match(value)
    is_data_uri = data_match is not None
    if data_match:
        value = data_match.group(1)
    compact = "".join(value.split())
    if len(compact) < 16:
        return None
    if BASE64_TEXT_PATTERN.fullmatch(compact) is None:
        return None
    compact = compact.replace("-", "+").replace("_", "/")
    compact += "=" * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            text = decoded.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not text.strip():
            continue
        printable = sum(char.isprintable() or char.isspace() for char in text)
        if printable / len(text) < 0.9:
            continue
        if HTML_LIKE_PATTERN.search(text):
            return text
        if is_data_uri and any(char.isalpha() for char in text):
            return text
    return None


def _decode_possible_base64_html(value: str) -> str:
    decoded = _decode_base64_text(value)
    return decoded if decoded is not None else value


def _short_text_state(value: str | None) -> str | None:
    if not value:
        return None
    value = _html_to_text(value)
    if len(value) <= MAX_TEXT_SENSOR_STATE_LENGTH:
        return value
    return f"{value[: MAX_TEXT_SENSOR_STATE_LENGTH - 3]}..."


def _latest_board_string_value(payload: Any, *keys: str) -> str | None:
    detail = _latest_board_detail_payload(payload)
    if detail is None:
        return None
    for key in keys:
        value = detail.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _latest_board_content(payload: Any) -> str | None:
    value = _latest_board_string_value(payload, *BOARD_CONTENT_FIELDS)
    if value is None:
        return None
    return _html_to_text(value)


def _board_image_base_url_from_payload(value: Any) -> str | None:
    candidates: list[str] = []
    _collect_absolute_urls(value, candidates)
    for candidate in candidates:
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        if parsed.path.startswith("/aptner/board/") or parsed.netloc in APTNER_KNOWN_IMAGE_HOSTS:
            return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _collect_absolute_urls(value: Any, urls: list[str]) -> None:
    if isinstance(value, str):
        for match in ABSOLUTE_URL_PATTERN.finditer(value):
            url = html.unescape(match.group(0)).strip().strip("\"'")
            if url not in urls:
                urls.append(url)
        return
    if isinstance(value, dict):
        for nested in value.values():
            _collect_absolute_urls(nested, urls)
        return
    if isinstance(value, list):
        for item in value:
            _collect_absolute_urls(item, urls)


def _normalize_board_image_url(value: str, *, base_url: str | None = None) -> str | None:
    value = html.unescape(value).strip().strip("\"'")
    if not value:
        return None
    if value.startswith("//"):
        value = f"https:{value}"
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            return None
        if not parsed.path.startswith("/aptner/board/"):
            return None
        return value
    if base_url is not None and (
        value.startswith("/aptner/board/") or value.startswith("aptner/board/")
    ):
        return urljoin(f"{base_url.rstrip('/')}/", value.lstrip("/"))
    return None


def _add_image_url(urls: list[str], value: str, *, base_url: str | None = None) -> None:
    normalized = _normalize_board_image_url(value, base_url=base_url)
    if normalized and normalized not in urls:
        urls.append(normalized)


def _normalize_board_image_path(value: str) -> str | None:
    value = html.unescape(value).strip().strip("\"'")
    if value.startswith("/aptner/board/"):
        return value
    if value.startswith("aptner/board/"):
        return f"/{value}"
    return None


def _add_image_path(paths: list[str], value: str) -> None:
    normalized = _normalize_board_image_path(value)
    if normalized and normalized not in paths:
        paths.append(normalized)


def _collect_board_image_refs(
    value: Any,
    urls: list[str],
    paths: list[str],
    *,
    key_hint: str = "",
    base_url: str | None = None,
) -> None:
    if len(urls) >= MAX_BOARD_IMAGE_URLS:
        return
    if isinstance(value, str):
        value = _decode_possible_base64_html(value)
        for match in HTML_IMAGE_SRC_PATTERN.finditer(value):
            _add_image_url(urls, match.group(1), base_url=base_url)
            _add_image_path(paths, match.group(1))
            if len(urls) >= MAX_BOARD_IMAGE_URLS:
                return
        for match in IMAGE_URL_PATTERN.finditer(value):
            _add_image_url(urls, match.group(0), base_url=base_url)
            if len(urls) >= MAX_BOARD_IMAGE_URLS:
                return
        for match in IMAGE_RELATIVE_PATH_PATTERN.finditer(value):
            _add_image_url(urls, match.group(1), base_url=base_url)
            _add_image_path(paths, match.group(1))
            if len(urls) >= MAX_BOARD_IMAGE_URLS:
                return
        if key_hint:
            normalized_key = key_hint.lower()
            if any(hint in normalized_key for hint in IMAGE_FIELD_HINTS):
                _add_image_url(urls, value, base_url=base_url)
                _add_image_path(paths, value)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                _collect_board_image_refs(
                    nested,
                    urls,
                    paths,
                    key_hint=key,
                    base_url=base_url,
                )
            else:
                _collect_board_image_refs(nested, urls, paths, base_url=base_url)
            if len(urls) >= MAX_BOARD_IMAGE_URLS:
                return
        return
    if isinstance(value, list):
        for item in value:
            _collect_board_image_refs(
                item,
                urls,
                paths,
                key_hint=key_hint,
                base_url=base_url,
            )
            if len(urls) >= MAX_BOARD_IMAGE_URLS:
                return


def _latest_board_image_refs(payload: Any) -> tuple[list[str], list[str]]:
    detail = _latest_board_detail_payload(payload)
    if detail is None:
        return [], []
    urls: list[str] = []
    paths: list[str] = []
    base_url = _board_image_base_url_from_payload(detail)
    for key in BOARD_CONTENT_FIELDS:
        _collect_board_image_refs(
            detail.get(key),
            urls,
            paths,
            key_hint=key,
            base_url=base_url,
        )
        if len(urls) >= MAX_BOARD_IMAGE_URLS:
            return urls, paths
    _collect_board_image_refs(detail, urls, paths, base_url=base_url)
    return urls, paths


def _latest_board_image_urls(payload: Any) -> list[str]:
    urls, _ = _latest_board_image_refs(payload)
    return urls


def _latest_board_first_image_url(payload: Any) -> str | None:
    image_urls = _latest_board_image_urls(payload)
    return image_urls[0] if image_urls else None


def _latest_board_article_attributes(payload: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    article_id = _latest_board_article_id(payload)
    if article_id is not None:
        attributes["article_id"] = article_id
    content = _latest_board_content(payload)
    if content:
        attributes["content"] = content
    return attributes


def _queued_notice_active_article(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    active = payload.get("active")
    return active if isinstance(active, dict) else None


def _queued_notice_board_payload(payload: Any) -> dict[str, Any]:
    active = _queued_notice_active_article(payload)
    return {"latest": active} if active is not None else {}


def _queued_notice_title(payload: Any) -> str | None:
    return _latest_board_title(_queued_notice_board_payload(payload))


def _queued_notice_article_id(payload: Any) -> Any:
    return _latest_board_article_id(_queued_notice_board_payload(payload))


def _queued_notice_content(payload: Any) -> str | None:
    return _latest_board_content(_queued_notice_board_payload(payload))


def _queued_notice_remaining(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    pending = payload.get("pending")
    return pending if isinstance(pending, int) else None


def _queued_notice_article_attributes(payload: Any) -> dict[str, Any]:
    attributes = _latest_board_article_attributes(
        _queued_notice_board_payload(payload)
    )
    if isinstance(payload, dict):
        for key in ("pending", "delay_seconds", "queued_at", "replayed_at"):
            value = payload.get(key)
            if value is not None:
                attributes[key] = value
    image_urls = _latest_board_image_urls(_queued_notice_board_payload(payload))
    if image_urls:
        attributes["image_urls"] = image_urls
    return attributes


def _board_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    if not isinstance(payload, dict):
        return None
    listing = _board_list_payload(payload)
    total = listing.get("totalArticles")
    if isinstance(total, int):
        return total
    return len(_board_articles(payload))


def _broadcast_list_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    listing = payload.get("list")
    return listing if isinstance(listing, dict) else {}


def _broadcast_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    listing = _broadcast_list_payload(payload)
    total = listing.get("totalCount")
    if isinstance(total, int):
        return total
    return len(_list_or_empty(listing, "articleList"))


def _broadcast_latest_title(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    if isinstance(payload, dict):
        latest = payload.get("latest")
        if isinstance(latest, dict):
            title = latest.get("title")
            if isinstance(title, str) and title:
                return title
    listing = _broadcast_list_payload(payload)
    articles = _list_or_empty(listing, "articleList")
    if articles and isinstance(articles[0], dict):
        title = articles[0].get("title")
        if isinstance(title, str) and title:
            return title
    return None


def _vote_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    vote_list = payload.get("voteList")
    return vote_list if isinstance(vote_list, list) else []


def _vote_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    if not isinstance(payload, dict):
        return None
    total = payload.get("totalElements")
    if isinstance(total, int):
        return total
    return len(_vote_list(payload))


def _survey_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    survey_list = payload.get("pollList") or payload.get("surveyList")
    return survey_list if isinstance(survey_list, list) else []


def _list_or_empty(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get(key)
    return items if isinstance(items, list) else []


def _first_dict_item(payload: Any, key: str) -> dict[str, Any] | None:
    items = _list_or_empty(payload, key)
    if items and isinstance(items[0], dict):
        return items[0]
    return None


def _first_string_value(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _latest_board_title(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    latest = _latest_board_detail_payload(payload)
    if latest is None:
        return None
    title = latest.get("title")
    return title if isinstance(title, str) else None


def _latest_board_date(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    latest = _latest_board_detail_payload(payload)
    if latest is None:
        return None
    for key in ("postDateFormatted", "regDateFormatted", "postDate", "regDate"):
        value = latest.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _latest_board_status(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    latest = _latest_board_detail_payload(payload)
    if latest is None:
        return None
    status = latest.get("status")
    return status if isinstance(status, str) else None


def _aptner_notice_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    return len(_list_or_empty(payload, "noticeList"))


def _latest_aptner_notice_title(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    latest = _first_dict_item(payload, "noticeList")
    if latest is None:
        return None
    title = latest.get("title")
    return title if isinstance(title, str) else None


def _schedule_next_day(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    events = _list_or_empty(payload, "events")
    days = (event.get("day") for event in events)
    return min(
        (day for day in days if isinstance(day, int)),
        default=None,
    )


@lru_cache(maxsize=512)
def _parse_parking_history_datetime_text(value: str) -> datetime | None:
    for fmt in PARKING_HISTORY_DATETIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_parking_history_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return _parse_parking_history_datetime_text(value.strip())


def _boolean_from_any(value: Any, *, include_event_words: bool = False) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"y", "yes", "true", "1"}:
            return True
        if normalized in {"n", "no", "false", "0"}:
            return False
        if include_event_words:
            if normalized == "exit":
                return True
            if normalized == "entry":
                return False
    return None


def _guest_parking_is_resident(item: dict[str, Any]) -> bool | None:
    for key in ("isResident", "resident", "isMyCar", "myCar"):
        value = _boolean_from_any(item.get(key))
        if value is not None:
            return value
    car_type = item.get("carType")
    if isinstance(car_type, str):
        normalized = car_type.strip().upper()
        if normalized == "HOUSEHOLD":
            return True
        if normalized:
            return False
    return None


def _guest_parking_history_items(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    access_list = _list_or_empty(payload, "accessList")
    if access_list:
        for item in access_list:
            if not isinstance(item, dict):
                continue
            is_resident = _guest_parking_is_resident(item)
            if include_resident is True and is_resident is not True:
                continue
            if include_resident is False and is_resident is True:
                continue
            normalized = dict(item)
            normalized["_is_resident"] = is_resident
            normalized["inDatetime"] = item.get("inDatetime") or item.get("inDate")
            normalized["outDatetime"] = item.get("outDatetime") or item.get("outDate")
            if "isExit" not in normalized:
                normalized["isExit"] = _boolean_from_any(item.get("isOut"))
            items.append(normalized)
        return items

    for parent in _list_or_empty(payload, "monthlyParkingHistoryList"):
        if not isinstance(parent, dict):
            continue
        for item in _list_or_empty(parent, "visitCarUseHistoryReportList"):
            if not isinstance(item, dict):
                continue
            is_resident = _guest_parking_is_resident(item)
            if include_resident is True and is_resident is not True:
                continue
            if include_resident is False and is_resident is True:
                continue
            normalized = dict(item)
            normalized["_is_resident"] = is_resident
            normalized["year"] = parent.get("year")
            normalized["month"] = parent.get("month")
            normalized["month_remaining_free"] = parent.get("remainedFreeParkingCount")
            items.append(normalized)
    return items


def _guest_parking_event_timestamp(item: dict[str, Any]) -> datetime | None:
    if _guest_parking_is_exit(item) is True:
        return _parse_parking_history_datetime(item.get("outDatetime")) or _parse_parking_history_datetime(
            item.get("inDatetime")
        )
    return _parse_parking_history_datetime(item.get("inDatetime")) or _parse_parking_history_datetime(
        item.get("outDatetime")
    )


def _guest_parking_is_exit(item: dict[str, Any]) -> bool | None:
    is_exit = _boolean_from_any(item.get("isExit"), include_event_words=True)
    if is_exit is not None:
        return is_exit
    return _boolean_from_any(item.get("isOut"), include_event_words=True)


def _guest_parking_entry_timestamp(item: dict[str, Any]) -> datetime | None:
    return (
        _parse_parking_history_datetime(item.get("inDatetime"))
        or _parse_parking_history_datetime(item.get("inDateTime"))
        or _parse_parking_history_datetime(item.get("inDate"))
    )


def _guest_parking_exit_timestamp(item: dict[str, Any]) -> datetime | None:
    return (
        _parse_parking_history_datetime(item.get("outDatetime"))
        or _parse_parking_history_datetime(item.get("outDateTime"))
        or _parse_parking_history_datetime(item.get("outDate"))
    )


def _guest_parking_latest_item_by_timestamp(
    items: list[dict[str, Any]],
    timestamp_fn: Callable[[dict[str, Any]], datetime | None],
) -> dict[str, Any] | None:
    latest_item = None
    latest_key = None
    for item in items:
        timestamp = timestamp_fn(item)
        if timestamp is None:
            continue
        item_key = (timestamp, str(item.get("carNo") or ""))
        if latest_key is None or item_key > latest_key:
            latest_item = item
            latest_key = item_key
    return latest_item


def _guest_parking_latest_event_item(
    payload: Any,
    *,
    is_exit: bool,
    include_resident: bool | None = None,
) -> dict[str, Any] | None:
    items = _guest_parking_history_items(payload, include_resident=include_resident)
    if is_exit:
        return _guest_parking_latest_item_by_timestamp(items, _guest_parking_exit_timestamp)
    return _guest_parking_latest_item_by_timestamp(items, _guest_parking_entry_timestamp)


def _guest_parking_item_datetime_text(
    item: dict[str, Any],
    *keys: str,
    fallback_fn: Callable[[dict[str, Any]], datetime | None] | None = None,
) -> str | None:
    value = _first_string_value(item, *keys)
    if value is not None:
        return value
    if fallback_fn is not None:
        fallback_at = fallback_fn(item)
        if fallback_at is not None:
            return fallback_at.isoformat(sep=" ")
    event_at = _guest_parking_event_timestamp(item)
    return event_at.isoformat(sep=" ") if event_at is not None else None


def _guest_parking_latest_entry(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> dict[str, Any] | None:
    return _guest_parking_latest_event_item(
        payload,
        is_exit=False,
        include_resident=include_resident,
    )


def _guest_parking_latest_exit(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> dict[str, Any] | None:
    return _guest_parking_latest_event_item(
        payload,
        is_exit=True,
        include_resident=include_resident,
    )


def _guest_parking_latest_entry_at(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> str | None:
    latest_entry = _guest_parking_latest_entry(payload, include_resident=include_resident)
    if latest_entry is None:
        return None
    return _guest_parking_item_datetime_text(
        latest_entry,
        "inDatetime",
        "inDateTime",
        "inDate",
        "entryDatetime",
        "entryDateTime",
        fallback_fn=_guest_parking_entry_timestamp,
    )


def _guest_parking_latest_exit_at(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> str | None:
    latest_exit = _guest_parking_latest_exit(payload, include_resident=include_resident)
    if latest_exit is None:
        return None
    return _guest_parking_item_datetime_text(
        latest_exit,
        "outDatetime",
        "outDateTime",
        "outDate",
        "exitDatetime",
        "exitDateTime",
        fallback_fn=_guest_parking_exit_timestamp,
    )


def _guest_parking_latest_event(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> dict[str, Any] | None:
    items = _guest_parking_history_items(payload, include_resident=include_resident)
    if not items:
        return None
    latest_item = None
    latest_event_at = None
    latest_key = None
    for item in items:
        event_at = _guest_parking_event_timestamp(item)
        item_key = (event_at or datetime.min, str(item.get("carNo") or ""))
        if latest_key is None or item_key > latest_key:
            latest_item = item
            latest_event_at = event_at
            latest_key = item_key
    if latest_item is None:
        return None
    event_type = "exit" if _guest_parking_is_exit(latest_item) is True else "entry"
    return {
        "type": event_type,
        "at": latest_event_at.isoformat(sep=" ") if latest_event_at is not None else None,
        "item": latest_item,
    }


def _guest_parking_active_count(
    payload: Any,
    *,
    include_resident: bool | None = None,
) -> int | None:
    if _error_text(payload):
        return None
    return sum(
        1
        for item in _guest_parking_history_items(
            payload,
            include_resident=include_resident,
        )
        if _guest_parking_is_exit(item) is False
    )


def _parking_vehicle_history_payload(data: dict[str, Any]) -> Any:
    payload = data.get("parking_hub_history")
    if isinstance(payload, dict) and not _error_text(payload):
        return payload
    return data.get("guest_parking_history")


def _management_fee_value(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    if not isinstance(payload, dict):
        return None

    latest_period = payload.get("latest_period") if isinstance(payload.get("latest_period"), dict) else {}
    details = payload.get("details") if isinstance(payload.get("details"), list) else []

    candidates = [
        latest_period.get("current_fee"),
        latest_period.get("currentFee"),
    ]
    if details and isinstance(details[0], dict):
        candidates.extend(
            [
                details[0].get("currentFee"),
                details[0].get("current_fee"),
            ]
        )

    for value in candidates:
        parsed = _parse_int(value)
        if parsed is not None:
            return parsed
    return None


def _management_fee_period(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    if not isinstance(payload, dict):
        return None
    latest_period = payload.get("latest_period")
    if not isinstance(latest_period, dict):
        return None
    year = latest_period.get("year")
    month = latest_period.get("month")
    if year is None or month is None:
        return None
    return f"{year}-{month:02d}" if isinstance(month, int) else f"{year}-{month}"


def _management_fee_average_value(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    return _parse_int(_management_fee_detail(payload).get("currentFeeAverage"))


def _management_fee_periods(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    periods = payload.get("periods")
    return periods if isinstance(periods, list) else []


def _management_fee_period_item(payload: Any, index: int) -> dict[str, Any] | None:
    periods = _management_fee_periods(payload)
    if 0 <= index < len(periods) and isinstance(periods[index], dict):
        return periods[index]
    return None


def _management_fee_period_string(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    year = item.get("year")
    month = item.get("month")
    if year is None or month is None:
        return None
    month_text = f"{int(month):02d}" if isinstance(month, (int, str)) and str(month).isdigit() else str(month)
    return f"{year}-{month_text}"


def _management_fee_period_value(payload: Any, index: int) -> int | None:
    if _error_text(payload):
        return None
    item = _management_fee_period_item(payload, index)
    if item is None:
        return None
    for key in ("current_fee", "currentFee"):
        value = _parse_int(item.get(key))
        if value is not None:
            return value
    return None


def _management_fee_period_label(payload: Any, index: int) -> str | None:
    if _error_text(payload):
        return None
    return _management_fee_period_string(_management_fee_period_item(payload, index))


def _management_fee_history_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    return len(_management_fee_periods(payload))


def _management_fee_previous_value(payload: Any) -> int | None:
    return _management_fee_period_value(payload, 1)


def _management_fee_previous_period(payload: Any) -> str | None:
    return _management_fee_period_label(payload, 1)


def _management_fee_change_value(payload: Any) -> int | None:
    current_value = _management_fee_value(payload)
    previous_value = _management_fee_previous_value(payload)
    if current_value is None or previous_value is None:
        return None
    return current_value - previous_value


def _management_fee_period_details(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    period_details = payload.get("period_details")
    return period_details if isinstance(period_details, list) else []


def _management_fee_period_detail(payload: Any, index: int) -> dict[str, Any] | None:
    period_details = _management_fee_period_details(payload)
    if 0 <= index < len(period_details) and isinstance(period_details[index], dict):
        return period_details[index]
    return None


def _management_fee_detail(payload: Any, *, period_index: int = 0) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    period_detail = _management_fee_period_detail(payload, period_index)
    if isinstance(period_detail, dict):
        detail = period_detail.get("detail")
        if isinstance(detail, dict):
            return detail
    if period_index == 0:
        latest_detail = payload.get("latest_detail")
        return latest_detail if isinstance(latest_detail, dict) else {}
    if period_index == 1:
        previous_detail = payload.get("previous_detail")
        return previous_detail if isinstance(previous_detail, dict) else {}
    return {}


def _split_csv_field(value: Any) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    return [item.strip() for item in value.split(",")]


def _management_fee_breakdown(payload: Any, *, period_index: int = 0) -> dict[str, dict[str, Any]]:
    breakdown: dict[str, dict[str, Any]] = {}
    for item in _management_fee_breakdown_items(payload, period_index=period_index):
        label = item.get("label")
        if not isinstance(label, str):
            continue
        breakdown[label] = {
            "index": item.get("index"),
            "value_raw": item.get("value_raw"),
            "value": item.get("value"),
            "average_raw": item.get("average_raw"),
            "average": item.get("average"),
        }
    return breakdown


def _management_fee_breakdown_items_from_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    labels = _split_csv_field(detail.get("etcItem"))
    values = _split_csv_field(detail.get("etcContents"))
    averages = _split_csv_field(detail.get("etcItemAverage"))

    breakdown: list[dict[str, Any]] = []
    for idx, label in enumerate(labels):
        if not label:
            continue
        raw_value = values[idx] if idx < len(values) else None
        raw_average = averages[idx] if idx < len(averages) else None
        breakdown.append(
            {
                "index": idx,
                "label": label,
                "value_raw": raw_value,
                "value": _parse_int(raw_value),
                "average_raw": raw_average,
                "average": _parse_int(raw_average),
            }
        )
    return breakdown


def _management_fee_breakdown_items(payload: Any, *, period_index: int = 0) -> list[dict[str, Any]]:
    detail = _management_fee_detail(payload, period_index=period_index)
    return _management_fee_breakdown_items_from_detail(detail)


def _management_fee_breakdown_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    return len(_management_fee_breakdown_items(payload))


def _management_fee_breakdown_item(payload: Any, index: int, *, period_index: int = 0) -> dict[str, Any] | None:
    for item in _management_fee_breakdown_items(payload, period_index=period_index):
        if item.get("index") == index:
            return item
    return None


def _management_fee_money_field(payload: Any, field: str, *, period_index: int = 0) -> int | None:
    if _error_text(payload):
        return None
    detail = _management_fee_detail(payload, period_index=period_index)
    return _parse_int(detail.get(field))


def _management_fee_area_float(payload: Any, *, period_index: int = 0) -> float | None:
    if _error_text(payload):
        return None
    detail = _management_fee_detail(payload, period_index=period_index)
    return _parse_float(detail.get("area"))


def _management_fee_entity_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    return _management_fee_breakdown_items(data.get("management_fee"))


def _management_fee_entity_label(item: dict[str, Any], fallback_index: int) -> str:
    label = item.get("label")
    if isinstance(label, str) and label:
        return label
    return f"Breakdown {fallback_index + 1}"


def _management_fee_entity_index(item: dict[str, Any], fallback_index: int) -> int:
    index = item.get("index")
    return index if isinstance(index, int) else fallback_index


def _parse_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = re.sub(r"[^0-9]", "", value)
        if digits:
            return int(digits)
    return None


def _parse_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\\-]", "", value)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def _fire_status_value(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    if not isinstance(payload, dict):
        return None
    inspection = payload.get("currentInspection") or payload.get("inspection")
    if not isinstance(inspection, dict):
        return "none"
    completed = inspection.get("hasResponse")
    if completed is None:
        completed = inspection.get("isCompleted")
    return "completed" if completed else "open"


def _fire_history_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    inspections = payload.get("inspections") or payload.get("data")
    return inspections if isinstance(inspections, list) else []


def _apartment_phone_value(data: dict[str, Any]) -> str | None:
    apartment = data.get("apartment")
    if not isinstance(apartment, dict):
        return None
    tel = apartment.get("tel")
    return tel if isinstance(tel, str) else None


def _apartment_name_value(data: dict[str, Any]) -> str | None:
    apartment = data.get("apartment")
    if not isinstance(apartment, dict):
        return None
    name = apartment.get("name")
    return name if isinstance(name, str) else None


def _usage_service_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    services = _list_or_empty(payload, "services")
    return len(services)


def _primary_contact_value(payload: Any) -> str | None:
    if _error_text(payload):
        return None
    contact = _first_dict_item(payload, "contactList")
    if contact is None:
        return None
    title = contact.get("title")
    tel = contact.get("tel")
    if isinstance(title, str) and isinstance(tel, str):
        return f"{title} ({tel})"
    if isinstance(title, str):
        return title
    if isinstance(tel, str):
        return tel
    return None


def _guest_parking_household_limit(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    if not isinstance(payload, dict):
        return None
    config = payload.get("visitConfig")
    if not isinstance(config, dict):
        return None
    limit = config.get("availableHouseHoldLimit")
    return limit if isinstance(limit, int) else None


def _guest_parking_remaining_free(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    latest = _first_dict_item(payload, "monthlyParkingHistoryList")
    if latest is None:
        return None
    return _parse_int(latest.get("remainedFreeParkingCount"))


def _visit_items(payload: Any) -> list[dict[str, Any]]:
    items = _list_or_empty(payload, "Item")
    if items:
        return items
    return _list_or_empty(payload, "visitList")


def _visit_valid_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    count = 0
    for item in _visit_items(payload):
        if item.get("is_valid") == "Y":
            count += 1
    return count


def _visit_valid_items(payload: Any) -> list[dict[str, Any]]:
    if _error_text(payload):
        return []
    return [
        item
        for item in _visit_items(payload)
        if isinstance(item, dict) and item.get("is_valid") == "Y"
    ]


@lru_cache(maxsize=512)
def _parse_visit_date_text(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    compact_match = re.match(r"^(\d{4})(\d{2})(\d{2})", value)
    if compact_match:
        try:
            year = int(compact_match.group(1))
            month = int(compact_match.group(2))
            day = int(compact_match.group(3))
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _parse_visit_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    return _parse_visit_date_text(value.strip())


def _visit_next_item(payload: Any) -> dict[str, Any] | None:
    if _error_text(payload):
        return None
    items = _visit_items(payload)
    if items and isinstance(items[0], dict):
        return items[0]
    return None


def _visit_next_date(payload: Any) -> str | None:
    next_item = _visit_next_item(payload)
    if next_item is None:
        return None
    value = next_item.get("visit_date")
    return value if isinstance(value, str) else None


def _visit_next_car_no(payload: Any) -> str | None:
    next_item = _visit_next_item(payload)
    if next_item is None:
        return None
    value = next_item.get("car_no")
    return value if isinstance(value, str) else None


def _visit_next_purpose(payload: Any) -> str | None:
    next_item = _visit_next_item(payload)
    if next_item is None:
        return None
    value = next_item.get("visit_purpose")
    return value if isinstance(value, str) else None


def _visit_today_item(payload: Any) -> dict[str, Any] | None:
    today = date.today()
    for item in _visit_valid_items(payload):
        if _parse_visit_date(item.get("visit_date")) == today:
            return item
    return None


def _household_member_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    return len(_list_or_empty(payload, "householdMemberList"))


def _verified_household_member_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    members = _list_or_empty(payload, "householdMemberList")
    count = 0
    for member in members:
        if member.get("verified") or member.get("isResidenceVerified") or member.get("isOwnerVerified"):
            count += 1
    return count


def _parking_discount_history_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    return len(_list_or_empty(payload, "historyList"))


def _visit_vehicle_usage_count(payload: Any) -> int | None:
    if _error_text(payload):
        return None
    items = _list_or_empty(payload, "Item")
    if items:
        return len(items)
    return len(_list_or_empty(payload, "visitList"))


@dataclass(frozen=True, kw_only=True)
class AptnerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.translation_key is None:
            object.__setattr__(self, "translation_key", self.key)
        object.__setattr__(self, "name", None)


SENSORS: tuple[AptnerSensorDescription, ...] = (
    AptnerSensorDescription(
        key="apartment",
        name="Apartment",
        icon="mdi:office-building",
        value_fn=_apartment_name_value,
    ),
    AptnerSensorDescription(
        key="apartment_phone",
        name="Apartment Phone",
        icon="mdi:phone",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_apartment_phone_value,
    ),
    AptnerSensorDescription(
        key="usage_services",
        name="Enabled Usage Services",
        icon="mdi:apps",
        value_fn=lambda data: _usage_service_count(data.get("usage_services")),
    ),
    AptnerSensorDescription(
        key="notice_count",
        name="Notice Count",
        icon="mdi:bullhorn",
        value_fn=lambda data: _board_count(data.get("board_notice")),
    ),
    AptnerSensorDescription(
        key="latest_notice_title",
        name="Latest Notice",
        icon="mdi:text-box-outline",
        value_fn=lambda data: _latest_board_title(data.get("board_notice")),
    ),
    AptnerSensorDescription(
        key="latest_notice_date",
        name="Latest Notice Date",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _latest_board_date(data.get("board_notice")),
    ),
    AptnerSensorDescription(
        key="latest_notice_content",
        name="Latest Notice Content",
        icon="mdi:text-long",
        value_fn=lambda data: _short_text_state(_latest_board_content(data.get("board_notice"))),
        attributes_fn=lambda data: _latest_board_article_attributes(data.get("board_notice")),
    ),
    AptnerSensorDescription(
        key="queued_notice_title",
        name="Queued Notice",
        icon="mdi:bullhorn-outline",
        value_fn=lambda data: _queued_notice_title(data.get("notice_replay")),
    ),
    AptnerSensorDescription(
        key="queued_notice_article_id",
        name="Queued Notice Article ID",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _queued_notice_article_id(data.get("notice_replay")),
    ),
    AptnerSensorDescription(
        key="queued_notice_content",
        name="Queued Notice Content",
        icon="mdi:text-long",
        value_fn=lambda data: _short_text_state(
            _queued_notice_content(data.get("notice_replay"))
        ),
        attributes_fn=lambda data: _queued_notice_article_attributes(
            data.get("notice_replay")
        ),
    ),
    AptnerSensorDescription(
        key="queued_notice_remaining",
        name="Queued Notice Remaining",
        icon="mdi:format-list-numbered",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _queued_notice_remaining(data.get("notice_replay")),
    ),
    AptnerSensorDescription(
        key="community_count",
        name="Community Count",
        icon="mdi:account-group",
        value_fn=lambda data: _board_count(data.get("board_community")),
    ),
    AptnerSensorDescription(
        key="latest_community_title",
        name="Latest Community Post",
        icon="mdi:forum-outline",
        value_fn=lambda data: _latest_board_title(data.get("board_community")),
    ),
    AptnerSensorDescription(
        key="latest_community_content",
        name="Latest Community Post Content",
        icon="mdi:text-long",
        value_fn=lambda data: _short_text_state(_latest_board_content(data.get("board_community"))),
        attributes_fn=lambda data: _latest_board_article_attributes(data.get("board_community")),
    ),
    AptnerSensorDescription(
        key="complaint_count",
        name="Complaint Count",
        icon="mdi:alert-box-outline",
        value_fn=lambda data: _board_count(data.get("board_complaint")),
    ),
    AptnerSensorDescription(
        key="latest_complaint_title",
        name="Latest Complaint",
        icon="mdi:message-alert-outline",
        value_fn=lambda data: _latest_board_title(data.get("board_complaint")),
    ),
    AptnerSensorDescription(
        key="latest_complaint_status",
        name="Latest Complaint Status",
        icon="mdi:progress-clock",
        value_fn=lambda data: _latest_board_status(data.get("board_complaint")),
    ),
    AptnerSensorDescription(
        key="defect_count",
        name="Defect Count",
        icon="mdi:home-repair-service",
        value_fn=lambda data: _board_count(data.get("board_defect")),
    ),
    AptnerSensorDescription(
        key="region_count",
        name="Region Board Count",
        icon="mdi:map-marker-radius",
        value_fn=lambda data: _board_count(data.get("board_region")),
    ),
    AptnerSensorDescription(
        key="latest_region_title",
        name="Latest Region Post",
        icon="mdi:map-search-outline",
        value_fn=lambda data: _latest_board_title(data.get("board_region")),
    ),
    AptnerSensorDescription(
        key="schedule_count",
        name="Schedule Count",
        icon="mdi:calendar-month",
        value_fn=lambda data: len(_list_or_empty(data.get("schedule"), "events")) if not _error_text(data.get("schedule")) else None,
    ),
    AptnerSensorDescription(
        key="schedule_next_day",
        name="Next Schedule Day",
        icon="mdi:calendar-arrow-right",
        value_fn=lambda data: _schedule_next_day(data.get("schedule")),
    ),
    AptnerSensorDescription(
        key="vote_current_count",
        name="Current Vote Count",
        icon="mdi:vote",
        value_fn=lambda data: _vote_count(data.get("vote_current")),
    ),
    AptnerSensorDescription(
        key="vote_closed_count",
        name="Closed Vote Count",
        icon="mdi:vote-outline",
        value_fn=lambda data: _vote_count(data.get("vote_closed")),
    ),
    AptnerSensorDescription(
        key="survey_ing_count",
        name="Ongoing Survey Count",
        icon="mdi:clipboard-text-clock",
        value_fn=lambda data: len(_survey_list(data.get("survey_ing"))) if not _error_text(data.get("survey_ing")) else None,
    ),
    AptnerSensorDescription(
        key="survey_done_count",
        name="Completed Survey Count",
        icon="mdi:clipboard-check-outline",
        value_fn=lambda data: len(_survey_list(data.get("survey_done"))) if not _error_text(data.get("survey_done")) else None,
    ),
    AptnerSensorDescription(
        key="aptner_notice_count",
        name="Aptner Notice Count",
        icon="mdi:information-outline",
        value_fn=lambda data: _aptner_notice_count(data.get("aptner_notice")),
    ),
    AptnerSensorDescription(
        key="aptner_notice_latest_title",
        name="Latest Aptner Notice",
        icon="mdi:message-text-outline",
        value_fn=lambda data: _latest_aptner_notice_title(data.get("aptner_notice")),
    ),
    AptnerSensorDescription(
        key="contacts_count",
        name="Contact Count",
        icon="mdi:card-account-phone",
        value_fn=lambda data: len(_list_or_empty(data.get("contacts"), "contactList")) if not _error_text(data.get("contacts")) else None,
    ),
    AptnerSensorDescription(
        key="primary_contact",
        name="Primary Contact",
        icon="mdi:phone-in-talk",
        value_fn=lambda data: _primary_contact_value(data.get("contacts")),
    ),
    AptnerSensorDescription(
        key="broadcast_count",
        name="Apartment Broadcast Count",
        icon="mdi:bullhorn-variant",
        value_fn=lambda data: _broadcast_count(data.get("broadcast")),
    ),
    AptnerSensorDescription(
        key="broadcast_latest",
        name="Latest Apartment Broadcast",
        icon="mdi:account-voice",
        value_fn=lambda data: _broadcast_latest_title(data.get("broadcast")),
    ),
    AptnerSensorDescription(
        key="guest_parking_count",
        name="Guest Parking Count",
        icon="mdi:car-clock",
        value_fn=lambda data: (
            data.get("guest_parking", {}).get("totalReserves")
            if isinstance(data.get("guest_parking"), dict) and isinstance(data.get("guest_parking", {}).get("totalReserves"), int)
            else len(_list_or_empty(data.get("guest_parking"), "reserveList"))
            if not _error_text(data.get("guest_parking"))
            else None
        ),
    ),
    AptnerSensorDescription(
        key="guest_parking_history_count",
        name="Guest Parking History Count",
        icon="mdi:car-search",
        value_fn=lambda data: len(_list_or_empty(data.get("guest_parking_history"), "monthlyParkingHistoryList")) if not _error_text(data.get("guest_parking_history")) else None,
    ),
    AptnerSensorDescription(
        key="guest_parking_household_limit",
        name="Guest Parking Household Limit",
        icon="mdi:counter",
        value_fn=lambda data: _guest_parking_household_limit(data.get("guest_parking")),
    ),
    AptnerSensorDescription(
        key="guest_parking_remaining_free",
        name="Guest Parking Remaining Free",
        icon="mdi:ticket-percent-outline",
        value_fn=lambda data: _guest_parking_remaining_free(data.get("guest_parking_history")),
    ),
    AptnerSensorDescription(
        key="parking_discount_history_count",
        name="Parking Payment History Count",
        icon="mdi:cash-clock",
        value_fn=lambda data: _parking_discount_history_count(data.get("parking_discount_history")),
    ),
    AptnerSensorDescription(
        key="parking_vehicle_last_entry_at",
        name="Parking Last Entry Time",
        icon="mdi:car-arrow-left",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _guest_parking_latest_entry_at(
            _parking_vehicle_history_payload(data),
            include_resident=False,
        ),
    ),
    AptnerSensorDescription(
        key="parking_vehicle_last_exit_at",
        name="Parking Last Exit Time",
        icon="mdi:car-arrow-right",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _guest_parking_latest_exit_at(
            _parking_vehicle_history_payload(data),
            include_resident=False,
        ),
    ),
    AptnerSensorDescription(
        key="parking_vehicle_last_entry_at_all",
        name="Parking Last Entry Time (All Vehicles)",
        icon="mdi:car-arrow-left",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _guest_parking_latest_entry_at(
            _parking_vehicle_history_payload(data),
            include_resident=None,
        ),
    ),
    AptnerSensorDescription(
        key="parking_vehicle_last_exit_at_all",
        name="Parking Last Exit Time (All Vehicles)",
        icon="mdi:car-arrow-right",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _guest_parking_latest_exit_at(
            _parking_vehicle_history_payload(data),
            include_resident=None,
        ),
    ),
    AptnerSensorDescription(
        key="visit_vehicle_usage_count",
        name="Visit Vehicle Reservation Count",
        icon="mdi:car-multiple",
        value_fn=lambda data: _visit_vehicle_usage_count(data.get("visit_vehicle_usage")),
    ),
    AptnerSensorDescription(
        key="visit_vehicle_valid_count",
        name="Valid Visit Vehicle Reservation Count",
        icon="mdi:car-check",
        value_fn=lambda data: _visit_valid_count(data.get("visit_vehicle_usage")),
    ),
    AptnerSensorDescription(
        key="visit_vehicle_next_date",
        name="Next Visit Vehicle Date",
        icon="mdi:calendar-car",
        value_fn=lambda data: _visit_next_date(data.get("visit_vehicle_usage")),
    ),
    AptnerSensorDescription(
        key="visit_vehicle_next_car_no",
        name="Next Visit Vehicle Car Number",
        icon="mdi:car-info",
        value_fn=lambda data: _visit_next_car_no(data.get("visit_vehicle_usage")),
    ),
    AptnerSensorDescription(
        key="visit_vehicle_next_purpose",
        name="Next Visit Vehicle Purpose",
        icon="mdi:clipboard-text-outline",
        value_fn=lambda data: _visit_next_purpose(data.get("visit_vehicle_usage")),
    ),
    AptnerSensorDescription(
        key="realtor_count",
        name="Realtor Count",
        icon="mdi:home-city-outline",
        value_fn=lambda data: len(_list_or_empty(data.get("realtor"), "articles")) if not _error_text(data.get("realtor")) else None,
    ),
    AptnerSensorDescription(
        key="fire_inspection",
        name="Fire Inspection",
        icon="mdi:fire-alert",
        device_class=SensorDeviceClass.ENUM,
        options=["none", "open", "completed"],
        value_fn=lambda data: _fire_status_value(data.get("fire_inspection_status")),
    ),
    AptnerSensorDescription(
        key="fire_inspection_history_count",
        name="Fire Inspection History Count",
        icon="mdi:history",
        value_fn=lambda data: len(_fire_history_list(data.get("fire_inspection_history"))) if not _error_text(data.get("fire_inspection_history")) else None,
    ),
    AptnerSensorDescription(
        key="household_member_count",
        name="Household Member Count",
        icon="mdi:home-account",
        value_fn=lambda data: _household_member_count(data.get("household_members")),
    ),
    AptnerSensorDescription(
        key="household_verified_count",
        name="Verified Household Count",
        icon="mdi:account-check-outline",
        value_fn=lambda data: _verified_household_member_count(data.get("household_members")),
    ),
    AptnerSensorDescription(
        key="management_fee",
        name="Management Fee",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_value(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_period",
        name="Management Fee Period",
        icon="mdi:calendar-range",
        value_fn=lambda data: _management_fee_period(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_average",
        name="Management Fee Average",
        icon="mdi:chart-line",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_average_value(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_previous",
        name="Previous Management Fee",
        icon="mdi:cash-minus",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_previous_value(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_previous_period",
        name="Previous Management Fee Period",
        icon="mdi:calendar-previous",
        value_fn=lambda data: _management_fee_previous_period(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_change",
        name="Management Fee Change",
        icon="mdi:swap-vertical",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_change_value(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_history_count",
        name="Management Fee History Count",
        icon="mdi:calendar-multiple",
        value_fn=lambda data: _management_fee_history_count(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_area",
        name="Management Fee Area",
        icon="mdi:ruler-square",
        suggested_display_precision=2,
        value_fn=lambda data: _management_fee_area_float(data.get("management_fee")),
    ),
    AptnerSensorDescription(
        key="management_fee_current_late_fee",
        name="Management Fee Current Late Fee",
        icon="mdi:cash-fast",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_money_field(data.get("management_fee"), "currentLateFee"),
    ),
    AptnerSensorDescription(
        key="management_fee_delinquent_fee",
        name="Management Fee Delinquent Fee",
        icon="mdi:cash-remove",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_money_field(data.get("management_fee"), "delinquentFee"),
    ),
    AptnerSensorDescription(
        key="management_fee_delinquent_late_fee",
        name="Management Fee Delinquent Late Fee",
        icon="mdi:cash-lock",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
        value_fn=lambda data: _management_fee_money_field(data.get("management_fee"), "delinquentLateFee"),
    ),
    AptnerSensorDescription(
        key="management_fee_breakdown_count",
        name="Management Fee Breakdown Count",
        icon="mdi:format-list-bulleted",
        value_fn=lambda data: _management_fee_breakdown_count(data.get("management_fee")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AptnerDataUpdateCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    enabled_sections = set(enabled_sections_from_config(entry.data, entry.options))
    entities: list[SensorEntity] = [
        AptnerSensor(coordinator, entry, description)
        for description in SENSORS
        if _device_group_for_key(description.key) in enabled_sections
    ]
    if SECTION_BILLING in enabled_sections:
        for fallback_index, item in enumerate(_management_fee_entity_items(coordinator.data)):
            entities.append(
                AptnerFeeBreakdownSensor(
                    coordinator,
                    entry,
                    index=_management_fee_entity_index(item, fallback_index),
                    label=_management_fee_entity_label(item, fallback_index),
                )
            )
    async_add_entities(entities)


class AptnerSensor(CoordinatorEntity[AptnerDataUpdateCoordinator], SensorEntity):
    """Representation of an Aptner sensor."""

    _attr_has_entity_name = True

    entity_description: AptnerSensorDescription

    def __init__(
        self,
        coordinator: AptnerDataUpdateCoordinator,
        entry: ConfigEntry,
        description: AptnerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_icon = description.icon
        if description.key in SENSITIVE_SENSOR_KEYS_DISABLED_BY_DEFAULT:
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        attributes = self.entity_description.attributes_fn(self.coordinator.data)
        return attributes or None

    @property
    def native_unit_of_measurement(self) -> str | None:
        localized_unit = _localized_sensor_unit(
            self.coordinator.hass,
            self.entity_description.key,
        )
        if localized_unit is not None:
            return localized_unit
        return self.entity_description.native_unit_of_measurement

    @property
    def suggested_display_precision(self) -> int | None:
        if self.entity_description.key in SENSOR_PRECISION_BY_KEY:
            return SENSOR_PRECISION_BY_KEY[self.entity_description.key]
        return self.entity_description.suggested_display_precision

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info_for_key(
            self.coordinator.hass,
            self._entry.entry_id,
            self.coordinator.data,
            self.entity_description.key,
        )


class AptnerFeeBreakdownSensor(CoordinatorEntity[AptnerDataUpdateCoordinator], SensorEntity):
    """Dynamic per-line management fee sensor."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "KRW"
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:receipt-text-outline"

    def __init__(
        self,
        coordinator: AptnerDataUpdateCoordinator,
        entry: ConfigEntry,
        *,
        index: int,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._index = index
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_management_fee_breakdown_{index}"

    @property
    def native_value(self) -> int | None:
        item = _management_fee_breakdown_item(
            self.coordinator.data.get("management_fee"),
            self._index,
        )
        if item is None:
            return None
        value = item.get("value")
        return value if isinstance(value, int) else None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info_for_key(
            self.coordinator.hass,
            self._entry.entry_id,
            self.coordinator.data,
            "management_fee_breakdown",
        )
