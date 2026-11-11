"""Heat-safety work planner: approximate hourly heat index and the lowest-heat outdoor work window.

Input: an hourly series of wet-bulb temperature and relative humidity for one point and day,
for example the `env_params` result from the FortyGuard Temperature API/MCP
(`wet_bulb_temperature_celsius` and `relative_humidity_percent`).

Method:
  1. Recover approximate hourly air temperature by inverting Stull (2011)'s wet-bulb formula
     (bisection). Stull's own error is about -1 to +0.65 °C in wet bulb, and inverting it
     magnifies that, so treat the result as approximate (about ±1-2 °C). Valid for
     -20 to 50 °C and RH 5-99 %; hours outside that range are skipped.
  2. Compute the NWS heat index for each hour (Rothfusz regression with the NWS adjustments).
     The heat index assumes shade; full sun can add up to about 8 °C (15 °F).
  3. Band each hour with the NWS heat-index categories and pick the lowest-heat window
     of N contiguous hours inside the working day (first calendar day in the input).

Planning aid only, not medical or regulatory advice. Follow OSHA/NIOSH guidance or your
company's heat plan for real crews.

Usage:
  python heat_planner.py result.json [--start 6 --end 19 --hours 4] [--json]
The JSON may be a REST status body, a saved MCP tool result, or the bare result object.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# NWS heat-index categories in °F (https://www.weather.gov/ama/heatindex)
BANDS_F = [(80, "Caution"), (90, "Extreme caution"), (103, "Danger"), (125, "Extreme danger")]
RANK = {"Lower risk": 0, "Caution": 1, "Extreme caution": 2, "Danger": 3, "Extreme danger": 4}


def band(hi_f: float) -> str:
    name = "Lower risk"
    for lower, label in BANDS_F:
        if hi_f >= lower:
            name = label
    return name


def stull_wet_bulb(t_c: float, rh: float) -> float:
    """Stull (2011) wet-bulb temperature (°C) from air temperature (°C) and RH (%)."""
    return (
        t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def air_temp_from_wet_bulb(wb_c: float, rh: float, lo: float = -20.0, hi: float = 50.0) -> float:
    """Invert Stull's formula by bisection: find T with stull_wet_bulb(T, rh) == wb_c.

    Raises ValueError outside Stull's valid range (-20..50 °C, RH 5..99 %)."""
    if not 5 <= rh <= 99:
        raise ValueError(f"RH {rh}% is outside Stull's valid range (5-99 %)")

    def f(t: float) -> float:
        return stull_wet_bulb(t, rh) - wb_c

    if f(lo) > 0 or f(hi) < 0:
        raise ValueError(f"wet bulb {wb_c} °C at RH {rh}% is outside the solvable range")
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def heat_index_f(t_f: float, rh: float) -> float:
    """NWS heat index (°F): simple formula below 80 °F, else Rothfusz with adjustments."""
    simple = 0.5 * (t_f + 61.0 + (t_f - 68.0) * 1.2 + rh * 0.094)
    if (simple + t_f) / 2 < 80:
        return simple
    hi = (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * rh
        - 0.22475541 * t_f * rh
        - 0.00683783 * t_f**2
        - 0.05481717 * rh**2
        + 0.00122874 * t_f**2 * rh
        + 0.00085282 * t_f * rh**2
        - 0.00000199 * t_f**2 * rh**2
    )
    if rh < 13 and 80 <= t_f <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t_f - 95)) / 17)
    elif rh > 85 and 80 <= t_f <= 87:
        hi += ((rh - 85) / 10) * ((87 - t_f) / 5)
    return hi


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


@dataclass
class Hour:
    hour: int
    rh: float
    wet_bulb_c: float
    air_temp_c: float
    heat_index_c: float
    risk: str


def load_result(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key_path in (
        ("body", "data", "result"),
        ("data", "result"),
        ("structuredContent", "result", "value"),
        ("structured_content", "result", "value"),
        ("result", "value", "raw"),
        ("result", "value"),
        ("result",),
    ):
        node = data
        try:
            for k in key_path:
                node = node[k]
            if isinstance(node, dict) and "locations" in node:
                return node
        except (KeyError, TypeError):
            continue
    if "locations" in data:
        return data
    raise ValueError(f"no hourly 'locations' block found in {path}")


def local_time(ts: str, tz_name: str | None) -> tuple[str | None, int]:
    """(date, local hour) for an 'HH:MM' label or an ISO timestamp.

    Timestamps with a non-zero offset are already local. UTC ('Z' or +00:00) timestamps are
    converted to metadata.timezone when it is given (needs the tz database; `pip install tzdata`
    on Windows)."""
    if len(ts) <= 5:
        return None, int(ts[:2])
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is not None and dt.utcoffset() == timedelta(0) and tz_name and tz_name not in ("UTC", "Etc/UTC"):
        try:
            dt = dt.astimezone(ZoneInfo(tz_name))
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"timestamps are UTC; install tzdata to convert them to {tz_name}") from e
    return dt.date().isoformat(), dt.hour


def plan_hours(result: dict) -> list[Hour]:
    """Hourly rows for the first calendar day in the input. Missing (-999/None) and
    out-of-range hours are skipped."""
    p = result["locations"][0]["parameters"]
    meta = result.get("metadata", {})
    stamps = meta.get("timestamps") or [f"{h:02d}:00" for h in range(len(p["relative_humidity_percent"]))]
    hours, first_day = [], None
    for i, ts in enumerate(stamps):
        day, hour = local_time(ts, meta.get("timezone"))
        first_day = first_day or day
        if day != first_day:
            continue
        rh, wb = p["relative_humidity_percent"][i], p["wet_bulb_temperature_celsius"][i]
        if rh is None or wb is None or rh == -999 or wb == -999:
            continue
        try:
            t_c = air_temp_from_wet_bulb(wb, rh)
        except ValueError:
            continue
        hi_c = f_to_c(heat_index_f(c_to_f(t_c), rh))
        hours.append(Hour(hour, rh, wb, round(t_c, 1), round(hi_c, 1), band(c_to_f(hi_c))))
    return hours


def best_window(hours: list[Hour], start: int, end: int, length: int) -> tuple[int, int] | None:
    """Lowest-risk, then lowest-heat, window of `length` contiguous hours between start and end."""
    day = [h for h in hours if start <= h.hour < end]
    best = None
    for i in range(len(day) - length + 1):
        win = day[i : i + length]
        if any(win[j + 1].hour != win[j].hour + 1 for j in range(length - 1)):
            continue
        score = (max(RANK[h.risk] for h in win), sum(h.heat_index_c for h in win))
        if best is None or score < best[0]:
            best = (score, (win[0].hour, win[-1].hour + 1))
    return best[1] if best else None


def render(hours: list[Hour], window, start: int, end: int, length: int) -> str:
    if not hours:
        return "No usable hours in the input (missing values or outside the formula's valid range)."
    lines = [
        "| Hour | RH % | Wet bulb °C | Air temp °C (approx.) | Heat index °C | Risk |",
        "|---|---|---|---|---|---|",
    ]
    for h in hours:
        lines.append(
            f"| {h.hour:02d}:00 | {h.rh:.0f} | {h.wet_bulb_c:.1f} | {h.air_temp_c:.1f} "
            f"| {h.heat_index_c:.1f} | {h.risk} |"
        )
    peak = max(hours, key=lambda h: h.heat_index_c)
    peak_line = f"- Heat index peaks at **{peak.hour:02d}:00 ({peak.heat_index_c:.1f} °C, {peak.risk})**."
    out = ["\n".join(lines), "", peak_line]
    if window:
        worst = max((h for h in hours if window[0] <= h.hour < window[1]), key=lambda h: RANK[h.risk])
        out.append(
            f"- Lowest-heat {length}-hour window between {start:02d}:00 and {end:02d}:00: "
            f"**{window[0]:02d}:00-{window[1]:02d}:00** (worst hour in it: {worst.risk})."
        )
        if RANK[worst.risk] >= 2:
            out.append(
                "- No low-risk window today: even this window reaches Extreme caution, "
                "so use extra precautions all day."
            )
    hot = [h for h in hours if start <= h.hour < end and RANK[h.risk] >= 2]
    if hot:
        out.append(
            "- Extra precautions (water, shade, rest breaks) at: "
            + ", ".join(f"{h.hour:02d}:00" for h in hot)
            + " (Extreme caution or worse)."
        )
    out.append("- Heat index assumes shade; full sun can add up to about 8 °C. Air temperature here is approximate.")
    out.append("- Planning aid only, not medical or regulatory advice. Follow your heat plan.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("result_json", type=Path)
    ap.add_argument("--start", type=int, default=6, help="earliest work hour (local, default 6)")
    ap.add_argument("--end", type=int, default=19, help="latest work hour end (local, default 19)")
    ap.add_argument("--hours", type=int, default=4, help="window length in hours (default 4)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a markdown table")
    a = ap.parse_args(argv)
    if not 0 <= a.start < a.end <= 24:
        ap.error("--start and --end must satisfy 0 <= start < end <= 24")
    if not 1 <= a.hours <= a.end - a.start:
        ap.error("--hours must be between 1 and the length of the working day")
    hours = plan_hours(load_result(a.result_json))
    window = best_window(hours, a.start, a.end, a.hours)
    if a.json:
        print(json.dumps({"hours": [asdict(h) for h in hours], "window": window}, indent=2))
    else:
        print(render(hours, window, a.start, a.end, a.hours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
