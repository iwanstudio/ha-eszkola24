"""Kalendarz z planem lekcji (z zastępstwami i sprawdzianami)."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ESzkolaConfigEntry
from .api import ESzkolaError, Lesson
from .const import CAL_FUTURE, CAL_PAST
from .entity import ESzkolaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ESzkolaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ESzkolaCalendar(coordinator, cd.child) for cd in coordinator.data.values()
    )


def lesson_to_event(lesson: Lesson) -> CalendarEvent:
    summary = lesson.subject
    tags = []
    if lesson.is_substitute:
        tags.append("zastępstwo")
    if lesson.has_test:
        tags.append("sprawdzian")
    if tags:
        summary = f"{summary} ({', '.join(tags)})"
    place = lesson.place
    if place and place.isdigit():
        place = f"sala {place}"
    description_parts = [lesson.title]
    if lesson.is_substitute:
        description_parts.append("Zastępstwo")
    if lesson.has_test:
        description_parts.append("Zapowiedziany sprawdzian")
    return CalendarEvent(
        start=lesson.start,
        end=lesson.end,
        summary=summary,
        location=place,
        description="\n".join(description_parts),
        uid=lesson.uid,
    )


class ESzkolaCalendar(ESzkolaEntity, CalendarEntity):
    _attr_name = "Plan lekcji"
    _attr_icon = "mdi:school"

    def __init__(self, coordinator, child) -> None:
        super().__init__(coordinator, child, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        cd = self.child_data
        if not cd:
            return None
        now = dt_util.now()
        for lesson in cd.lessons:
            if lesson.end > now:
                return lesson_to_event(lesson)
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        cd = self.child_data
        now = dt_util.now()
        cached_from = (now - CAL_PAST).replace(hour=0, minute=0, second=0, microsecond=0)
        cached_to = (now + CAL_FUTURE).replace(hour=0, minute=0, second=0, microsecond=0)
        if cd and start_date >= cached_from and end_date <= cached_to:
            lessons = cd.lessons
        else:
            try:
                lessons = await self.coordinator.async_fetch_lessons(
                    self.child.child_id, start_date, end_date
                )
            except ESzkolaError as err:
                _LOGGER.warning("Nie udało się pobrać planu lekcji: %s", err)
                lessons = cd.lessons if cd else []
        return [
            lesson_to_event(l)
            for l in lessons
            if l.end > start_date and l.start < end_date
        ]
