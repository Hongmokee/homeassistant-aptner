from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_CANCEL_VISIT_VEHICLE,
    SERVICE_REFRESH,
    SERVICE_REGISTER_VISIT_VEHICLE,
    SERVICE_SUBMIT_SURVEY,
    SERVICE_SUBMIT_VOTE,
)

ATTR_ENTRY_ID = "entry_id"

MAX_ENTRY_ID_LENGTH = 128
MAX_SIGN_FILE_URL_LENGTH = 2048
MAX_ANSWER_LENGTH = 1000
MAX_CAR_NO_LENGTH = 20
MAX_VISITOR_PHONE_LENGTH = 20
MAX_VISIT_PURPOSE_LENGTH = 100
MAX_VISIT_RESERVE_IDX_LENGTH = 32
MAX_SURVEY_ANSWERS = 50

POSITIVE_INT = vol.All(vol.Coerce(int), vol.Range(min=1))
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 -]{6,18}[0-9]$")
VISIT_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def _bounded_string(
    value: Any,
    *,
    field: str,
    max_length: int,
    min_length: int = 1,
) -> str:
    text = cv.string(value).strip()
    if len(text) < min_length:
        raise vol.Invalid(f"{field} is required")
    if len(text) > max_length:
        raise vol.Invalid(f"{field} is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise vol.Invalid(f"{field} contains control characters")
    return text


def _entry_id(value: Any) -> str:
    return _bounded_string(
        value,
        field=ATTR_ENTRY_ID,
        max_length=MAX_ENTRY_ID_LENGTH,
    )


def _https_url(value: Any) -> str:
    text = _bounded_string(
        value,
        field="sign_file_url",
        max_length=MAX_SIGN_FILE_URL_LENGTH,
    )
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise vol.Invalid("sign_file_url must be an HTTPS URL")
    return text


def _answer_text(value: Any) -> str:
    return _bounded_string(value, field="answer", max_length=MAX_ANSWER_LENGTH)


def _car_no(value: Any) -> str:
    return _bounded_string(
        value,
        field="car_no",
        max_length=MAX_CAR_NO_LENGTH,
        min_length=2,
    )


def _visitor_phone(value: Any) -> str:
    text = _bounded_string(
        value,
        field="visitor_phone",
        max_length=MAX_VISITOR_PHONE_LENGTH,
    )
    if PHONE_PATTERN.fullmatch(text) is None:
        raise vol.Invalid("visitor_phone has an invalid format")
    return text


def _visit_date(value: Any) -> str:
    text = _bounded_string(value, field="visit_date", max_length=19)
    for date_format in VISIT_DATE_FORMATS:
        try:
            datetime.strptime(text, date_format)
        except ValueError:
            continue
        return text
    raise vol.Invalid("visit_date has an invalid format")


def _visit_purpose(value: Any) -> str:
    return _bounded_string(
        value,
        field="visit_purpose",
        max_length=MAX_VISIT_PURPOSE_LENGTH,
    )


def _visit_reserve_idx(value: Any) -> str:
    text = _bounded_string(
        value,
        field="visit_reserve_idx",
        max_length=MAX_VISIT_RESERVE_IDX_LENGTH,
    )
    if not text.isdecimal():
        raise vol.Invalid("visit_reserve_idx must be numeric")
    return text


SERVICE_BASE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): _entry_id})

SERVICE_REFRESH_SCHEMA = SERVICE_BASE_SCHEMA

SERVICE_SUBMIT_VOTE_SCHEMA = SERVICE_BASE_SCHEMA.extend(
    {
        vol.Required("vote_id"): POSITIVE_INT,
        vol.Optional("selection_item_id"): POSITIVE_INT,
        vol.Optional("sign_file_url"): _https_url,
    }
)

SERVICE_SUBMIT_SURVEY_SCHEMA = SERVICE_BASE_SCHEMA.extend(
    {
        vol.Required("survey_id"): POSITIVE_INT,
        vol.Required("answers"): vol.All(
            [
                vol.Schema(
                    {
                        vol.Required("question_id"): POSITIVE_INT,
                        vol.Required("answer"): _answer_text,
                    }
                )
            ],
            vol.Length(min=1, max=MAX_SURVEY_ANSWERS),
        ),
    }
)

SERVICE_REGISTER_VISIT_VEHICLE_SCHEMA = SERVICE_BASE_SCHEMA.extend(
    {
        vol.Required("car_no"): _car_no,
        vol.Required("visitor_phone"): _visitor_phone,
        vol.Required("visit_date"): _visit_date,
        vol.Required("visit_purpose"): _visit_purpose,
    }
)

SERVICE_CANCEL_VISIT_VEHICLE_SCHEMA = SERVICE_BASE_SCHEMA.extend(
    {
        vol.Required("visit_reserve_idx"): _visit_reserve_idx,
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str | None):
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    if not entries:
        raise HomeAssistantError("No Aptner entries are configured.")
    if entry_id is not None:
        coordinator = entries.get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"Unknown Aptner entry_id: {entry_id}")
        return coordinator
    if len(entries) > 1:
        raise HomeAssistantError(
            "Multiple Aptner entries are configured; entry_id is required."
        )
    return next(iter(entries.values()))


async def async_register_services(hass: HomeAssistant) -> None:
    """Register Aptner services."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("services_registered"):
        return

    async def handle_refresh(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
        await coordinator.async_request_refresh()

    async def handle_submit_vote(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
        await coordinator.client.async_submit_vote(
            vote_id=call.data["vote_id"],
            selection_item_id=call.data.get("selection_item_id"),
            sign_file_url=call.data.get("sign_file_url"),
        )
        await coordinator.async_request_refresh()

    async def handle_submit_survey(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
        answers = [
            {
                "questionId": answer["question_id"],
                "answer": answer["answer"],
            }
            for answer in call.data["answers"]
        ]
        await coordinator.client.async_submit_survey(
            survey_id=call.data["survey_id"],
            answers=answers,
        )
        await coordinator.async_request_refresh()

    async def handle_register_visit_vehicle(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
        await coordinator.client.async_register_visit_vehicle(
            car_no=call.data["car_no"],
            visitor_phone=call.data["visitor_phone"],
            visit_date=call.data["visit_date"],
            visit_purpose=call.data["visit_purpose"],
        )
        await coordinator.async_request_refresh()

    async def handle_cancel_visit_vehicle(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
        await coordinator.client.async_cancel_visit_vehicle(
            visit_reserve_idx=call.data["visit_reserve_idx"],
        )
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        handle_refresh,
        schema=SERVICE_REFRESH_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_VOTE,
        handle_submit_vote,
        schema=SERVICE_SUBMIT_VOTE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_SURVEY,
        handle_submit_survey,
        schema=SERVICE_SUBMIT_SURVEY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REGISTER_VISIT_VEHICLE,
        handle_register_visit_vehicle,
        schema=SERVICE_REGISTER_VISIT_VEHICLE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_VISIT_VEHICLE,
        handle_cancel_visit_vehicle,
        schema=SERVICE_CANCEL_VISIT_VEHICLE_SCHEMA,
    )
    domain_data["services_registered"] = True


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister Aptner services when the last entry is removed."""
    domain_data = hass.data.get(DOMAIN, {})
    if domain_data.get("entries"):
        return
    if not domain_data.get("services_registered"):
        return

    for service in (
        SERVICE_REFRESH,
        SERVICE_SUBMIT_VOTE,
        SERVICE_SUBMIT_SURVEY,
        SERVICE_REGISTER_VISIT_VEHICLE,
        SERVICE_CANCEL_VISIT_VEHICLE,
    ):
        hass.services.async_remove(DOMAIN, service)
    domain_data["services_registered"] = False
