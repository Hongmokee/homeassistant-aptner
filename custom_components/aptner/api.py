from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    APP_VERSION,
    DEFAULT_ENABLED_SECTIONS,
    DEFAULT_PAGE_LIMIT,
    LEGACY_BASE_URL,
    LEGACY_OS,
    SECTION_BILLING,
    SECTION_COMMUNITY,
    SECTION_HOUSEHOLD,
    SECTION_OVERVIEW,
    SECTION_PARKING,
    SECTION_SAFETY,
    SECTION_SCHEDULE,
    USER_AGENT,
    V2_BASE_URL,
)

MAX_ERROR_MESSAGE_LENGTH = 160
SENSITIVE_ERROR_FIELD_PATTERN = re.compile(
    r"(?i)([\"']?\b(?:accessToken|refreshToken|authorization|password|visitor_phone|"
    r"visitorPhone|car_no|carNo|phone|tel|mbIdx|mb_idx)\b[\"']?\s*[:=]\s*[\"']?)"
    r"[^,\"';}\s]+([\"']?)"
)


class AptnerApiError(Exception):
    """Raised when the Aptner API returns an error."""


@dataclass(slots=True)
class LegacyContext:
    """Legacy API fields only exposed by the old login response."""

    apartment_tag: str
    member_idx: str
    dong: str
    ho: str


class AptnerApiClient:
    """Thin async client built from the decompiled Android API surface."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        enabled_sections: tuple[str, ...] = DEFAULT_ENABLED_SECTIONS,
        page_limit: int = DEFAULT_PAGE_LIMIT,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._enabled_sections = set(enabled_sections)
        self._page_limit = page_limit

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._user: dict[str, Any] | None = None
        self._legacy: LegacyContext | None = None
        self._initialized = False
        self._refresh_lock = asyncio.Lock()
        self._legacy_lock = asyncio.Lock()

    @property
    def user(self) -> dict[str, Any] | None:
        """Return the last fetched v2 user payload."""
        return self._user

    async def async_initialize(self) -> None:
        """Authenticate and fetch the bootstrap user payload."""
        if self._initialized and self._access_token and self._user is not None:
            return

        tokens = await self._request_json(
            "POST",
            f"{V2_BASE_URL}/auth/token",
            headers=self._build_v2_headers(include_auth=False),
            payload={
                "id": self._username,
                "password": self._password,
                "isShowUpdateTerms": False,
            },
        )
        self._access_token = self._require_string(tokens, "accessToken")
        self._refresh_token = self._require_string(tokens, "refreshToken")
        self._user = await self._request_v2("GET", "/user/me")

        if self._needs_legacy_context():
            await self._ensure_legacy()

        self._initialized = True

    async def async_fetch_dashboard(self) -> dict[str, Any]:
        """Fetch all supported datasets used by the HA sensors."""
        await self.async_initialize()

        user = dict(self._user or {})
        apartment = user.get("apartment") or {}
        kapt_code = user.get("kaptCode")
        today = date.today()

        request_plan: list[tuple[str, asyncio.Task[Any]]] = []

        if self._section_enabled(SECTION_OVERVIEW):
            request_plan.append(
                (
                    "usage_services",
                    self._schedule_optional(
                        lambda: self._fetch_usage_services(kapt_code),
                        kapt_code,
                    ),
                )
            )

        if self._section_enabled(SECTION_COMMUNITY):
            request_plan.extend(
                (
                    (
                        "board_notice",
                        asyncio.create_task(
                            self._safe_fetch(
                                self._fetch_board_articles_with_latest("notice")
                            )
                        ),
                    ),
                    (
                        "board_community",
                        asyncio.create_task(
                            self._safe_fetch(
                                self._fetch_board_articles_with_latest("comm")
                            )
                        ),
                    ),
                    (
                        "board_complaint",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_board_articles("complaint"))
                        ),
                    ),
                    (
                        "board_defect",
                        asyncio.create_task(
                            self._safe_fetch(
                                self._safe_optional_feature(
                                    self._fetch_board_articles("as")
                                )
                            )
                        ),
                    ),
                    (
                        "board_region",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_board_articles("region"))
                        ),
                    ),
                    (
                        "aptner_notice",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_aptner_notices())
                        ),
                    ),
                    (
                        "contacts",
                        self._schedule_optional(
                            lambda: self._fetch_contacts(kapt_code),
                            kapt_code,
                        ),
                    ),
                    (
                        "broadcast",
                        asyncio.create_task(self._safe_fetch(self._fetch_broadcast())),
                    ),
                    (
                        "realtor",
                        self._schedule_optional(
                            lambda: self._fetch_realtor(kapt_code),
                            kapt_code,
                        ),
                    ),
                )
            )

        if self._section_enabled(SECTION_SCHEDULE):
            request_plan.extend(
                (
                    (
                        "schedule",
                        asyncio.create_task(
                            self._safe_fetch(
                                self._fetch_schedule(today.year, today.month)
                            )
                        ),
                    ),
                    (
                        "vote_current",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_votes("current"))
                        ),
                    ),
                    (
                        "vote_closed",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_votes("closed"))
                        ),
                    ),
                    (
                        "survey_ing",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_surveys("ing"))
                        ),
                    ),
                    (
                        "survey_done",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_surveys("done"))
                        ),
                    ),
                )
            )

        if self._section_enabled(SECTION_PARKING):
            request_plan.extend(
                (
                    (
                        "guest_parking",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_guest_parking())
                        ),
                    ),
                    (
                        "guest_parking_history",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_guest_parking_history())
                        ),
                    ),
                    (
                        "parking_hub_history",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_parking_hub_history())
                        ),
                    ),
                    (
                        "parking_discount_history",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_parking_discount_history())
                        ),
                    ),
                    (
                        "visit_vehicle_usage",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_visit_vehicle_usage())
                        ),
                    ),
                )
            )

        if self._section_enabled(SECTION_HOUSEHOLD):
            request_plan.append(
                (
                    "household_members",
                    asyncio.create_task(
                        self._safe_fetch(self._fetch_household_members())
                    ),
                )
            )

        if self._section_enabled(SECTION_SAFETY):
            request_plan.extend(
                (
                    (
                        "fire_inspection_status",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_fire_inspection_status())
                        ),
                    ),
                    (
                        "fire_inspection_history",
                        asyncio.create_task(
                            self._safe_fetch(self._fetch_fire_inspection_history())
                        ),
                    ),
                )
            )

        if self._section_enabled(SECTION_BILLING):
            request_plan.append(
                (
                    "management_fee",
                    asyncio.create_task(self._safe_fetch(self._fetch_management_fee())),
                )
            )

        results = await asyncio.gather(*(task for _, task in request_plan))

        dashboard: dict[str, Any] = {
            "apartment": apartment,
        }

        for (key, _), value in zip(request_plan, results, strict=True):
            dashboard[key] = value

        return dashboard

    def _schedule_optional(
        self,
        factory: Callable[[], Coroutine[Any, Any, Any]],
        condition: Any,
    ) -> asyncio.Task[Any]:
        if not condition:
            return asyncio.create_task(self._return_value(None))
        return asyncio.create_task(self._safe_fetch(factory()))

    async def _return_value(self, value: Any) -> Any:
        return value

    def _section_enabled(self, section: str) -> bool:
        return section in self._enabled_sections

    def _needs_legacy_context(self) -> bool:
        return bool(self._enabled_sections & {SECTION_BILLING, SECTION_PARKING})

    async def _ensure_legacy(self) -> bool:
        if self._legacy is not None:
            return True
        async with self._legacy_lock:
            if self._legacy is not None:
                return True
            try:
                await self._authenticate_legacy()
            except AptnerApiError:
                # The integration keeps working without legacy bootstrap.
                self._legacy = None
                return False
            return True

    async def _safe_fetch(self, coroutine: Any) -> Any:
        try:
            return await coroutine
        except AptnerApiError as err:
            return {"_error": self._sanitize_error_message(str(err))}
        except aiohttp.ClientError as err:
            return {"_error": self._sanitize_error_message(f"network error: {err}")}
        except OSError as err:
            return {"_error": self._sanitize_error_message(f"network error: {err}")}

    async def _safe_optional_feature(self, coroutine: Any) -> Any:
        try:
            return await coroutine
        except AptnerApiError as err:
            if str(err).startswith("404:"):
                return None
            raise

    async def _authenticate_legacy(self) -> None:
        payload = await self._request_legacy(
            "POST",
            "/user/newLogin",
            payload={
                "id": self._username,
                "password": self._password,
                "pushToken": "",
                "appVersion": APP_VERSION,
                "appOs": LEGACY_OS,
            },
            include_member_header=False,
        )
        member = payload.get("member")
        if not isinstance(member, dict):
            raise AptnerApiError("legacy login did not return member data")

        apartment_tag = self._string_or_none(member.get("aptTag"))
        member_idx = self._string_or_none(member.get("idx"))
        dong = self._string_or_none(member.get("dong"))
        ho = self._string_or_none(member.get("ho"))

        if not all([apartment_tag, member_idx, dong, ho]):
            raise AptnerApiError("legacy login returned incomplete fee context")

        self._legacy = LegacyContext(
            apartment_tag=apartment_tag,
            member_idx=member_idx,
            dong=dong,
            ho=ho,
        )

    async def _refresh_v2_token(self) -> None:
        if not self._refresh_token:
            raise AptnerApiError("missing refresh token")

        tokens = await self._request_json(
            "POST",
            f"{V2_BASE_URL}/auth/refresh",
            headers={
                **self._build_v2_headers(include_auth=False),
                "Authorization": f"Bearer {self._refresh_token}",
            },
        )
        self._access_token = self._require_string(tokens, "accessToken")
        self._refresh_token = self._require_string(tokens, "refreshToken")

    async def _request_v2(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        include_auth: bool = True,
        allow_refresh: bool = True,
    ) -> Any:
        url = f"{V2_BASE_URL}{path}"
        request_token = self._access_token
        try:
            return await self._request_json(
                method,
                url,
                headers=self._build_v2_headers(include_auth=include_auth),
                params=params,
                payload=payload,
            )
        except AptnerApiError as err:
            if not allow_refresh or not include_auth or "401" not in str(err) or not self._refresh_token:
                raise
            async with self._refresh_lock:
                if self._access_token == request_token:
                    await self._refresh_v2_token()
            return await self._request_json(
                method,
                url,
                headers=self._build_v2_headers(include_auth=include_auth),
                params=params,
                payload=payload,
            )

    async def _request_legacy(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        include_member_header: bool = True,
    ) -> Any:
        payload_data = await self._request_json(
            method,
            f"{LEGACY_BASE_URL}{path}",
            headers=self._build_legacy_headers(include_member_header=include_member_header),
            params=params,
            payload=payload,
            form=form,
        )
        if isinstance(payload_data, dict):
            result_code = payload_data.get("ResultCode")
            if result_code is not None and str(result_code) != "1":
                message = self._extract_error_message(payload_data) or "legacy API error"
                raise AptnerApiError(message)
        return payload_data

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
    ) -> Any:
        async with self._session.request(
            method,
            url,
            headers=headers,
            params=self._clean_query_params(params),
            json=self._clean_dict(payload) if form is None else None,
            data=self._clean_dict(form),
        ) as response:
            text = await response.text()
            data = self._decode_payload(text)

            if response.status >= 400:
                message = self._extract_error_message(data) or f"HTTP {response.status}"
                raise AptnerApiError(
                    self._sanitize_error_message(f"{response.status}: {message}")
                )

            return data

    async def _fetch_usage_services(self, kapt_code: str) -> Any:
        return await self._request_v2("GET", f"/apt/services/usage/{kapt_code}")

    async def _fetch_board_articles(self, board_group: str) -> Any:
        return await self._request_v2(
            "GET",
            f"/board/{board_group}/articles",
            params={"pg": 1, "limit": self._page_limit},
        )

    async def _fetch_board_articles_with_latest(self, board_group: str) -> Any:
        listing = await self._fetch_board_articles(board_group)
        latest = None
        article_id = self._latest_board_article_id(listing)
        if article_id is not None:
            try:
                latest = await self._fetch_board_article_detail(board_group, article_id)
            except (AptnerApiError, aiohttp.ClientError, OSError):
                latest = None
        return {
            "list": listing,
            "latest": latest,
        }

    async def async_fetch_board_article_detail(
        self,
        board_group: str,
        article_id: str,
    ) -> Any:
        """Fetch one board article detail payload."""
        await self.async_initialize()
        return await self._fetch_board_article_detail(board_group, article_id)

    async def _fetch_board_article_detail(self, board_group: str, article_id: str) -> Any:
        article_id_path = quote(article_id, safe="")
        paths = (
            f"/board/{board_group}/article/{article_id_path}",
            f"/board/{board_group}/articles/{article_id_path}",
            f"/board/article/{article_id_path}",
        )
        last_error: AptnerApiError | None = None
        for path in paths:
            try:
                payload = await self._request_v2("GET", path)
            except AptnerApiError as err:
                last_error = err
                if str(err).startswith("404:"):
                    continue
                raise
            return self._unwrap_common_response(payload)
        if last_error is not None:
            raise last_error
        raise AptnerApiError("missing board article detail path")

    def _latest_board_article_id(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        articles = payload.get("articleList")
        if not isinstance(articles, list) or not articles or not isinstance(articles[0], dict):
            return None
        for key in ("articleId", "id", "articleNo", "idx"):
            value = articles[0].get(key)
            article_id = self._string_or_none(value)
            if article_id:
                return article_id
        return None

    async def _fetch_schedule(self, year: int, month: int) -> Any:
        return await self._request_v2(
            "GET",
            "/board/event/calendar",
            params={"year": year, "month": month},
        )

    async def _fetch_votes(self, list_type: str) -> Any:
        return await self._request_v2(
            "GET",
            f"/vote/list/{list_type}",
            params={"pg": 1, "limit": self._page_limit},
        )

    async def _fetch_surveys(self, list_type: str) -> Any:
        return await self._request_v2(
            "GET",
            f"/poll/{list_type}/list",
            params={"pg": 1},
        )

    async def _fetch_contacts(self, kapt_code: str) -> Any:
        return await self._request_v2("GET", f"/apt/contacts/{kapt_code}")

    async def _fetch_aptner_notices(self) -> Any:
        return await self._request_v2(
            "GET",
            "/cc/notice/articles",
            params={"pg": 1},
        )

    async def _fetch_broadcast(self) -> Any:
        payload = await self._request_v2(
            "GET",
            "/tts/articles",
            params={"pg": 1, "limit": self._page_limit},
        )
        latest_article = None
        if isinstance(payload, dict):
            articles = payload.get("articleList")
            if isinstance(articles, list) and articles and isinstance(articles[0], dict):
                article_id = articles[0].get("articleId")
                if isinstance(article_id, int):
                    try:
                        latest_article = await self._request_v2("GET", f"/tts/article/{article_id}")
                    except (AptnerApiError, aiohttp.ClientError, OSError):
                        latest_article = None
        return {
            "list": payload,
            "latest": latest_article,
        }

    async def _fetch_guest_parking(self) -> Any:
        return await self._request_v2(
            "GET",
            "/pc/reserves",
            params={"pg": 1, "limit": self._page_limit},
        )

    async def _fetch_guest_parking_history(self) -> Any:
        return await self._request_v2("GET", "/pc/monthly-access-history")

    async def _fetch_parking_hub_history(self) -> Any:
        # parkinghub history uses isResident=true to include household + visitor cars.
        return await self._request_v2(
            "GET",
            "/pc/parking-hub/accesses",
            params={"isResident": True},
        )

    async def _fetch_parking_discount_history(self) -> Any:
        return await self._request_v2("GET", "/pc/sales/history")

    async def _fetch_household_members(self) -> Any:
        return await self._request_v2("GET", "/user/household/members")

    async def _fetch_visit_vehicle_usage(self) -> Any:
        if not await self._ensure_legacy():
            return {"_error": "legacy visit context unavailable"}
        return await self._request_legacy(
            "GET",
            "/visit/visit_reserve_list",
            params={"mb_idx": self._legacy.member_idx, "page": "1"},
        )

    async def _fetch_realtor(self, kapt_code: str) -> Any:
        payload = await self._request_v2(
            "GET",
            "/biz-board/realtor/articles",
            params={"page": 1, "limit": self._page_limit, "kaptCode": kapt_code},
        )
        return self._unwrap_common_response(payload)

    async def _fetch_fire_inspection_status(self) -> Any:
        payload = await self._request_v2("GET", "/utility/fire-inspections/status")
        return self._unwrap_common_response(payload)

    async def _fetch_fire_inspection_history(self) -> Any:
        payload = await self._request_v2("GET", "/utility/fire-inspections/responses")
        return self._unwrap_common_response(payload)

    async def _fetch_management_fee(self) -> Any:
        if not await self._ensure_legacy():
            return {"_error": "legacy fee context unavailable"}

        periods_payload = await self._request_legacy(
            "GET",
            "/fee/fee_check_data",
            params={
                "apt_tag": self._legacy.apartment_tag,
                "mb_dong": self._legacy.dong,
                "mb_ho": self._legacy.ho,
            },
        )
        periods = periods_payload.get("Item") if isinstance(periods_payload, dict) else None
        if not isinstance(periods, list):
            periods = []

        latest_period = periods[0] if periods else None
        period_details: list[dict[str, Any]] = []
        for period in periods[:2]:
            if not isinstance(period, dict):
                continue
            fee_id = period.get("id")
            if fee_id is None:
                continue

            detail_payload = await self._safe_fetch(
                self._request_legacy(
                    "GET",
                    "/fee/fee_list_data",
                    params={
                        "id": fee_id,
                        "apt_tag": self._legacy.apartment_tag,
                        "mb_dong": self._legacy.dong,
                        "mb_ho": self._legacy.ho,
                    },
                )
            )
            raw_details = detail_payload.get("Item") if isinstance(detail_payload, dict) else None
            details = raw_details if isinstance(raw_details, list) else []
            detail_error = (
                detail_payload.get("_error")
                if isinstance(detail_payload, dict) and isinstance(detail_payload.get("_error"), str)
                else None
            )
            period_details.append(
                {
                    "period_id": fee_id,
                    "period": period,
                    "details": details,
                    "detail": details[0] if details and isinstance(details[0], dict) else None,
                    "detail_error": detail_error,
                }
            )

        latest_period_detail = period_details[0] if period_details and isinstance(period_details[0], dict) else None
        previous_period_detail = (
            period_details[1]
            if len(period_details) > 1 and isinstance(period_details[1], dict)
            else None
        )
        latest_details = (
            latest_period_detail.get("details")
            if isinstance(latest_period_detail, dict) and isinstance(latest_period_detail.get("details"), list)
            else []
        )
        latest_detail = (
            latest_period_detail.get("detail")
            if isinstance(latest_period_detail, dict) and isinstance(latest_period_detail.get("detail"), dict)
            else None
        )
        previous_detail = (
            previous_period_detail.get("detail")
            if isinstance(previous_period_detail, dict) and isinstance(previous_period_detail.get("detail"), dict)
            else None
        )

        return {
            "latest_detail": latest_detail,
            "previous_detail": previous_detail,
            "latest_period": latest_period,
            "periods": periods,
            "details": latest_details,
            "period_details": period_details,
        }

    async def async_submit_vote(
        self,
        *,
        vote_id: int,
        selection_item_id: int | None,
        sign_file_url: str | None = None,
    ) -> None:
        await self.async_initialize()
        await self._request_v2(
            "POST",
            f"/vote/{vote_id}/sign",
            payload={
                "voteId": vote_id,
                "selectionItemId": selection_item_id,
                "signFileUrl": sign_file_url,
            },
        )

    async def async_submit_survey(
        self,
        *,
        survey_id: int,
        answers: list[dict[str, Any]],
    ) -> None:
        await self.async_initialize()
        await self._request_v2(
            "POST",
            "/poll/participate",
            payload={
                "pollId": survey_id,
                "answerList": answers,
            },
        )

    async def async_register_visit_vehicle(
        self,
        *,
        car_no: str,
        visitor_phone: str,
        visit_date: str,
        visit_purpose: str,
    ) -> Any:
        await self.async_initialize()
        if not await self._ensure_legacy():
            raise AptnerApiError("legacy visit context unavailable")
        return await self._request_legacy(
            "POST",
            "/visit/submit_visit_data",
            form={
                "mb_idx": self._legacy.member_idx,
                "car_no": car_no,
                "visitor_phone": visitor_phone,
                "visit_date": visit_date,
                "visit_purpose": visit_purpose,
            },
        )

    async def async_cancel_visit_vehicle(self, *, visit_reserve_idx: str) -> Any:
        await self.async_initialize()
        if not await self._ensure_legacy():
            raise AptnerApiError("legacy visit context unavailable")
        return await self._request_legacy(
            "GET",
            "/visit/cancel_visit_reserve",
            params={"visit_reserve_idx": visit_reserve_idx},
        )

    def _build_v2_headers(self, *, include_auth: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "x-app-version": APP_VERSION,
            "x-device-model": "Home Assistant",
            "x-os-version": "Home Assistant",
        }
        if include_auth and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _build_legacy_headers(self, *, include_member_header: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "x-app-version": APP_VERSION,
        }
        if include_member_header and self._legacy is not None:
            headers["mbIdx"] = self._legacy.member_idx
        return headers

    def _unwrap_common_response(self, payload: Any) -> Any:
        if isinstance(payload, dict) and "messageId" in payload and "data" in payload:
            return payload.get("data")
        return payload

    def _decode_payload(self, text: str) -> Any:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _extract_error_message(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("message", "msg", "ResultMessage", "type", "code"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        if isinstance(payload, str) and payload:
            return payload
        return None

    def _sanitize_error_message(self, message: str) -> str:
        sanitized = SENSITIVE_ERROR_FIELD_PATTERN.sub(
            r"\1[redacted]\2",
            message,
        )
        sanitized = " ".join(sanitized.split())
        if len(sanitized) > MAX_ERROR_MESSAGE_LENGTH:
            return f"{sanitized[:MAX_ERROR_MESSAGE_LENGTH]}..."
        return sanitized

    def _require_string(self, payload: dict[str, Any], key: str) -> str:
        value = self._string_or_none(payload.get(key))
        if value is None:
            raise AptnerApiError(f"missing field: {key}")
        return value

    def _string_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    def _clean_dict(self, data: dict[str, Any] | None) -> dict[str, Any] | None:
        if data is None:
            return None
        return {key: value for key, value in data.items() if value is not None}

    def _clean_query_params(self, data: dict[str, Any] | None) -> dict[str, Any] | None:
        if data is None:
            return None
        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, bool):
                cleaned[key] = "true" if value else "false"
                continue
            if isinstance(value, (list, tuple)):
                cleaned[key] = [
                    ("true" if item else "false") if isinstance(item, bool) else item
                    for item in value
                    if item is not None
                ]
                continue
            cleaned[key] = value
        return cleaned
