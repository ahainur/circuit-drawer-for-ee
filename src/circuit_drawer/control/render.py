"""Validate-then-draw for control diagrams, and the bundled example tables."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from ..compile import FORMATS, build
from ..profiles import get_profile
from .check import Report, check
from .emit import emit

# name -> one-line title; the tables live in templates/<name>.json
TEMPLATES = {
    "single_loop": "单闭环电压控制 (PI + 限幅 + PWM)",
    "dual_loop": "电压外环、电流内环双闭环控制",
    "buck_charger": "充电机 Buck 控制器：CV/CC-CP 选择、乘除点、A/B 切换",
    "foc_pmsm": "PMSM 磁场定向控制 (FOC)：转速外环、dq 电流 PI、Clarke/Park、SVPWM",
    "mpc_fcs": "有限控制集模型预测电流控制 (FCS-MPC)：预测模型、代价函数、最优开关选择",
    "buck_vmode": "Buck 电压模式单环：Type III 补偿、PWM 增益、功率级、分压反馈（含环路参数）",
    "buck_digital": "1 MHz Buck 数字控制延迟与量化",
    "gfm_sync": "两机相角同步小信号模型",
}


class Rejected(ValueError):
    """The table failed the checker; nothing was drawn."""

    def __init__(self, report: Report):
        self.report = report
        super().__init__("control diagram rejected:\n" + "\n".join(f"  {e}" for e in report.errors))


@dataclass
class Result:
    report: Report
    files: dict[str, Path]


def available(directory: Path | None = None) -> dict[str, str]:
    """Bundled examples and the user's saved templates: name -> title."""
    from .library import user_templates
    out = dict(TEMPLATES)
    for name, (title, path) in user_templates(directory).items():
        out.setdefault(name, f"{title}  [saved: {path}]")
    return out


def template(name: str, directory: Path | None = None) -> dict[str, Any]:
    if name in TEMPLATES:
        text = resources.files(__package__).joinpath("templates", f"{name}.json").read_text("utf-8")
        return json.loads(text)
    from .library import user_templates
    saved = user_templates(directory).get(name)
    if saved is None:
        raise KeyError(f"unknown control template {name!r}; "
                       f"choose from {sorted(available(directory))}")
    return json.loads(saved[1].read_text(encoding="utf-8"))


def load(source: str) -> dict[str, Any]:
    """A template name or a JSON file."""
    path = Path(source)
    if path.suffix.lower() == ".json" or path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return template(source)


def render(raw: dict[str, Any], profile: str | None = None, out_dir: str | Path = "out",
           name: str | None = None, formats: tuple[str, ...] = FORMATS, mono: bool = False,
           grid: bool = False) -> Result:
    """Check, then write the figure.  Refuses a table with errors."""
    prof = get_profile(profile or str(raw.get("profile") or "ieee-double"))
    rep = check(raw, prof)
    if not rep.ok:
        raise Rejected(rep)
    tex = emit(rep, prof, mono=mono, grid=grid)
    stem = name or rep.diagram.name
    return Result(rep, build(tex, Path(out_dir), stem, formats))


def bode(raw: dict[str, Any], profile: str = "ieee-single", out_dir: str | Path = "out",
         name: str | None = None, formats: tuple[str, ...] = FORMATS,
         mono: bool = False) -> Result:
    """Check the table, then plot its loop gains and closed-loop responses."""
    from .bode import bode_tex
    from .loop import curves
    prof = get_profile(profile)
    rep = check(raw, get_profile(str(raw.get("profile") or "ieee-double")))
    if not rep.ok:
        raise Rejected(rep)
    data, freq = curves(rep)
    if not data:
        raise ValueError("没有可画的 Bode 曲线（nothing to plot）：请给出 loop.breaks、loop.closed 或 loop.plant；"
                         "dq 变换、锁相环这类函数方块不参与线性分析")
    stem = name or f"{rep.diagram.name}_bode"
    return Result(rep, build(bode_tex(data, freq, prof, mono), Path(out_dir), stem, formats))
