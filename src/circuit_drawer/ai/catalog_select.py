"""Conservative selection of optional examples; keep all schema and edit rules."""
from __future__ import annotations
import re

# Semantic groups are hints for example selection, never topology rewrite rules.
_GROUPS = (
    (("buck", "降压"), ("buck", "single_loop", "dual_loop")),
    (("双环", "dual", "current loop", "电流环"), ("dual_loop", "buck")),
    (("单环", "single", "voltage loop", "电压环"), ("single_loop", "buck_vmode")),
    (("foc", "pmsm", "电机", "motor", "clarke", "park"), ("foc", "pmsm")),
    (("gfm", "grid", "并网", "构网"), ("gfm", "grid")),
    (("mpc", "预测控制"), ("mpc",)),
    (("数字", "digital", "adc"), ("digital",)),
)


def select_examples(catalog, description):
    """Named examples are always retained; unknown requests keep the full catalog.

    Filtering an unfamiliar request aggressively risks losing a user-authored
    function or snippet. Those stay available unless a relevant match is found.
    """
    if not description or not description.strip():
        return catalog
    text = description.casefold()
    needles = {needle for words, related in _GROUPS if any(w in text for w in words) for needle in related}
    selected = {name: value for name, value in catalog.items()
                if re.search(r"(?<![\w])" + re.escape(name.casefold()) + r"(?![\w])", text)
                or any(needle in name.casefold() for needle in needles)}
    # User entries are not discarded: they may provide required functions even
    # when the description refers to them through their displayed title.
    for name, value in catalog.items():
        title = str(value.get("title", ""))
        if title and title.casefold() in text:
            selected[name] = value
    return selected or catalog
