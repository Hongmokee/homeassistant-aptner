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
