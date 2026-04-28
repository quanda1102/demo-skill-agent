from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    field = params.get("condition_field", "passed")
    duration = int(params.get("duration_seconds", 30))
    satisfied = bool(input_data.get(field))
    print(json.dumps({**input_data, "satisfied": satisfied, "duration": duration}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
