"""Generate planner/sample_env_params.json: a synthetic hot, humid day in the env_params result shape.

The values are made up (a smooth diurnal curve), not FortyGuard data, so the repo can ship a
runnable example and tests without redistributing anyone's data.
"""

import json
import math
from pathlib import Path

from heat_planner import stull_wet_bulb

HERE = Path(__file__).resolve().parent


def day():
    temps, rhs = [], []
    for h in range(24):
        # air temperature: 27 °C at 03:00, 37 °C at 15:00; humidity moves the opposite way
        phase = math.cos((h - 15) / 24 * 2 * math.pi)
        t = 32 + 5 * phase
        rh = 58 - 22 * phase
        temps.append(round(t, 1))
        rhs.append(round(rh, 1))
    wb = [round(stull_wet_bulb(t, rh), 1) for t, rh in zip(temps, rhs, strict=True)]
    return temps, rhs, wb


def main():
    temps, rhs, wb = day()
    stamps = [f"2025-08-18T{h:02d}:00:00-05:00" for h in range(24)]
    result = {
        "metadata": {"timezone": "America/Chicago", "timestamps": stamps, "note": "synthetic example data"},
        "locations": [
            {
                "lat": 29.76,
                "lon": -95.37,
                "parameters": {
                    "relative_humidity_percent": rhs,
                    "wet_bulb_temperature_celsius": wb,
                    "synthetic_air_temperature_celsius": temps,
                },
            }
        ],
    }
    (HERE / "sample_env_params.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("wrote sample_env_params.json")


if __name__ == "__main__":
    main()
