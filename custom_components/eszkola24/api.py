"""Klient panelu rodzica eSzkoła24 (logowanie jak w przeglądarce).

Moduł nie zależy od Home Assistanta, żeby dało się go testować osobno.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
EVENT_TYPES = '["event-lesson","event-group","event-user"]'


class ESzkolaError(Exception):
    """Ogólny błąd komunikacji."""


class ESzkolaAuthError(ESzkolaError):
    """Błędny login/hasło albo nie udało się zalogować."""


@dataclass
class Child:
    child_id: str
    name: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else self.child_id


@dataclass
class Lesson:
    uid: str
    title: str
    subject: str
    start: datetime
    end: datetime
    place: str | None
    has_test: bool
    is_substitute: bool
    kind: str


@dataclass
class Homework:
    hw_id: str
    day: date | None
    topic: str
    subject: str
    teacher: str
    attachments: int
    confirmed: bool


@dataclass
class Grade:
    subject: str
    value: str
    description: str
    created: date | None
    weight: str | None = None

    @property
    def key(self) -> str:
        created = self.created.isoformat() if self.created else ""
        return f"{self.subject}|{self.value}|{self.description}|{created}"


@dataclass
class SubjectGrades:
    subject: str
    grades: list[Grade] = field(default_factory=list)
    average: float | None = None
    semester: str | None = None
    proposed: str | None = None
    final: str | None = None


# --------------------------------------------------------------------------
# Parsery (czyste funkcje)
# --------------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(el: Any) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_csrf_token(html: str) -> str | None:
    el = _soup(html).find("input", attrs={"name": "_csrf_token"})
    return el.get("value") if el else None


def is_login_page(html: str) -> bool:
    soup = _soup(html)
    return bool(
        soup.find("input", attrs={"name": "password"})
        and soup.find("input", attrs={"name": "_csrf_token"})
    )


def parse_children(html: str) -> list[Child]:
    """Lista dzieci z przełącznika w nagłówku panelu."""
    soup = _soup(html)
    for select in soup.find_all("select"):
        options = select.find_all("option")
        values = [(o.get("value") or "").strip() for o in options]
        if options and all(v.isdigit() for v in values):
            return [Child(v, _text(o)) for v, o in zip(values, options)]
    return []


def _parse_dt(value: str, tz: tzinfo) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19] if "T" in value else value, fmt).replace(
                tzinfo=tz
            )
        except ValueError:
            continue
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def _strip_class(title: str, subject: str | None) -> str:
    """'Vb język polski' -> 'Język polski'; 'J.angielski_5b_gr.1' zostaje czytelne."""
    name = subject or title
    if not subject:
        name = re.sub(r"^[IVX]+[a-z]\s+", "", title)
    name = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    return name[:1].upper() + name[1:] if name else title.strip()


def parse_events(data: list[dict[str, Any]], tz: tzinfo) -> list[Lesson]:
    lessons: list[Lesson] = []
    for item in data:
        props = item.get("extendedProps") or {}
        title = str(item.get("title") or "").strip()
        subject_raw = props.get("subject")
        # Dla zajęć grupowych subject bywa pusty – wtedy bierzemy tytuł
        subject = _strip_class(title, subject_raw if subject_raw else None)
        try:
            start = _parse_dt(str(item["start"]), tz)
            end = _parse_dt(str(item.get("end") or item["start"]), tz)
        except (KeyError, ValueError):
            _LOGGER.debug("Pomijam wydarzenie bez poprawnej daty: %s", item)
            continue
        place = props.get("eventPlace")
        lessons.append(
            Lesson(
                uid=str(item.get("id") or f"{title}-{start.isoformat()}"),
                title=title,
                subject=subject,
                start=start,
                end=end,
                place=str(place) if place not in (None, "") else None,
                has_test=bool(int(props.get("hasTest") or 0)),
                is_substitute=bool(int(props.get("isSubstitute") or 0)),
                kind=str(props.get("type") or ""),
            )
        )
    lessons.sort(key=lambda l: l.start)
    return lessons


def _parse_pl_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _header_index(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        hl = h.lower()
        if any(n in hl for n in names):
            return i
    return None


def parse_homework(html: str) -> tuple[list[Homework], int]:
    """Zwraca (prace domowe ze strony, liczba stron)."""
    soup = _soup(html)
    items: list[Homework] = []
    table = None
    for t in soup.find_all("table"):
        headers = [_text(th) for th in t.select("thead th")]
        if _header_index(headers, "temat") is not None:
            table = t
            break
    if table is not None:
        headers = [_text(th) for th in table.select("thead th")]
        i_date = _header_index(headers, "data")
        i_topic = _header_index(headers, "temat")
        i_subj = _header_index(headers, "przedmiot")
        i_teacher = _header_index(headers, "nauczyciel")
        i_att = _header_index(headers, "załącz")
        i_conf = _header_index(headers, "potwierdz")
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            def cell(i: int | None) -> str:
                return _text(cells[i]) if i is not None and i < len(cells) else ""

            link = row.find("a", href=re.compile(r"/homework/view/\d+"))
            hw_id = (
                re.search(r"/homework/view/(\d+)", link["href"]).group(1)
                if link
                else f"{cell(i_date)}|{cell(i_subj)}|{cell(i_topic)[:40]}"
            )
            att = cell(i_att)
            items.append(
                Homework(
                    hw_id=hw_id,
                    day=_parse_pl_date(cell(i_date)),
                    topic=cell(i_topic),
                    subject=cell(i_subj),
                    teacher=cell(i_teacher),
                    attachments=int(att) if att.isdigit() else 0,
                    confirmed=cell(i_conf).lower().startswith("tak"),
                )
            )
    pages = 1
    for a in soup.select("a[href*='page=']"):
        m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if m:
            pages = max(pages, int(m.group(1)))
    return items, pages


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def parse_grades(html: str) -> list[SubjectGrades]:
    soup = _soup(html)
    result: list[SubjectGrades] = []
    for t in soup.find_all("table"):
        headers = [_text(th) for th in t.select("thead th")]
        if _header_index(headers, "oceny") is None or _header_index(headers, "przedmiot") is None:
            continue
        i_subj = _header_index(headers, "przedmiot")
        i_grades = _header_index(headers, "oceny")
        i_avg = _header_index(headers, "średnia", "srednia")
        i_sem = _header_index(headers, "semestr")
        i_prop = _header_index(headers, "proponowana")
        i_final = _header_index(headers, "końcowa", "koncowa")
        for row in t.select("tbody tr"):
            cells = row.find_all("td")
            if not cells or i_subj is None or i_subj >= len(cells):
                continue

            def cell(i: int | None) -> str:
                val = _text(cells[i]) if i is not None and i < len(cells) else ""
                return "" if val == "-" else val

            subject = cell(i_subj)
            sg = SubjectGrades(subject=subject)
            if i_grades is not None and i_grades < len(cells):
                badges = cells[i_grades].select("span.badge") or cells[i_grades].find_all(
                    ["span", "a"]
                )
                for b in badges:
                    value = _text(b)
                    if not value:
                        continue
                    sg.grades.append(
                        Grade(
                            subject=subject,
                            value=value,
                            description=(b.get("data-title") or b.get("title") or "").strip(),
                            created=_parse_pl_date(b.get("data-created") or ""),
                            weight=b.get("data-rank"),
                        )
                    )
            sg.average = _to_float(cell(i_avg)) if cell(i_avg) else None
            sg.semester = cell(i_sem) or None
            sg.proposed = cell(i_prop) or None
            sg.final = cell(i_final) or None
            result.append(sg)
        break
    return result


def _add_week(moment: datetime, tz: tzinfo) -> datetime:
    """+7 dni w czasie lokalnym (poprawnie przez zmianę czasu letni/zimowy)."""
    naive = moment.replace(tzinfo=None) + timedelta(days=7)
    return naive.replace(tzinfo=tz)


# --------------------------------------------------------------------------
# Klient
# --------------------------------------------------------------------------


class ESzkolaClient:
    """Sesja panelu rodzica. Jedno zapytanie naraz (wybór dziecka jest w sesji)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        tz: tzinfo,
    ) -> None:
        host = host.strip()
        if "://" in host:
            host = urlparse(host).netloc
        self._base = f"https://{host.strip('/')}"
        self._session = session
        self._username = username
        self._password = password
        self._tz = tz
        self._lock = asyncio.Lock()
        self._logged_in = False
        self._current_child: str | None = None
        self.children: list[Child] = []

    # -- niskopoziomowe -----------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> tuple[str, str]:
        headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                **kwargs,
            ) as resp:
                text = await resp.text()
                if resp.status >= 500:
                    raise ESzkolaError(f"Serwer zwrócił {resp.status} dla {path}")
                return text, resp.url.path
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ESzkolaError(f"Błąd połączenia z {self._base}: {err}") from err

    async def _login(self) -> None:
        html, _ = await self._request("GET", "/login")
        token = parse_csrf_token(html)
        if not token:
            raise ESzkolaError("Nie znaleziono tokenu logowania na stronie /login")
        html, path = await self._request(
            "POST",
            "/login",
            data={
                "login": self._username,
                "password": self._password,
                "_csrf_token": token,
            },
            headers={"Referer": f"{self._base}/login"},
        )
        if path.rstrip("/").endswith("/login") or is_login_page(html):
            raise ESzkolaAuthError("Niepoprawny login lub hasło")
        self._logged_in = True
        self._current_child = None
        children = parse_children(html)
        if not children:
            html, _ = await self._request("GET", "/")
            children = parse_children(html)
        if children:
            self.children = children
        _LOGGER.debug("Zalogowano do eSzkoła24, dzieci: %s", self.children)

    async def _get_page(self, path: str, child_id: str | None) -> str:
        """GET z automatycznym ponownym logowaniem po wygaśnięciu sesji."""
        if child_id:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}_childId={child_id}"
        for attempt in (1, 2):
            if not self._logged_in:
                await self._login()
            html, final_path = await self._request("GET", path)
            if final_path.rstrip("/").endswith("/login") or is_login_page(html):
                self._logged_in = False
                if attempt == 2:
                    raise ESzkolaAuthError("Sesja wygasa zaraz po zalogowaniu")
                continue
            if child_id:
                self._current_child = child_id
            return html
        raise ESzkolaError("Nie udało się pobrać strony")

    # -- publiczne ------------------------------------------------------------

    async def async_login(self) -> list[Child]:
        async with self._lock:
            await self._login()
            return self.children

    async def async_get_lessons(
        self, child_id: str, start: datetime, end: datetime
    ) -> list[Lesson]:
        """Plan lekcji w zakresie [start, end).

        Serwer zwraca plan tylko dla jednego tygodnia na zapytanie, dlatego
        pytamy tydzień po tygodniu (pon. 00:00 – następny pon. 00:00), tak jak
        robi to widok tygodnia w panelu.
        """
        start_l = start.astimezone(self._tz)
        end_l = end.astimezone(self._tz)
        monday = (start_l - timedelta(days=start_l.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        weeks: list[datetime] = []
        while monday < end_l and len(weeks) < 60:
            weeks.append(monday)
            monday = _add_week(monday, self._tz)

        found: dict[str, Lesson] = {}
        async with self._lock:
            # Ustawienie dziecka w sesji tak, jak robi to panel
            await self._get_page("/calendar", child_id)
            for week_start in weeks:
                week_end = _add_week(week_start, self._tz)
                for lesson in await self._fetch_week(week_start, week_end):
                    if lesson.end > start_l and lesson.start < end_l:
                        found[f"{lesson.uid}|{lesson.start.isoformat()}"] = lesson
        return sorted(found.values(), key=lambda l: l.start)

    async def _fetch_week(self, week_start: datetime, week_end: datetime) -> list[Lesson]:
        body = {
            "type": EVENT_TYPES,
            "start": week_start.isoformat(timespec="seconds"),
            "end": week_end.isoformat(timespec="seconds"),
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{self._base}/calendar",
        }
        text, path = await self._request("POST", "/calendar/events", data=body, headers=headers)
        try:
            data = json.loads(text)
        except ValueError as err:
            self._logged_in = False
            raise ESzkolaError(
                f"Plan lekcji nie zwrócił JSON (ścieżka {path}) – możliwe wygaśnięcie sesji"
            ) from err
        if not isinstance(data, list):
            raise ESzkolaError("Nieoczekiwany format planu lekcji")
        return parse_events(data, self._tz)

    async def async_get_homework(self, child_id: str, max_pages: int = 3) -> list[Homework]:
        async with self._lock:
            html = await self._get_page("/homework", child_id)
            items, pages = parse_homework(html)
            for page in range(2, min(pages, max_pages) + 1):
                html = await self._get_page(f"/homework?page={page}", child_id)
                more, _ = parse_homework(html)
                items.extend(more)
        unique = {hw.hw_id: hw for hw in items}
        return sorted(unique.values(), key=lambda h: h.day or date.min, reverse=True)

    async def async_get_grades(self, child_id: str) -> list[SubjectGrades]:
        async with self._lock:
            html = await self._get_page("/stats/grade", child_id)
        return parse_grades(html)
