"""Stałe integracji eSzkoła24."""

from datetime import timedelta

DOMAIN = "eszkola24"

CONF_HOST = "host"
CONF_EXCLUDE = "exclude_pattern"
CONF_SCAN_HOURS = "scan_interval_hours"

DEFAULT_HOST = "panel-spmorawica.eszkola24.pl"
DEFAULT_EXCLUDE = "Świetlica"
DEFAULT_SCAN_HOURS = 3

# Okno, które koordynator trzyma w pamięci (reszta pobierana na żądanie kalendarza)
CAL_PAST = timedelta(days=7)
CAL_FUTURE = timedelta(days=35)

HOMEWORK_MAX_PAGES = 3

EVENT_NEW_GRADE = "eszkola24_new_grade"
EVENT_NEW_HOMEWORK = "eszkola24_new_homework"

STORAGE_VERSION = 1
