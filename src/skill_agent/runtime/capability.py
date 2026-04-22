from __future__ import annotations

from .models import CapabilityStatus, RuntimeLog, SkillStub


def check_capability(
    stub: SkillStub, requested_action: str
) -> tuple[CapabilityStatus, list[RuntimeLog]]:
    """Determine whether a skill supports the requested action.

    Rules (from policy v1):
    - Undeclared action → deny (unknown_capability when no metadata, unsupported_operation otherwise)
    - Forbidden action  → deny (unsupported_operation)
    - Declared in supported_actions → allow (supported)
    - Domain declared but no supported_actions list → unknown_capability
    """
    logs: list[RuntimeLog] = []
    action = requested_action.lower().strip()

    if not action:
        logs.append(RuntimeLog("warning", "capability", "No action specified"))
        return CapabilityStatus.unknown_capability, logs

    # No capability metadata at all
    if not stub.supported_actions and not stub.domain:
        logs.append(
            RuntimeLog(
                "warning",
                "capability",
                f"'{stub.skill_id}' declares no capability metadata — cannot verify action '{action}'",
            )
        )
        return CapabilityStatus.unknown_capability, logs

    # Forbidden actions are checked first regardless of supported_actions
    forbidden = [fa.lower() for fa in stub.forbidden_actions]
    if action in forbidden:
        logs.append(
            RuntimeLog(
                "warning",
                "capability",
                f"Action '{action}' is explicitly forbidden for '{stub.skill_id}'",
            )
        )
        return CapabilityStatus.unsupported_operation, logs

    # Explicit supported_actions list — undeclared = deny
    if stub.supported_actions:
        supported = [sa.lower() for sa in stub.supported_actions]
        if action in supported:
            logs.append(
                RuntimeLog("info", "capability", f"Action '{action}' is supported by '{stub.skill_id}'")
            )
            return CapabilityStatus.supported, logs
        else:
            logs.append(
                RuntimeLog(
                    "warning",
                    "capability",
                    f"Action '{action}' is not in supported_actions for '{stub.skill_id}'",
                )
            )
            return CapabilityStatus.unsupported_operation, logs

    # Domain is declared but no supported_actions list
    logs.append(
        RuntimeLog(
            "info",
            "capability",
            f"'{stub.skill_id}' has domain metadata but no supported_actions — capability unknown for '{action}'",
        )
    )
    return CapabilityStatus.unknown_capability, logs
