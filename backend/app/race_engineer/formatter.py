"""Number-to-speech formatting for callout text.

Speech engines read raw values badly: "1:32.487" comes out as "one colon
thirty two point four eight seven", "P4" as "pee four" on some voices and
"position four" on others. Every number that reaches an utterance goes
through this module, which spells values out in words so the wording is
identical on every browser and voice.

Precision is deliberately low — a driver at 200 km/h can take in "one minute
thirty-two point five", not three decimal places.
"""

from __future__ import annotations

from decimal import Decimal

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
)


def spoken_int(n: int) -> str:
    """Whole number in words ("thirty-two"). Falls back to digits past 9999."""
    if n < 0:
        return f"minus {spoken_int(-n)}"
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = f"{_ONES[hundreds]} hundred"
        return f"{head} {spoken_int(rest)}" if rest else head
    if n < 10_000:
        thousands, rest = divmod(n, 1000)
        head = f"{_ONES[thousands]} thousand"
        return f"{head} {spoken_int(rest)}" if rest else head
    return str(n)


def spoken_decimal(value: float, places: int = 1) -> str:
    """Decimal in words ("five point two"); a zero fraction is dropped."""
    if value < 0:
        return f"minus {spoken_decimal(-value, places)}"
    quant = 10**places
    total = round(value * quant)
    whole, frac = divmod(total, quant)
    if frac == 0:
        return spoken_int(whole)
    # Digits are spoken one at a time after the point ("point zero five"),
    # which is how people read times and fuel figures aloud.
    digits = " ".join(_ONES[int(d)] for d in str(frac).zfill(places))
    return f"{spoken_int(whole)} point {digits}"


def plural(count: float, singular: str, many: str | None = None) -> str:
    """Pick the noun form for a (possibly fractional) count."""
    return singular if abs(count - 1.0) < 1e-9 else (many or f"{singular}s")


def spoken_lap_time(ms: int) -> str:
    """Lap time in words: 92487 -> "one minute thirty-two point five"."""
    if ms <= 0:
        return "no time"
    tenths_total = round(ms / 100)  # round once, then carry, so 59.96 s -> 1:00
    minutes, rest = divmod(tenths_total, 600)
    seconds, tenths = divmod(rest, 10)
    if minutes == 0:
        secs = spoken_decimal(seconds + tenths / 10)
        return f"{secs} {plural(seconds + tenths / 10, 'second')}"
    head = f"{spoken_int(minutes)} {plural(minutes, 'minute')}"
    if seconds == 0 and tenths == 0:
        return head  # "two minutes", not "two minutes zero"
    tail = spoken_decimal(seconds + tenths / 10)
    return f"{head} {tail}"


def spoken_gap(ms: float) -> str:
    """Time gap magnitude: "three tenths", "one point two seconds".

    Never says "zero tenths" — callers only ask for a gap they already found
    worth mentioning, so a sub-tenth value rounds up rather than vanishing.
    """
    seconds = abs(ms) / 1000
    if seconds < 1.0:
        tenths = max(1, round(seconds * 10))
        return f"{spoken_int(tenths)} {plural(tenths, 'tenth')}"
    return f"{spoken_decimal(seconds)} {plural(round(seconds, 1), 'second')}"


def spoken_laps(value: float) -> str:
    """Lap count in words: 5.24 -> "five point two laps"."""
    rounded = round(value, 1)
    return f"{spoken_decimal(value)} {plural(rounded, 'lap')}"


METERS_PER_FOOT = 0.3048
KMH_PER_MPH = 1.609344


def spoken_distance(meters: float, units: str = "metric") -> str:
    """Track distance. Braking points are quoted in feet where mph is used."""
    if units == "imperial":
        feet = round(meters / METERS_PER_FOOT)
        return f"{spoken_int(feet)} {plural(feet, 'foot', 'feet')}"
    value = round(meters)
    return f"{spoken_int(value)} {plural(value, 'meter')}"


def spoken_speed(kmh: float, units: str = "metric") -> str:
    if units == "imperial":
        mph = round(kmh / KMH_PER_MPH)
        return f"{spoken_int(mph)} {plural(mph, 'mile')} per hour"
    value = round(kmh)
    return f"{spoken_int(value)} {plural(value, 'kilometer')} per hour"


def spoken_position(n: int) -> str:
    """Race position. Words, not "P4" — voices disagree on abbreviations."""
    return f"position {spoken_int(n)}"


def spoken_temperature(celsius: float) -> str:
    degrees = round(celsius)
    return f"{spoken_int(degrees)} {plural(degrees, 'degree')}"


_WHEEL_NAMES = {
    "fl": "front-left",
    "fr": "front-right",
    "rl": "rear-left",
    "rr": "rear-right",
}


def spoken_wheels(wheels: list[str]) -> str:
    """Wheel set: one wheel by name, an axle as "front"/"rear", else "all"."""
    keys = [w for w in ("fl", "fr", "rl", "rr") if w in wheels]
    if len(keys) == 1:
        return _WHEEL_NAMES[keys[0]]
    if keys == ["fl", "fr"]:
        return "front"
    if keys == ["rl", "rr"]:
        return "rear"
    return "all-wheel" if len(keys) == 4 else "multiple wheel"


def spoken_corner(number: int | None, name: str = "") -> str:
    """Corner reference, or a positional hint when the corner is unknown.

    A circuit whose corners have been labelled by hand (#48) carries their
    names, and "you lost three tenths in the Parabolica" is what an engineer
    actually says — so the name wins over the number whenever there is one.
    """
    if name:
        return name
    return f"turn {spoken_int(number)}" if number else "the next braking zone"


def spoken_sector_decimal(
    value: float,
    places: int = 3,
    rounding: str = "round",
) -> str:
    """Speak sector timing values with fixed decimal precision.

    Unlike spoken_decimal(), fractional trailing zeros are retained.
    The fractional digits are spoken individually for predictable TTS.
    """
    from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

    if places not in (1, 2, 3):
        raise ValueError("Sector precision must be 1, 2 or 3")
    if rounding not in ("round", "truncate"):
        raise ValueError("Sector rounding must be 'round' or 'truncate'")

    negative = value < 0
    magnitude = Decimal(str(abs(value)))
    unit = Decimal(1).scaleb(-places)
    mode = ROUND_HALF_UP if rounding == "round" else ROUND_DOWN
    quantized = magnitude.quantize(unit, rounding=mode)

    whole, fractional = format(quantized, f".{places}f").split(".")
    digits = " ".join(_ONES[int(digit)] for digit in fractional)

    if int(whole) == 0 and quantized != 0:
        result = f"point {digits}"
    else:
        result = f"{spoken_int(int(whole))} point {digits}"

    return f"minus {result}" if negative and quantized != 0 else result


def _precise_timing_seconds(ms: int | float, places: int, rounding: str) -> Decimal:
    """Quantize milliseconds to seconds using the selected voice precision."""
    from decimal import ROUND_DOWN, ROUND_HALF_UP

    if places not in (1, 2, 3):
        raise ValueError("Timing precision must be 1, 2 or 3")
    if rounding not in ("round", "truncate"):
        raise ValueError("Timing rounding must be 'round' or 'truncate'")

    mode = ROUND_HALF_UP if rounding == "round" else ROUND_DOWN
    unit = Decimal(1).scaleb(-places)
    return (Decimal(str(abs(ms))) / Decimal(1000)).quantize(
        unit, rounding=mode
    )


def spoken_precise_lap_time(
    ms: int,
    places: int = 3,
    rounding: str = "round",
) -> str:
    """Speak a completed lap time with configurable fractional precision."""
    from decimal import Decimal

    if ms <= 0:
        return "no time"

    total = _precise_timing_seconds(ms, places, rounding)
    minutes, seconds = divmod(total, Decimal(60))

    spoken_seconds = spoken_sector_decimal(
        float(seconds), places, rounding
    )

    if minutes == 0:
        unit = "second" if seconds == 1 else "seconds"
        return f"{spoken_seconds} {unit}"

    spoken_minutes = int(minutes)
    head = f"{spoken_int(spoken_minutes)} {plural(spoken_minutes, 'minute')}"
    return f"{head} {spoken_seconds}"


def spoken_sector_duration(
    ms: int | float,
    places: int = 3,
    rounding: str = "round",
) -> str:
    """Speak sector duration in minutes when appropriate.

    Quantize before splitting minutes and seconds so rounding across
    the 60-second boundary is handled correctly. Reference deltas
    continue to use spoken_sector_decimal separately.
    """
    from decimal import Decimal

    total = _precise_timing_seconds(ms, places, rounding)
    minutes, seconds = divmod(total, Decimal(60))
    spoken_seconds = spoken_sector_decimal(float(seconds), places, rounding)

    if not minutes:
        return f"{spoken_seconds} seconds"

    if 0 < seconds < 1:
        spoken_seconds = f"zero {spoken_seconds}"

    count = int(minutes)
    spoken_minutes = f"{spoken_int(count)} {plural(count, 'minute')}"
    return f"{spoken_minutes} {spoken_seconds} seconds"


def spoken_precise_gap(
    ms: int | float,
    places: int = 3,
    rounding: str = "round",
) -> str:
    """Speak a lap-time difference using the sector precision settings."""
    seconds = _precise_timing_seconds(ms, places, rounding)
    spoken = spoken_sector_decimal(float(seconds), places, rounding)
    unit = "second" if seconds == 1 else "seconds"
    return f"{spoken} {unit}"
