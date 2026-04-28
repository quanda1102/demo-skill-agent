from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    metric = params.get("metric", "ram")
    limit = int(params.get("limit", 5))
    default_processes = [
        {"pid": 1201, "user": "app", "usage": 6144.0, "name": "java-order-api", "metric": "ram"},
        {"pid": 981, "user": "root", "usage": 820.0, "name": "systemd", "metric": "ram"},
        {"pid": 2210, "user": "app", "usage": 72.5, "name": "nginx-worker", "metric": "cpu"},
        {"pid": 2230, "user": "root", "usage": 18.0, "name": "kworker", "metric": "cpu"},
    ]
    processes = params.get("mock_processes") or default_processes
    filtered = [p for p in processes if p.get("metric") == metric]
    filtered.sort(key=lambda p: float(p.get("usage", 0)), reverse=True)

    print(json.dumps({**input_data, "metric": metric, "processes": filtered[:limit]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
