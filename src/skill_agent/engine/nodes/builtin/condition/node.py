from __future__ import annotations

import json
import operator
import os
import re
import sys
from typing import Any

try:
    from src.skill_agent.providers.openai_provider import OpenAIProvider
    from src.skill_agent.providers.provider import MinimaxProvider
except Exception:
    OpenAIProvider = None
    MinimaxProvider = None


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

    condition = str(params.get("condition") or "").strip()
    true_branch = str(params.get("true_branch") or "true")
    false_branch = str(params.get("false_branch") or "false")

    result, source = evaluate_condition(params=params, input_data=input_data)
    branch = true_branch if result["matched"] else false_branch
    print(
        json.dumps(
            {
                **input_data,
                "condition": condition,
                "matched": result["matched"],
                "branch": branch,
                "true_branch": true_branch,
                "false_branch": false_branch,
                "reason": result["reason"],
                "condition_source": source,
            },
            ensure_ascii=False,
        )
    )


def evaluate_condition(params: dict[str, Any], input_data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    provider = _provider_from_env()
    if provider is not None and params.get("condition"):
        try:
            result = _evaluate_with_llm(provider, params=params, input_data=input_data)
            if isinstance(result.get("matched"), bool):
                return {"matched": result["matched"], "reason": str(result.get("reason") or "")}, "llm"
        except Exception:
            pass
    return _evaluate_fallback(params=params, input_data=input_data), "fallback"


def _evaluate_with_llm(provider, *, params: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    response = provider.invoke(
        [
            {
                "role": "system",
                "content": (
                    "Bạn là condition node trong workflow engine. "
                    "Nhiệm vụ: đánh giá điều kiện if/else dựa trên input JSON. "
                    "Chỉ trả về JSON object hợp lệ, không markdown, không giải thích ngoài JSON. "
                    'Schema bắt buộc: {"matched": boolean, "reason": "một câu ngắn bằng tiếng Việt"}.'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "condition": params.get("condition"),
                        "input": input_data,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ],
        tools=None,
    )
    content = str(response.get("content") or "").strip()
    return json.loads(_strip_code_fence(content))


def _evaluate_fallback(params: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    field = str(params.get("field") or "")
    op = str(params.get("operator") or "")
    threshold = params.get("value")

    if field and op in OPS and threshold is not None:
        current = _read_field(input_data, field)
        matched = OPS[op](float(current), float(threshold))
        return {
            "matched": bool(matched),
            "reason": f"{field}={current} {op} {threshold} là {bool(matched)}.",
        }

    parsed = _parse_simple_condition(str(params.get("condition") or ""), input_data)
    if parsed is not None:
        return parsed

    return {
        "matched": bool(input_data.get("passed") or input_data.get("satisfied") or input_data.get("matched")),
        "reason": "Fallback dùng field passed/satisfied/matched từ input.",
    }


def _parse_simple_condition(condition: str, input_data: dict[str, Any]) -> dict[str, Any] | None:
    match = re.search(r"([A-Za-z_][\w.]*)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)", condition)
    if not match:
        return None
    field, op, raw_threshold = match.groups()
    if op not in OPS:
        return None
    current = _read_field(input_data, field)
    threshold = float(raw_threshold)
    matched = OPS[op](float(current), threshold)
    return {
        "matched": bool(matched),
        "reason": f"{field}={current} {op} {threshold:g} là {bool(matched)}.",
    }


def _read_field(data: dict[str, Any], field: str) -> Any:
    current: Any = data
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Input field '{field}' not found")
        current = current[part]
    return current


def _strip_code_fence(content: str) -> str:
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content.strip(), flags=re.IGNORECASE).strip()
        content = re.sub(r"```$", "", content).strip()
    return content


def _provider_from_env():
    provider_config = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider_config == "openai":
        if OpenAIProvider is None or not os.environ.get("OPENAI_API_KEY"):
            return None
        return OpenAIProvider(temperature=0.0, top_p=0.9, max_tokens=300)
    if MinimaxProvider is not None and os.environ.get("MINIMAX_ENDPOINT"):
        return MinimaxProvider(temperature=0.0, top_p=0.9, max_tokens=300)
    return None


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
