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

DEFAULT_VALUES = {
    "available_ram_gb": 2.0,
    "cpu_usage_percent": 92.0,
    "cpu_load_average": 24.0,
    "io_util_percent": 95.0,
}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    metric = params["metric"]
    op = params.get("operator", ">")
    if op not in OPS:
        raise ValueError(f"Unsupported operator: {op}")

    value = float(params.get("mock_value", input_data.get(metric, DEFAULT_VALUES.get(metric, 0.0))))
    threshold = float(params["value"])
    print(
        json.dumps(
            {
                **input_data,
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "operator": op,
                "passed": OPS[op](value, threshold),
                "unit": params.get("unit", ""),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
