from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MinimaxToolCall:
    id: str
    type: str
    function_name: str
    function_arguments: str

    @classmethod
    def from_dict(cls, tc: dict) -> "MinimaxToolCall":
        return cls(
            id=tc["id"],
            type=tc["type"],
            function_name=tc["function"]["name"],
            function_arguments=tc["function"]["arguments"],
        )
