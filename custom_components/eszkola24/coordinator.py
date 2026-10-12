"""Koordynator odświeżania danych eSzkoła24."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Child,
    ESzkolaAuthError,
    ESzkolaClient,
    ESzkolaError,
    Grade,
    Homework,
    Lesson,
    SubjectGrades,
)
from .const import (
    CAL_FUTURE,
    CAL_PAST,
    CONF_EXCLUDE,
    CONF_SCAN_HOURS,
    DEFAULT_EXCLUDE,
    DEFAULT_SCAN_HOURS,
    DOMAIN,
    EVENT_NEW_GRADE,
    EVENT_NEW_HOMEWORK,
    HOMEWORK_MAX_PAGES,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ChildData:
    child: Child
    lessons: list[Lesson] = field(default_factory=list)
    homework: list[Homework] = field(default_factory=list)
    grades: list[SubjectGrades] = field(default_factory=list)

    @property
    def all_grades(self) -> list[Grade]:
        out = [g for s in self.grades for g in s.grades]
        return sorted(out, key=lambda g: g.created or date.min)


class ESzkolaCoordinator(DataUpdateCoordinator[dict[str, ChildData]]):
    """Pobiera plan, prace domowe i oceny dla wszystkich dzieci."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ESzkolaClient) -> None:
        hours = entry.options.get(CONF_SCAN_HOURS, DEFAULT_SCAN_HOURS)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=hours),
        )
        self.client = client
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._seen: dict[str, dict[str, list[str]]] | None = None

    @property
    def exclude_regex(self) -> re.Pattern | None:
        pattern = self.config_entry.options.get(CONF_EXCLUDE, DEFAULT_EXCLUDE)
        if not pattern:
            return None
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error:
            return re.compile(re.escape(pattern), re.IGNORECASE)

    def filter_lessons(self, lessons: list[Lesson]) -> list[Lesson]:
        rx = self.exclude_regex
        if rx is None:
            return lessons
        return [l for l in lessons if not (rx.search(l.title) or rx.search(l.subject))]

    async def async_fetch_lessons(self, child_id: str, start, end) -> list[Lesson]:
        """Pobranie planu dla dowolnego zakresu (używa kalendarz)."""
        lessons = await self.client.async_get_lessons(child_id, start, end)
        return self.filter_lessons(lessons)

    async def _async_update_data(self) -> dict[str, ChildData]:
        if self._seen is None:
            self._seen = await self._store.async_load() or {}

        try:
            if not self.client.children:
                await self.client.async_login()
            now = dt_util.now()
            start = (now - CAL_PAST).replace(hour=0, minute=0, second=0, microsecond=0)
            end = (now + CAL_FUTURE).replace(hour=0, minute=0, second=0, microsecond=0)
            result: dict[str, ChildData] = {}
            for child in self.client.children:
                cid = child.child_id
                result[cid] = ChildData(
                    child=child,
                    lessons=await self.async_fetch_lessons(cid, start, end),
                    homework=await self.client.async_get_homework(cid, HOMEWORK_MAX_PAGES),
                    grades=await self.client.async_get_grades(cid),
                )
        except ESzkolaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ESzkolaError as err:
            raise UpdateFailed(str(err)) from err

        await self._announce_new(result)
        return result

    async def _announce_new(self, data: dict[str, ChildData]) -> None:
        """Zdarzenia o nowych ocenach i pracach domowych (pomija pierwsze uruchomienie)."""
        assert self._seen is not None
        changed = False
        for cid, cd in data.items():
            seen = self._seen.get(cid)
            grade_keys = [g.key for g in cd.all_grades]
            hw_ids = [h.hw_id for h in cd.homework]
            if seen is None:
                self._seen[cid] = {"grades": grade_keys, "homework": hw_ids}
                changed = True
                continue
            old_grades = set(seen.get("grades", []))
            old_hw = set(seen.get("homework", []))
            for g in cd.all_grades:
                if g.key not in old_grades:
                    self.hass.bus.async_fire(
                        EVENT_NEW_GRADE,
                        {
                            "child_id": cid,
                            "child": cd.child.first_name,
                            "subject": g.subject,
                            "grade": g.value,
                            "description": g.description,
                            "date": g.created.isoformat() if g.created else None,
                        },
                    )
            for h in cd.homework:
                if h.hw_id not in old_hw:
                    self.hass.bus.async_fire(
                        EVENT_NEW_HOMEWORK,
                        {
                            "child_id": cid,
                            "child": cd.child.first_name,
                            "subject": h.subject,
                            "topic": h.topic,
                            "teacher": h.teacher,
                            "date": h.day.isoformat() if h.day else None,
                        },
                    )
            if set(grade_keys) != old_grades or set(hw_ids) != old_hw:
                # zachowujemy też stare klucze, żeby zniknięcie z listy (stronicowanie) nie powodowało powtórek
                self._seen[cid] = {
                    "grades": sorted(old_grades | set(grade_keys)),
                    "homework": sorted(old_hw | set(hw_ids))[-500:],
                }
                changed = True
        if changed:
            await self._store.async_save(self._seen)
