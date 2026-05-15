from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "aptner"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.IMAGE]

APP_VERSION = "2.21.50"
LEGACY_OS = "android"
USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36"

V2_BASE_URL = "https://v2.aptner.com"
LEGACY_BASE_URL = "https://api.aptner.com/app"

CONF_PAGE_LIMIT = "page_limit"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_ENABLED_SECTIONS = "enabled_sections"

SECTION_OVERVIEW = "overview"
SECTION_BILLING = "billing"
SECTION_HOUSEHOLD = "household"
SECTION_SAFETY = "safety"
SECTION_SCHEDULE = "schedule"
SECTION_PARKING = "parking"
SECTION_COMMUNITY = "community"

ALL_SECTIONS: tuple[str, ...] = (
    SECTION_OVERVIEW,
    SECTION_BILLING,
    SECTION_HOUSEHOLD,
    SECTION_SAFETY,
    SECTION_SCHEDULE,
    SECTION_PARKING,
    SECTION_COMMUNITY,
)
DEFAULT_ENABLED_SECTIONS = ALL_SECTIONS

DEFAULT_PAGE_LIMIT = 20
DEFAULT_SCAN_INTERVAL_MINUTES = 15
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 50

MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 60

SERVICE_REFRESH = "refresh_data"
SERVICE_SUBMIT_SURVEY = "submit_survey"
SERVICE_SUBMIT_VOTE = "submit_vote"
SERVICE_REGISTER_VISIT_VEHICLE = "register_visit_vehicle"
SERVICE_CANCEL_VISIT_VEHICLE = "cancel_visit_vehicle"
SERVICE_SET_NOTICE_OCR_CONTENT = "set_notice_ocr_content"


def normalize_enabled_sections(value: object) -> tuple[str, ...]:
    """Return a stable, known-only enabled section tuple."""
    if value is None:
        return DEFAULT_ENABLED_SECTIONS
    if isinstance(value, str):
        values = {value}
    elif isinstance(value, (list, tuple, set)):
        values = {item for item in value if isinstance(item, str)}
    else:
        return DEFAULT_ENABLED_SECTIONS
    values.add(SECTION_OVERVIEW)
    return tuple(section for section in ALL_SECTIONS if section in values)


def enabled_sections_from_config(
    data: dict[str, object],
    options: dict[str, object],
) -> tuple[str, ...]:
    """Read enabled sections from options, falling back for older entries."""
    return normalize_enabled_sections(
        options.get(CONF_ENABLED_SECTIONS, data.get(CONF_ENABLED_SECTIONS))
    )
