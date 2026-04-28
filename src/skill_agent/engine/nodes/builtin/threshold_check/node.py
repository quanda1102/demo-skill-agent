from __future__ import annotations

import json
import operator
import sys

OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    field = params.get("field", "value")
    op = params.get("operator", "<")
    threshold = float(params["value"])
    value = float(input_data[field])
    if op not in OPS:
        raise ValueError(f"Unsupported operator: {op}")

    print(
        json.dumps(
            {
                **input_data,
                "passed": OPS[op](value, threshold),
                "value": value,
                "threshold": threshold,
                "operator": op,
                "unit": params.get("unit", input_data.get("unit", "")),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
