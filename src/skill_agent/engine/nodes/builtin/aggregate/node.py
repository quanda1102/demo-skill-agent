from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    field = params.get("field", "value")
    function = params.get("function", "avg")
    raw = input_data.get(field, input_data.get("values", []))
    values = raw if isinstance(raw, list) else [raw]
    nums = [float(value) for value in values]

    if function == "avg":
        result = sum(nums) / len(nums)
    elif function == "min":
        result = min(nums)
    elif function == "max":
        result = max(nums)
    elif function == "sum":
        result = sum(nums)
    elif function == "count":
        result = len(nums)
    else:
        raise ValueError(f"Unsupported aggregate function: {function}")

    print(json.dumps({**input_data, "result": result, "count": len(nums), "function": function}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
