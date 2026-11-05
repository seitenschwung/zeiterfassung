from datetime import date

from pydantic import BaseModel, Field, field_validator


class LoginIn(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


class EntryIn(BaseModel):
    start: str
    end: str | None = None


class AbsenceIn(BaseModel):
    kind: str
    fraction: float = 1.0

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("urlaub", "krank"):
            raise ValueError("Art muss 'urlaub' oder 'krank' sein")
        return v

    @field_validator("fraction")
    @classmethod
    def _fraction(cls, v: float) -> float:
        if v not in (0.5, 1.0):
            raise ValueError("Anteil muss 0.5 oder 1 sein")
        return v


class SettingsIn(BaseModel):
    weekly_hours: float = Field(gt=0, le=80)
    workdays: list[int] = Field(min_length=1)
    vacation_days: float = Field(ge=0, le=366)
    vacation_overrides: dict[str, float] = {}
    start_date: date | None = None
    start_balance_hours: float = Field(ge=-2000, le=2000)

    @field_validator("vacation_overrides")
    @classmethod
    def _overrides(cls, v: dict[str, float]) -> dict[str, float]:
        for year, days in v.items():
            if not (year.isdigit() and len(year) == 4) or not 0 <= days <= 366:
                raise ValueError("Urlaubstage je Jahr: Jahr (4 Ziffern) und 0 bis 366 Tage")
        return v

    @field_validator("workdays")
    @classmethod
    def _workdays(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v) or len(set(v)) != len(v):
            raise ValueError("Arbeitstage: eindeutige Werte von 1 (Mo) bis 7 (So)")
        return sorted(v)
