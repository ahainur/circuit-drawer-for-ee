"""Timing and token counts of one model request, for the speed log."""

from __future__ import annotations

import time


class Meter:
    """Started when the request goes out; ``text`` marks the first visible
    reply text, ``done`` adds the token counts the service reported."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.first: float | None = None

    def text(self) -> None:
        if self.first is None:
            self.first = time.monotonic()

    def done(self, input_tokens: int | None, output_tokens: int | None) -> dict:
        end = time.monotonic()
        return {"first_text_s": None if self.first is None else round(self.first - self.t0, 1),
                "total_s": round(end - self.t0, 1),
                "input_tokens": input_tokens, "output_tokens": output_tokens}


def describe(stat: dict) -> str:
    """One Chinese line for the terminal and the page."""
    first = stat.get("first_text_s")
    tok = stat.get("input_tokens"), stat.get("output_tokens")
    return (f"首个正文 {first if first is not None else '-'} 秒，共 {stat['total_s']} 秒"
            + (f"，输入 {tok[0]} / 输出 {tok[1]} tokens" if tok != (None, None) else "")
            + (f"，请求思考强度 {stat['requested_effort'] or '服务商默认'}"
               if "requested_effort" in stat else ""))
