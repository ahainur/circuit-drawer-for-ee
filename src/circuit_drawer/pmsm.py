"""Three-wire, sinusoidal PMSM parameter and measurement declarations.

The d axis follows the permanent-magnet flux, theta_e=0 aligns it with phase
a, and positive q current produces positive mechanical torque. Clarke/Park
use the existing amplitude-invariant convention in control.functions.
"""
from __future__ import annotations

import math


# name, editor label, example value; all quantities are per phase, in SI.
FIELDS = [
    ("resistance", "每相定子电阻 Rs (Ω，>0)", "0.4"),
    ("ld", "d 轴电感 Ld (H，>0)", "2m"),
    ("lq", "q 轴电感 Lq (H，>0)", "2m"),
    ("flux_linkage", "永磁体每相峰值磁链 ψm (Wb，>0)", "50m"),
    ("pole_pairs", "极对数 p（正整数）", "4"),
    ("inertia", "转子与负载总转动惯量 J (kg·m²，>0)", "1m"),
]
OPTIONAL_FIELDS = [
    ("friction", "粘性阻尼 B (N·m·s/rad，≥0)", "0"),
    ("load_torque", "恒定负载转矩 TL (N·m；正值减小正向转速)", "0"),
    ("initial_speed", "初始机械转速 ωm (rad/s)", "0"),
    ("initial_angle", "初始电角度 θe (rad；0 为 d 轴对齐 a 相)", "0"),
    ("initial_id", "初始 d 轴电流 id (A)", "0"),
    ("initial_iq", "初始 q 轴电流 iq (A)", "0"),
]
QUANTITIES = {
    "ia": {"label": "a 相电流（流入电机）", "unit": "A"},
    "ib": {"label": "b 相电流（流入电机）", "unit": "A"},
    "ic": {"label": "c 相电流（流入电机）", "unit": "A"},
    "id": {"label": "d 轴电流", "unit": "A"},
    "iq": {"label": "q 轴电流", "unit": "A"},
    "omega_m": {"label": "机械转速 (rad/s)", "unit": "rad_s"},
    "theta_e": {"label": "电角度（连续弧度）", "unit": "rad"},
    "torque": {"label": "电磁转矩", "unit": "Nm"},
}


def parameters(config, element: str, parse_number) -> dict[str, float]:
    """Use the caller's engineering-number parser; do not duplicate SPICE syntax."""
    required = {key for key, _, _ in FIELDS}
    defaults = {key: default for key, _, default in OPTIONAL_FIELDS}
    if not isinstance(config, dict):
        raise ValueError(f"{element}: PMSM 仿真参数必须是对象")
    missing, extra = required - config.keys(), config.keys() - required - defaults.keys()
    if missing or extra:
        details = ([f"缺少字段 {sorted(missing)}"] if missing else [])
        details += [f"未知字段 {sorted(extra)}"] if extra else []
        raise ValueError(f"{element}: " + "；".join(details))
    values = {key: parse_number(value, f"{element}.{key}")
              for key, value in {**defaults, **config}.items()}
    for key in required:
        if values[key] <= 0:
            raise ValueError(f"{element}.{key} 必须大于 0")
    if not values["pole_pairs"].is_integer():
        raise ValueError(f"{element}.pole_pairs 必须是正整数（极对数）")
    if values["friction"] < 0:
        raise ValueError(f"{element}.friction 不能小于 0")
    # Catch overflow before an exporter emits an infinite state derivative.
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError(f"{element}: PMSM 参数必须是有限数值")
    return values
