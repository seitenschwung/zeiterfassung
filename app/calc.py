"""Pure time-tracking maths: holidays, day splitting (DST-safe), soll/ist/diff per day."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.config import TZ


@dataclass(frozen=True)
class Settings:
    weekly_hours: float = 40.0
    workdays: tuple[int, ...] = (1, 2, 3, 4, 5)  # ISO weekdays, Monday = 1
    vacation_days: float = 30.0
    start_date: date | None = None
    start_balance_hours: float = 0.0

    @property
    def daily_soll(self) -> int:
        """Target seconds on a regular working day."""
        return round(self.weekly_hours * 3600 / len(self.workdays))

    @property
    def start_balance(self) -> int:
        return round(self.start_balance_hours * 3600)


def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def holidays_sh(year: int) -> dict[date, str]:
    """Public holidays in Schleswig-Holstein (Reformationstag since 2018)."""
    ostern = easter(year)
    days = {
        date(year, 1, 1): "Neujahr",
        ostern - timedelta(days=2): "Karfreitag",
        ostern + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        ostern + timedelta(days=39): "Christi Himmelfahrt",
        ostern + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    if year >= 2018:
        days[date(year, 10, 31)] = "Reformationstag"
    return days


def holiday_name(d: date) -> str | None:
    return holidays_sh(d.year).get(d)


def is_working_day(d: date, settings: Settings) -> bool:
    return d.isoweekday() in settings.workdays and holiday_name(d) is None


def day_bounds(d: date) -> tuple[int, int]:
    """Epoch seconds of local midnight at the start and end of day d (23 h / 25 h on DST days)."""
    nxt = d + timedelta(days=1)
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ)
    return int(start.timestamp()), int(end.timestamp())


def local_date(ts: int) -> date:
    return datetime.fromtimestamp(ts, TZ).date()


def seconds_per_day(start_ts: int, end_ts: int) -> dict[date, int]:
    """Real elapsed seconds of [start, end) per local calendar day (splits entries over midnight)."""
    out: dict[date, int] = {}
    d = local_date(start_ts)
    while True:
        lo, hi = day_bounds(d)
        if lo >= end_ts:
            break
        sec = min(end_ts, hi) - max(start_ts, lo)
        if sec > 0:
            out[d] = out.get(d, 0) + sec
        d += timedelta(days=1)
    return out


def day_soll(d: date, settings: Settings) -> int:
    return settings.daily_soll if is_working_day(d, settings) else 0


def build_days(
    first: date,
    last: date,
    entries: list[tuple[int, int | None]],
    absences: dict[date, tuple[str, float]],
    settings: Settings,
    count_from: date,
    today: date,
    now_ts: int,
) -> list[dict]:
    """Per-day figures for first..last. Days outside count_from..today have diff 0 (not counted)."""
    worked: dict[date, int] = {}
    for start, end in entries:
        for d, sec in seconds_per_day(start, end if end is not None else max(now_ts, start)).items():
            worked[d] = worked.get(d, 0) + sec
    days = []
    d = first
    while d <= last:
        soll = day_soll(d, settings)
        absence = absences.get(d)
        credit = round(soll * absence[1]) if absence else 0
        w = worked.get(d, 0)
        ist = w + credit
        counted = count_from <= d <= today
        days.append(
            {
                "date": d,
                "weekday": d.isoweekday(),
                "holiday": holiday_name(d),
                "workday": is_working_day(d, settings),
                "absence": {"kind": absence[0], "fraction": absence[1]} if absence else None,
                "soll": soll,
                "worked": w,
                "credit": credit,
                "ist": ist,
                "diff": ist - soll if counted else 0,
                "counted": counted,
            }
        )
        d += timedelta(days=1)
    return days
