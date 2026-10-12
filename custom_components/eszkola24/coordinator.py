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
    Test,
    subject_key,
)
from .const import (
    CAL_FUTURE,
    CAL_PAST,
    CONF_DAYS_OFF,
    CONF_EXCLUDE,
    CONF_SCAN_HOURS,
    DEFAULT_EXCLUDE,
    DEFAULT_SCAN_HOURS,
    DOMAIN,
    EVENT_NEW_GRADE,
    EVENT_NEW_HOMEWORK,
    EVENT_NEW_TEST,
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
    tests: list[Test] = field(default_factory=list)

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
        self._tests: dict[str, list[Test]] = {}

    @property
    def exclude_regex(self) -> re.Pattern | None:
        pattern = self.config_entry.options.get(CONF_EXCLUDE, DEFAULT_EXCLUDE)
        if not pattern:
            return None
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error:
            return re.compile(re.escape(pattern), re.IGNORECASE)

    def filter_lessons(self, lessons: list[Lesson], days_off: set[date] | None = None) -> list[Lesson]:
        rx = self.exclude_regex
        days_off = days_off or set()
        return [
            l
            for l in lessons
            if l.start.date() not in days_off
            and not (rx and (rx.search(l.title) or rx.search(l.subject)))
        ]

    async def async_days_off(self, start, end) -> set[date]:
        """Dni, które mają wpis w wybranym kalendarzu dni wolnych."""
        entity_id = self.config_entry.options.get(CONF_DAYS_OFF)
        if not entity_id:
            return set()
        if self.hass.states.get(entity_id) is None:
            _LOGGER.warning("Kalendarz dni wolnych %s nie istnieje", entity_id)
            return set()
        try:
            resp = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": entity_id,
                    "start_date_time": dt_util.as_local(start).isoformat(),
                    "end_date_time": dt_util.as_local(end).isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Nie udało się odczytać kalendarza dni wolnych: %s", err)
            return set()
        days: set[date] = set()
        for ev in (resp or {}).get(entity_id, {}).get("events", []):
            ev_start, ev_end = str(ev.get("start", "")), str(ev.get("end", ""))
            if len(ev_start) == 10:  # wydarzenie całodniowe; koniec wyłączny
                d0 = date.fromisoformat(ev_start)
                d1 = date.fromisoformat(ev_end) if len(ev_end) == 10 else d0 + timedelta(days=1)
            else:
                d0 = dt_util.as_local(dt_util.parse_datetime(ev_start)).date()
                last = dt_util.as_local(dt_util.parse_datetime(ev_end) - timedelta(seconds=1)).date()
                d1 = last + timedelta(days=1)
            while d0 < d1:
                days.add(d0)
                d0 += timedelta(days=1)
        return days

    @staticmethod
    def annotate_tests(lessons: list[Lesson], tests: list[Test]) -> list[Lesson]:
        """Dopisuje do lekcji zakres sprawdzianu z zakładki Sprawdziany."""
        by_day: dict[tuple, Test] = {}
        for t in tests:
            if t.day:
                by_day[(t.day, subject_key(t.subject))] = t
        used: set[tuple] = set()
        # przy kilku lekcjach tego samego przedmiotu jednego dnia zakres trafia do pierwszej
        for lesson in sorted(lessons, key=lambda l: l.start):
            for name in (lesson.subject, lesson.title):
                k = (lesson.start.date(), subject_key(name))
                t = by_day.get(k)
                if t is not None and k not in used:
                    used.add(k)
                    lesson.has_test = True
                    lesson.test_topic = t.topic or None
                    break
        return lessons

    async def async_fetch_lessons(self, child_id: str, start, end) -> list[Lesson]:
        """Pobranie planu dla dowolnego zakresu (używa kalendarz)."""
        lessons = await self.client.async_get_lessons(child_id, start, end)
        lessons = self.filter_lessons(lessons, await self.async_days_off(start, end))
        return self.annotate_tests(lessons, self._tests.get(child_id, []))

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
                try:
                    self._tests[cid] = await self.client.async_get_tests(cid)
                except ESzkolaAuthError:
                    raise
                except ESzkolaError as err:
                    _LOGGER.warning("Nie udało się pobrać sprawdzianów: %s", err)
                result[cid] = ChildData(
                    child=child,
                    lessons=await self.async_fetch_lessons(cid, start, end),
                    homework=await self.client.async_get_homework(cid, HOMEWORK_MAX_PAGES),
                    grades=await self.client.async_get_grades(cid),
                    tests=self._tests.get(cid, []),
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
            test_keys = [t.key for t in cd.tests]
            if seen is None:
                self._seen[cid] = {"grades": grade_keys, "homework": hw_ids, "tests": test_keys}
                changed = True
                continue
            if "tests" not in seen:
                # aktualizacja z wersji bez sprawdzianów – nie ogłaszamy starych
                seen["tests"] = test_keys
                changed = True
            old_grades = set(seen.get("grades", []))
            old_hw = set(seen.get("homework", []))
            old_tests = set(seen.get("tests", []))
            for t in cd.tests:
                if t.key not in old_tests:
                    self.hass.bus.async_fire(
                        EVENT_NEW_TEST,
                        {
                            "child_id": cid,
                            "child": cd.child.first_name,
                            "subject": t.subject,
                            "topic": t.topic,
                            "date": t.day.isoformat() if t.day else None,
                        },
                    )
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
            if set(grade_keys) - old_grades or set(hw_ids) - old_hw or set(test_keys) - old_tests:
                # zachowujemy też stare klucze, żeby zniknięcie z listy (stronicowanie) nie powodowało powtórek
                self._seen[cid] = {
                    "grades": sorted(old_grades | set(grade_keys)),
                    "homework": sorted(old_hw | set(hw_ids))[-500:],
                    "tests": sorted(old_tests | set(test_keys))[-500:],
                }
                changed = True
        if changed:
            await self._store.async_save(self._seen)
