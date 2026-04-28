from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timezone


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    station_id = params.get("station_id", "BTS_001")
    metric = params.get("metric", "RSSI")
    simulate_drop = bool(params.get("simulate_drop", True))

    elapsed_seed = datetime.now(timezone.utc).timestamp()
    random.seed(f"{station_id}:{int(elapsed_seed // 5)}")
    value = -75 + math.sin(elapsed_seed * 0.1) * 4 + random.uniform(-1.5, 1.5)
    if simulate_drop:
        value -= 22

    print(
        json.dumps(
            {
                "value": round(value, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "station_id": station_id,
                "metric": metric,
                "unit": "dBm" if metric.upper() == "RSSI" else "",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
