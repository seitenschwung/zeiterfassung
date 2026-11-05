from datetime import date, datetime

from app import calc
from app.calc import Settings
from app.config import TZ


def ts(local: str) -> int:
    return int(datetime.fromisoformat(local).replace(tzinfo=TZ).timestamp())


H = 3600


def test_easter():
    assert calc.easter(2024) == date(2024, 3, 31)
    assert calc.easter(2025) == date(2025, 4, 20)
    assert calc.easter(2026) == date(2026, 4, 5)


def test_holidays_schleswig_holstein_2026():
    h = calc.holidays_sh(2026)
    assert h[date(2026, 4, 3)] == "Karfreitag"
    assert h[date(2026, 4, 6)] == "Ostermontag"
    assert h[date(2026, 5, 14)] == "Christi Himmelfahrt"
    assert h[date(2026, 5, 25)] == "Pfingstmontag"
    assert h[date(2026, 10, 31)] == "Reformationstag"
    assert date(2026, 1, 6) not in h  # Heilige Drei Könige: nicht in SH
    assert date(2026, 11, 1) not in h  # Allerheiligen: nicht in SH
    assert len(h) == 10
    assert date(2017, 10, 31) not in calc.holidays_sh(2017)


def test_daily_soll_follows_weekly_hours_and_workdays():
    assert Settings().daily_soll == 8 * H
    assert Settings(weekly_hours=38.5).daily_soll == round(7.7 * H)
    assert Settings(weekly_hours=32, workdays=(1, 2, 3, 4)).daily_soll == 8 * H


def test_working_day_excludes_weekend_and_holiday():
    s = Settings()
    assert calc.is_working_day(date(2026, 11, 4), s)
    assert not calc.is_working_day(date(2026, 11, 7), s)  # Saturday
    assert not calc.is_working_day(date(2026, 12, 25), s)  # holiday


def test_entry_over_midnight_is_split_per_day():
    parts = calc.seconds_per_day(ts("2026-11-03T22:00"), ts("2026-11-04T02:30"))
    assert parts == {date(2026, 11, 3): 2 * H, date(2026, 11, 4): 2.5 * H}


def test_entry_over_several_days():
    parts = calc.seconds_per_day(ts("2026-11-03T12:00"), ts("2026-11-05T06:00"))
    assert parts == {date(2026, 11, 3): 12 * H, date(2026, 11, 4): 24 * H, date(2026, 11, 5): 6 * H}


def test_entry_ending_exactly_at_midnight_does_not_touch_next_day():
    assert calc.seconds_per_day(ts("2026-11-03T20:00"), ts("2026-11-04T00:00")) == {date(2026, 11, 3): 4 * H}


def test_spring_dst_day_has_23_hours():
    lo, hi = calc.day_bounds(date(2026, 3, 29))
    assert hi - lo == 23 * H


def test_autumn_dst_day_has_25_hours():
    lo, hi = calc.day_bounds(date(2026, 10, 25))
    assert hi - lo == 25 * H


def test_night_shift_over_spring_dst_counts_real_elapsed_time():
    # 22:00 Sat -> 06:00 Sun, clocks jump 02:00 -> 03:00, so only 7 real hours
    start, end = ts("2026-03-28T22:00"), ts("2026-03-29T06:00")
    assert end - start == 7 * H
    assert calc.seconds_per_day(start, end) == {date(2026, 3, 28): 2 * H, date(2026, 3, 29): 5 * H}


def test_night_shift_over_autumn_dst_counts_real_elapsed_time():
    # 22:00 -> 06:00 with clocks going back 03:00 -> 02:00: nine real hours
    start, end = ts("2026-10-24T22:00"), ts("2026-10-25T06:00")
    assert end - start == 9 * H
    assert calc.seconds_per_day(start, end) == {date(2026, 10, 24): 2 * H, date(2026, 10, 25): 7 * H}


def build(
    entries, absences=None, today=date(2026, 11, 6), count_from=date(2026, 11, 2), first=None, last=None
):
    return calc.build_days(
        first or date(2026, 11, 2),
        last or date(2026, 11, 8),
        entries,
        absences or {},
        Settings(),
        count_from,
        today,
        ts("2026-11-06T12:00"),
    )


def test_overtime_and_undertime_per_day():
    days = build(
        [(ts("2026-11-02T08:00"), ts("2026-11-02T17:30")), (ts("2026-11-03T09:00"), ts("2026-11-03T15:00"))]
    )
    assert days[0]["worked"] == 9.5 * H and days[0]["diff"] == 1.5 * H
    assert days[1]["diff"] == -2 * H
    assert days[2]["diff"] == -8 * H  # Wednesday without entry, in the past
    assert days[5]["soll"] == 0 and days[5]["diff"] == 0  # Saturday


def test_future_days_and_days_before_start_are_not_counted():
    days = build([], today=date(2026, 11, 4), count_from=date(2026, 11, 3))
    assert days[0]["diff"] == 0  # before start
    assert days[1]["diff"] == -8 * H
    assert days[2]["diff"] == -8 * H  # today counts
    assert days[3]["diff"] == 0 and days[3]["soll"] == 8 * H  # future: soll shown, not counted


def test_vacation_and_sick_days_credit_the_target():
    days = build([], absences={date(2026, 11, 2): ("urlaub", 1.0), date(2026, 11, 3): ("krank", 0.5)})
    assert days[0]["ist"] == 8 * H and days[0]["diff"] == 0
    assert days[1]["credit"] == 4 * H and days[1]["diff"] == -4 * H


def test_half_day_vacation_plus_work_balances():
    days = build(
        [(ts("2026-11-02T08:00"), ts("2026-11-02T12:00"))], absences={date(2026, 11, 2): ("urlaub", 0.5)}
    )
    assert days[0]["diff"] == 0


def test_running_entry_counts_until_now():
    days = build([(ts("2026-11-06T08:00"), None)])
    assert days[4]["worked"] == 4 * H


def test_holiday_has_no_soll():
    days = calc.build_days(
        date(2026, 12, 24), date(2026, 12, 26), [], {}, Settings(), date(2026, 12, 24), date(2026, 12, 31), 0
    )
    assert [d["soll"] for d in days] == [8 * H, 0, 0]
    assert days[1]["holiday"] == "1. Weihnachtstag"


def test_vacation_quota_falls_back_to_default_for_other_years():
    s = Settings(vacation_days=30, vacation_overrides={2026: 5})
    assert s.vacation_for(2026) == 5 and s.vacation_for(2027) == 30
