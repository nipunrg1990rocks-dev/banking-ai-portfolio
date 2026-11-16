"""Synthetic business calendars generated from published holiday *rules*.

These calendars are good enough to exercise settlement-date logic (weekends, fixed and
moving holidays, Easter, substitute days, calendars that differ between an exchange and the
currency's payment system). They are generated, not copied from an authoritative source, and
must not be used for real settlement.
"""

from __future__ import annotations

from datetime import date, timedelta

YEARS = range(2024, 2033)


def easter(y: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(y, month, day + 1)


def nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    """n-th weekday (0=Mon) of a month; n=-1 means last."""
    if n > 0:
        d = date(y, m, 1)
        while d.weekday() != weekday:
            d += timedelta(days=1)
        return d + timedelta(weeks=n - 1)
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def observed_us(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def substitute_monday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _uk_christmas(y):
    xmas, boxing = date(y, 12, 25), date(y, 12, 26)
    out = []
    if xmas.weekday() == 5:  # Sat -> Mon 27, Boxing Sun -> Tue 28
        out += [("Christmas Day (substitute)", date(y, 12, 27)), ("Boxing Day (substitute)", date(y, 12, 28))]
    elif xmas.weekday() == 6:  # Sun -> Tue 27; Boxing Mon 26
        out += [("Boxing Day", boxing), ("Christmas Day (substitute)", date(y, 12, 27))]
    elif xmas.weekday() == 4:  # Fri; Boxing Sat -> Mon 28
        out += [("Christmas Day", xmas), ("Boxing Day (substitute)", date(y, 12, 28))]
    else:
        out += [("Christmas Day", xmas), ("Boxing Day", boxing)]
    return out


JP_EQUINOX = {2024: (date(2024, 3, 20), date(2024, 9, 22)), 2025: (date(2025, 3, 20), date(2025, 9, 23)),
              2026: (date(2026, 3, 20), date(2026, 9, 23)), 2027: (date(2027, 3, 21), date(2027, 9, 23)),
              2028: (date(2028, 3, 20), date(2028, 9, 22)), 2029: (date(2029, 3, 20), date(2029, 9, 23)),
              2030: (date(2030, 3, 20), date(2030, 9, 23)), 2031: (date(2031, 3, 21), date(2031, 9, 23)),
              2032: (date(2032, 3, 20), date(2032, 9, 22))}


def rules(code: str, y: int) -> list[tuple[str, date]]:
    e = easter(y)
    if code in ("USNY", "XNYS", "XCME", "XCBO"):
        h = [("New Year's Day", observed_us(date(y, 1, 1))),
             ("Martin Luther King Jr. Day", nth_weekday(y, 1, 0, 3)),
             ("Presidents' Day", nth_weekday(y, 2, 0, 3)),
             ("Memorial Day", nth_weekday(y, 5, 0, -1)),
             ("Juneteenth", observed_us(date(y, 6, 19))),
             ("Independence Day", observed_us(date(y, 7, 4))),
             ("Labor Day", nth_weekday(y, 9, 0, 1)),
             ("Thanksgiving Day", nth_weekday(y, 11, 3, 4)),
             ("Christmas Day", observed_us(date(y, 12, 25)))]
        if code == "USNY":  # payment/bond calendar also closes on these
            h += [("Columbus Day", nth_weekday(y, 10, 0, 2)), ("Veterans Day", observed_us(date(y, 11, 11)))]
        else:  # exchanges close Good Friday
            h += [("Good Friday", e - timedelta(days=2))]
        return h
    if code in ("GBLO", "XLON"):
        return [("New Year's Day", substitute_monday(date(y, 1, 1))), ("Good Friday", e - timedelta(days=2)),
                ("Easter Monday", e + timedelta(days=1)), ("Early May bank holiday", nth_weekday(y, 5, 0, 1)),
                ("Spring bank holiday", nth_weekday(y, 5, 0, -1)), ("Summer bank holiday", nth_weekday(y, 8, 0, -1))
                ] + _uk_christmas(y)
    if code in ("EUTA", "XETR", "XEUR"):
        h = [("New Year's Day", date(y, 1, 1)), ("Good Friday", e - timedelta(days=2)),
             ("Easter Monday", e + timedelta(days=1)), ("Labour Day", date(y, 5, 1)),
             ("Christmas Day", date(y, 12, 25)), ("St Stephen's Day", date(y, 12, 26))]
        if code in ("XETR", "XEUR"):
            h += [("Christmas Eve", date(y, 12, 24)), ("New Year's Eve", date(y, 12, 31))]
        return h
    if code in ("JPTO", "XTKS"):
        ve, ae = JP_EQUINOX[y]
        base = [("New Year holiday", date(y, 1, 1)), ("New Year holiday", date(y, 1, 2)),
                ("New Year holiday", date(y, 1, 3)), ("Coming of Age Day", nth_weekday(y, 1, 0, 2)),
                ("National Foundation Day", date(y, 2, 11)), ("Emperor's Birthday", date(y, 2, 23)),
                ("Vernal Equinox Day", ve), ("Showa Day", date(y, 4, 29)), ("Constitution Day", date(y, 5, 3)),
                ("Greenery Day", date(y, 5, 4)), ("Children's Day", date(y, 5, 5)),
                ("Marine Day", nth_weekday(y, 7, 0, 3)), ("Mountain Day", date(y, 8, 11)),
                ("Respect for the Aged Day", nth_weekday(y, 9, 0, 3)), ("Autumnal Equinox Day", ae),
                ("Sports Day", nth_weekday(y, 10, 0, 2)), ("Culture Day", date(y, 11, 3)),
                ("Labour Thanksgiving Day", date(y, 11, 23)), ("Bank holiday", date(y, 12, 31))]
        out = list(base)
        days = {d for _, d in base}
        for name, d in base:  # substitute holiday: Sunday holiday -> next non-holiday weekday
            if d.weekday() == 6:
                s = d + timedelta(days=1)
                while s in days:
                    s += timedelta(days=1)
                out.append((name + " (substitute)", s))
        # "Citizens' holiday": a day sandwiched between two holidays (e.g. 22 Sep 2026)
        rad = nth_weekday(y, 9, 0, 3)
        if (ae - rad).days == 2:
            out.append(("Citizens' Holiday", rad + timedelta(days=1)))
        return out
    if code == "CATO":
        vic = date(y, 5, 24)
        while vic.weekday() != 0:
            vic -= timedelta(days=1)
        return [("New Year's Day", substitute_monday(date(y, 1, 1))), ("Good Friday", e - timedelta(days=2)),
                ("Victoria Day", vic), ("Canada Day", substitute_monday(date(y, 7, 1))),
                ("Civic Holiday", nth_weekday(y, 8, 0, 1)), ("Labour Day", nth_weekday(y, 9, 0, 1)),
                ("National Day for Truth and Reconciliation", substitute_monday(date(y, 9, 30))),
                ("Thanksgiving", nth_weekday(y, 10, 0, 2)), ("Remembrance Day", substitute_monday(date(y, 11, 11)))
                ] + _uk_christmas(y)
    if code == "AUSY":
        return [("New Year's Day", substitute_monday(date(y, 1, 1))), ("Australia Day", substitute_monday(date(y, 1, 26))),
                ("Good Friday", e - timedelta(days=2)), ("Easter Monday", e + timedelta(days=1)),
                ("Anzac Day", date(y, 4, 25)), ("King's Birthday", nth_weekday(y, 6, 0, 2)),
                ("Bank Holiday", nth_weekday(y, 8, 0, 1)), ("Labour Day", nth_weekday(y, 10, 0, 1))
                ] + _uk_christmas(y)
    if code == "CHZU":
        return [("New Year's Day", date(y, 1, 1)), ("Berchtold's Day", date(y, 1, 2)),
                ("Good Friday", e - timedelta(days=2)), ("Easter Monday", e + timedelta(days=1)),
                ("Ascension Day", e + timedelta(days=39)), ("Whit Monday", e + timedelta(days=50)),
                ("National Day", date(y, 8, 1)), ("Christmas Day", date(y, 12, 25)),
                ("St Stephen's Day", date(y, 12, 26))]
    raise KeyError(code)


CALENDARS = {
    "USNY": ("US payments and bond market (simulated)", "America/New_York"),
    "XNYS": ("US equity exchange (simulated)", "America/New_York"),
    "XCME": ("US futures exchange (simulated)", "America/Chicago"),
    "XCBO": ("US options exchange (simulated)", "America/Chicago"),
    "GBLO": ("UK payments (simulated)", "Europe/London"),
    "XLON": ("UK equity exchange (simulated)", "Europe/London"),
    "EUTA": ("Euro payments, TARGET-style (simulated)", "Europe/Berlin"),
    "XETR": ("German equity exchange (simulated)", "Europe/Berlin"),
    "XEUR": ("European futures exchange (simulated)", "Europe/Berlin"),
    "JPTO": ("Japan payments (simulated)", "Asia/Tokyo"),
    "XTKS": ("Japan equity exchange (simulated)", "Asia/Tokyo"),
    "CATO": ("Canada payments (simulated)", "America/Toronto"),
    "AUSY": ("Australia payments (simulated)", "Australia/Sydney"),
    "CHZU": ("Switzerland payments (simulated)", "Europe/Zurich"),
}

CCY_CALENDAR = {"USD": "USNY", "EUR": "EUTA", "GBP": "GBLO", "JPY": "JPTO", "CAD": "CATO", "AUD": "AUSY",
                "CHF": "CHZU"}


def build_holidays() -> list[dict]:
    rows = []
    for code in CALENDARS:
        seen = set()
        for y in YEARS:
            for name, d in rules(code, y):
                if d.weekday() >= 5 or (code, d) in seen:
                    continue
                seen.add((code, d))
                rows.append({"calendar_code": code, "holiday_date": d, "holiday_name": name})
    rows.sort(key=lambda r: (r["calendar_code"], r["holiday_date"]))
    return rows


def holiday_sets(rows: list[dict]) -> dict[str, set[date]]:
    out: dict[str, set[date]] = {c: set() for c in CALENDARS}
    for r in rows:
        out[r["calendar_code"]].add(r["holiday_date"])
    return out
