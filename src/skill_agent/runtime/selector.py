from __future__ import annotations

import re

from .models import RuntimeLog, SelectionConfig, SelectionStatus, SkillStub


def select_skill(
    stubs: list[SkillStub],
    user_request: str,
    config: SelectionConfig | None = None,
    requested_action: str = "",
) -> tuple[SkillStub | None, SelectionStatus, list[RuntimeLog]]:
    cfg = config or SelectionConfig()
    logs: list[RuntimeLog] = []

    if not stubs:
        logs.append(RuntimeLog("warning", "selection", "No skills available"))
        return None, SelectionStatus.no_match, logs

    request_tokens = _tokenize(user_request)
    if not request_tokens:
        logs.append(RuntimeLog("warning", "selection", "Empty or unparseable request"))
        return None, SelectionStatus.no_match, logs

    scored: list[tuple[int, SkillStub]] = []
    for stub in stubs:
        candidate_tokens = _tokenize(f"{stub.name} {stub.description}")
        score = len(request_tokens & candidate_tokens)
        scored.append((score, stub))
        logs.append(RuntimeLog("info", "selection", f"  '{stub.skill_id}' score={score}"))

    # Filter out skills that explicitly forbid the requested action before ranking
    if requested_action:
        action_lower = requested_action.lower()
        eligible = [
            (score, stub) for score, stub in scored
            if action_lower not in [a.lower() for a in stub.forbidden_actions]
        ]
        if eligible:
            n_removed = len(scored) - len(eligible)
            if n_removed:
                logs.append(RuntimeLog(
                    "info", "selection",
                    f"Excluded {n_removed} skill(s) that forbid action '{requested_action}'",
                ))
            scored = eligible
        # else: every candidate forbids the action — keep all and let capability layer handle it

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_stub = scored[0]

    if best_score < cfg.min_score:
        logs.append(RuntimeLog("warning", "selection", "No skill matched the request (all scores = 0)"))
        return None, SelectionStatus.no_match, logs

    # Check for ambiguity: top two both above confidence threshold and within margin
    if len(scored) >= 2:
        second_score, _ = scored[1]
        both_confident = (
            best_score >= cfg.low_confidence_threshold
            and second_score >= cfg.low_confidence_threshold
        )
        within_margin = (best_score - second_score) <= cfg.ambiguity_margin
        if both_confident and within_margin:
            logs.append(
                RuntimeLog(
                    "warning",
                    "selection",
                    f"Ambiguous match: top two skills score {best_score} vs {second_score} — ask for clarification",
                )
            )
            return best_stub, SelectionStatus.ambiguous, logs

    if best_score < cfg.low_confidence_threshold:
        logs.append(
            RuntimeLog(
                "warning",
                "selection",
                f"Low-confidence match: '{best_stub.skill_id}' (score={best_score}) — consider asking for clarification",
            )
        )
        return best_stub, SelectionStatus.low_confidence, logs

    logs.append(RuntimeLog("info", "selection", f"Selected '{best_stub.skill_id}' (score={best_score})"))
    return best_stub, SelectionStatus.matched, logs


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))
