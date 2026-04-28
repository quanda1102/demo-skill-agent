from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    ip = params.get("ip") or input_data.get("ip") or "10.0.12.34"
    service = params.get("mock_service", "web_app")
    service_type = params.get("mock_service_type", service)
    excluded_services = set(params.get("excluded_services", ["database", "cloud_compute"]))
    excluded = service_type in excluded_services

    print(
        json.dumps(
            {
                **input_data,
                "ip": ip,
                "service": service,
                "service_type": service_type,
                "excluded": excluded,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
