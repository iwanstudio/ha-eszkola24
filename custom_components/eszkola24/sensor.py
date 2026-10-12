"""Czujniki: prace domowe, oceny, najbliższy sprawdzian."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ESzkolaConfigEntry
from .entity import ESzkolaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ESzkolaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for cd in coordinator.data.values():
        entities += [
            HomeworkSensor(coordinator, cd.child, "homework"),
            LatestGradeSensor(coordinator, cd.child, "latest_grade"),
            GradesSensor(coordinator, cd.child, "grades"),
            NextTestSensor(coordinator, cd.child, "next_test"),
        ]
    async_add_entities(entities)


class HomeworkSensor(ESzkolaEntity, SensorEntity):
    """Stan: liczba prac domowych na dziś i kolejne dni."""

    _attr_name = "Prace domowe"
    _attr_icon = "mdi:bag-personal"
    _attr_native_unit_of_measurement = "szt."

    def _upcoming(self):
        cd = self.child_data
        if not cd:
            return []
        today = dt_util.now().date()
        return sorted(
            (h for h in cd.homework if h.day and h.day >= today), key=lambda h: h.day
        )

    @property
    def native_value(self) -> int | None:
        return len(self._upcoming()) if self.child_data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cd = self.child_data
        if not cd:
            return {}

        def as_dict(h):
            return {
                "data": h.day.isoformat() if h.day else None,
                "przedmiot": h.subject,
                "temat": h.topic,
                "nauczyciel": h.teacher,
                "potwierdzone": h.confirmed,
            }

        return {
            "nadchodzace": [as_dict(h) for h in self._upcoming()],
            "ostatnie": [as_dict(h) for h in cd.homework[:5]],
        }


class LatestGradeSensor(ESzkolaEntity, SensorEntity):
    _attr_name = "Ostatnia ocena"
    _attr_icon = "mdi:star-circle"

    def _latest(self):
        cd = self.child_data
        grades = cd.all_grades if cd else []
        return grades[-1] if grades else None

    @property
    def native_value(self) -> str | None:
        g = self._latest()
        return g.value if g else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        g = self._latest()
        if not g:
            return {}
        return {
            "przedmiot": g.subject,
            "opis": g.description,
            "data": g.created.isoformat() if g.created else None,
            "waga": g.weight,
        }


class GradesSensor(ESzkolaEntity, SensorEntity):
    """Stan: średnia ze wszystkich przedmiotów; atrybuty: oceny per przedmiot."""

    _attr_name = "Oceny"
    _attr_icon = "mdi:school-outline"

    @property
    def native_value(self) -> float | None:
        cd = self.child_data
        if not cd:
            return None
        avgs = [s.average for s in cd.grades if s.average is not None]
        return round(sum(avgs) / len(avgs), 2) if avgs else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cd = self.child_data
        if not cd:
            return {}
        return {
            "przedmioty": {
                s.subject: {
                    "srednia": s.average,
                    "oceny": [g.value for g in s.grades],
                    "semestr": s.semester,
                    "proponowana": s.proposed,
                    "koncowa": s.final,
                }
                for s in cd.grades
            },
            "liczba_ocen": sum(len(s.grades) for s in cd.grades),
        }


class NextTestSensor(ESzkolaEntity, SensorEntity):
    """Najbliższa lekcja z zapowiedzianym sprawdzianem."""

    _attr_name = "Najbliższy sprawdzian"
    _attr_icon = "mdi:file-document-edit-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _next(self):
        cd = self.child_data
        if not cd:
            return None
        now = dt_util.now()
        return next((l for l in cd.lessons if l.has_test and l.end > now), None)

    @property
    def native_value(self) -> datetime | None:
        nxt = self._next()
        return nxt.start if nxt else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        nxt = self._next()
        return {"przedmiot": nxt.subject, "sala": nxt.place} if nxt else {}
