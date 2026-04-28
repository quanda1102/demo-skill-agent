from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    condition_field = params.get("condition_field", "satisfied")
    sent = bool(input_data.get(condition_field, True))
    message = params.get("message", "Workflow alert")
    output = {
        **input_data,
        "sent": sent,
        "message": message,
        "severity": params.get("severity", "warning"),
        "alert_id": f"alert_{uuid.uuid4().hex[:8]}" if sent else None,
        "sent_at": datetime.now(timezone.utc).isoformat() if sent else None,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
