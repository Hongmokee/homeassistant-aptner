from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AptnerApiClient, AptnerApiError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

NOTICE_BOARD_GROUP = "notice"
NOTICE_BOARD_PAYLOAD_KEY = "board_notice"
NOTICE_REPLAY_PAYLOAD_KEY = "notice_replay"
NOTICE_REPLAY_DELAY = timedelta(minutes=2)
NOTICE_REPLAY_STORAGE_KEY = f"{DOMAIN}_notice_replay"
NOTICE_REPLAY_STORAGE_VERSION = 1


class AptnerDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Aptner API polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AptnerApiClient,
        *,
        entry_id: str,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            logger=hass.data.get(DOMAIN, {}).get("logger", _LOGGER),
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self._store = Store(
            hass,
            NOTICE_REPLAY_STORAGE_VERSION,
            f"{NOTICE_REPLAY_STORAGE_KEY}_{entry_id}",
        )
        self._notice_replay_state_loaded = False
        self._notice_replay_initialized = False
        self._last_seen_notice_article_id: str | None = None
        self._notice_replay_queue: deque[dict[str, Any]] = deque()
        self._notice_replay_active: dict[str, Any] | None = None
        self._notice_replay_task: asyncio.Task[None] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.async_fetch_dashboard()
        except (AptnerApiError, aiohttp.ClientError, OSError) as err:
            raise UpdateFailed(str(err)) from err

        try:
            await self._async_queue_new_notice_articles(data)
        except (AptnerApiError, aiohttp.ClientError, OSError) as err:
            _LOGGER.warning("Failed to queue new Aptner notices: %s", err)
        data[NOTICE_REPLAY_PAYLOAD_KEY] = self._notice_replay_payload()
        return data

    def async_shutdown(self) -> None:
        """Cancel background tasks owned by the coordinator."""
        if self._notice_replay_task is not None:
            self._notice_replay_task.cancel()
            self._notice_replay_task = None

    async def _async_queue_new_notice_articles(self, data: dict[str, Any]) -> None:
        await self._async_load_notice_replay_state()
        notice_payload = data.get(NOTICE_BOARD_PAYLOAD_KEY)
        articles = _board_articles(notice_payload)
        if not articles:
            return

        latest_article_id = _board_article_id(articles[0])
        if latest_article_id is None:
            return

        if not self._notice_replay_initialized:
            self._last_seen_notice_article_id = latest_article_id
            self._notice_replay_initialized = True
            await self._async_save_notice_replay_state()
            return

        if latest_article_id == self._last_seen_notice_article_id:
            return

        new_articles: list[dict[str, Any]] = []
        for article in articles:
            article_id = _board_article_id(article)
            if article_id is None:
                continue
            if article_id == self._last_seen_notice_article_id:
                break
            new_articles.append(article)

        self._last_seen_notice_article_id = latest_article_id
        await self._async_save_notice_replay_state()
        if not new_articles:
            return

        replay_articles: list[dict[str, Any]] = []
        for article in reversed(new_articles):
            replay_articles.append(
                await self._async_notice_article_for_replay(article, notice_payload)
            )
        queued_count = self._enqueue_notice_replay_articles(replay_articles)
        if queued_count == 0:
            await self._async_save_notice_replay_state()
        _LOGGER.debug(
            "Queued %s new Aptner notices for replay",
            queued_count,
        )

    async def _async_notice_article_for_replay(
        self,
        article: dict[str, Any],
        notice_payload: Any,
    ) -> dict[str, Any]:
        article_id = _board_article_id(article)
        latest_detail = _latest_board_detail_payload(notice_payload)
        if (
            article_id is not None
            and latest_detail is not None
            and _board_article_id(latest_detail) == article_id
        ):
            return {**article, **latest_detail}

        if article_id is None:
            return dict(article)

        try:
            detail = await self.client.async_fetch_board_article_detail(
                NOTICE_BOARD_GROUP,
                article_id,
            )
        except (AptnerApiError, aiohttp.ClientError, OSError) as err:
            _LOGGER.debug(
                "Failed to fetch Aptner notice detail for replay article %s: %s",
                article_id,
                err,
            )
            return dict(article)

        detail_payload = _unwrap_board_detail_payload(detail)
        if detail_payload is None:
            return dict(article)
        return {**article, **detail_payload}

    async def _async_load_notice_replay_state(self) -> None:
        if self._notice_replay_state_loaded:
            return

        stored = await self._store.async_load()
        if isinstance(stored, dict):
            article_id = _string_or_none(stored.get("last_seen_notice_article_id"))
            if article_id is not None:
                self._last_seen_notice_article_id = article_id
                self._notice_replay_initialized = True
        self._notice_replay_state_loaded = True

    async def _async_save_notice_replay_state(self) -> None:
        await self._store.async_save(
            {
                "last_seen_notice_article_id": self._last_seen_notice_article_id,
            }
        )

    def _enqueue_notice_replay_articles(self, articles: list[dict[str, Any]]) -> int:
        existing_ids = {
            article_id
            for article_id in (
                _replay_item_article_id(self._notice_replay_active),
                *(
                    _replay_item_article_id(item)
                    for item in self._notice_replay_queue
                ),
            )
            if article_id is not None
        }
        queued_at = _utcnow_iso()
        queued_count = 0

        for article in articles:
            article_id = _board_article_id(article)
            if article_id is not None and article_id in existing_ids:
                continue
            if article_id is not None:
                existing_ids.add(article_id)
            self._notice_replay_queue.append(
                {
                    "article": article,
                    "queued_at": queued_at,
                }
            )
            queued_count += 1

        if self._notice_replay_queue and (
            self._notice_replay_task is None or self._notice_replay_task.done()
        ):
            self._notice_replay_task = self.hass.async_create_task(
                self._async_drain_notice_replay_queue()
            )
        return queued_count

    async def _async_drain_notice_replay_queue(self) -> None:
        try:
            while self._notice_replay_queue:
                self._notice_replay_active = self._notice_replay_queue.popleft()
                self._notice_replay_active["replayed_at"] = _utcnow_iso()
                _LOGGER.debug(
                    "Replaying Aptner notice %s; %s notices remain queued",
                    _replay_item_article_id(self._notice_replay_active),
                    len(self._notice_replay_queue),
                )
                self._publish_notice_replay_payload()
                await asyncio.sleep(NOTICE_REPLAY_DELAY.total_seconds())
        except asyncio.CancelledError:
            raise
        else:
            await self._async_save_notice_replay_state()
        finally:
            self._notice_replay_task = None

    def _publish_notice_replay_payload(self) -> None:
        if self.data is None:
            return
        data = dict(self.data)
        data[NOTICE_REPLAY_PAYLOAD_KEY] = self._notice_replay_payload()
        self.async_set_updated_data(data)

    def _notice_replay_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pending": len(self._notice_replay_queue),
            "delay_seconds": int(NOTICE_REPLAY_DELAY.total_seconds()),
        }
        if self._notice_replay_active is None:
            return payload

        article = self._notice_replay_active.get("article")
        if isinstance(article, dict):
            payload["active"] = article
            article_id = _board_article_id(article)
            if article_id is not None:
                payload["article_id"] = article_id

        for key in ("queued_at", "replayed_at"):
            value = self._notice_replay_active.get(key)
            if isinstance(value, str):
                payload[key] = value
        return payload


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _board_articles(payload: Any) -> list[dict[str, Any]]:
    listing = _board_list_payload(payload)
    articles = listing.get("articleList")
    return [
        article
        for article in articles
        if isinstance(article, dict)
    ] if isinstance(articles, list) else []


def _board_list_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    listing = payload.get("list")
    if isinstance(listing, dict):
        return listing
    return payload


def _board_article_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("articleId", "id", "articleNo", "idx"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


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
        return _unwrap_board_detail_payload(latest)
    return None


def _replay_item_article_id(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    article = item.get("article")
    return _board_article_id(article)
