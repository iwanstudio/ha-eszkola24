# eSzkoła24 dla Home Assistanta

Nieoficjalna integracja panelu rodzica [eSzkoła24](https://eszkola24.pl). Loguje się do panelu tak jak przeglądarka i dla każdego dziecka na koncie tworzy:

| Encja | Co pokazuje |
|---|---|
| `calendar.eszkola24_<imię>_plan_lekcji` | Prawdziwy plan lekcji z panelu, z oznaczeniem **zastępstw** i **sprawdzianów** |
| `sensor.eszkola24_<imię>_prace_domowe` | Liczba nadchodzących prac domowych; w atrybutach lista (data, przedmiot, treść, nauczyciel) |
| `sensor.eszkola24_<imię>_ostatnia_ocena` | Ostatnia ocena; w atrybutach przedmiot, opis (z czego), data i waga |
| `sensor.eszkola24_<imię>_oceny` | Średnia ze wszystkich przedmiotów; w atrybutach wszystkie oceny z opisami (per przedmiot) i lista 15 ostatnich |
| `sensor.eszkola24_<imię>_najblizszy_sprawdzian` | Data najbliższego sprawdzianu; w atrybutach przedmiot, zakres i lista nadchodzących |

Zakres sprawdzianu (zakładka Statystyki → Sprawdziany) trafia też do opisu właściwej lekcji w kalendarzu.

Dodatkowo integracja wysyła zdarzenia `eszkola24_new_grade`, `eszkola24_new_homework` i `eszkola24_new_test`, gdy pojawi się nowa ocena, praca domowa albo sprawdzian.

> Integracja nie jest związana z eSzkoła24. Korzysta z tych samych stron co panel w przeglądarce, więc zmiany po stronie serwisu mogą ją zepsuć.

## Instalacja (HACS)

1. HACS → menu ⋮ → **Niestandardowe repozytoria** → adres `https://github.com/iwanstudio/ha-eszkola24`, kategoria **Integracja**.
2. Wyszukaj „eSzkoła24” w HACS i kliknij **Pobierz**.
3. Uruchom ponownie Home Assistanta.
4. **Ustawienia → Urządzenia i usługi → Dodaj integrację → eSzkoła24**. Podaj adres panelu swojej szkoły (np. `panel-twojaszkola.eszkola24.pl`), login i hasło.

Instalacja ręczna: skopiuj folder `custom_components/eszkola24` do `config/custom_components/` i zrestartuj HA.

## Ustawienia

W opcjach integracji (przycisk **Konfiguruj**):

- **Pomijaj w kalendarzu zajęcia pasujące do** – domyślnie `Świetlica`. Można podać kilka słów jako wyrażenie regularne, np. `Świetlica|Koło`.
- **Odświeżanie co ile godzin** – domyślnie 3.
- **Kalendarz dni wolnych** – dowolny kalendarz HA (np. lokalny „Dni wolne od szkoły”); lekcje w dni, w które ma on wpis, są pomijane. Szkoła nie wykreśla świąt i ferii z planu.

Hasło jest zapisywane wyłącznie w konfiguracji Twojego Home Assistanta. Jeśli je zmienisz w eSzkoła24, HA poprosi o ponowne zalogowanie.

## Przykład: powiadomienie o nowej ocenie

```yaml
triggers:
  - trigger: event
    event_type: eszkola24_new_grade
actions:
  - action: notify.mobile_app_twoj_telefon
    data:
      title: "Nowa ocena: {{ trigger.event.data.child }}"
      message: >
        {{ trigger.event.data.subject }}: {{ trigger.event.data.grade }}
        {% if trigger.event.data.description %}({{ trigger.event.data.description }}){% endif %}
```

## Przykład: co przynieść jutro (karta Markdown)

```jinja
{% set hw = state_attr('sensor.eszkola24_maria_prace_domowe', 'nadchodzace') or [] %}
{% set jutro = (now() + timedelta(days=1)).date() | string %}
{% for h in hw if h.data == jutro %}
**{{ h.przedmiot }}**: {{ h.temat }}
{% else %}
Na jutro nic nie trzeba przynosić.
{% endfor %}
```
