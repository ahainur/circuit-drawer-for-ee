"""Read the same checked example tables shipped with the wheel and Windows app."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import sys
import sysconfig

from . import spice


CASES = {
    "boost": ("spice/boost.json",),
    "single_phase_full_bridge": ("spice/single_phase_full_bridge.json",),
    "grid_inverter_dq": ("closed_loop/grid_inverter_dq.json", "closed_loop/grid_dq.control.json"),
    "boost_apfc": ("stateful_control/apfc_hysteresis.json", "stateful_control/apfc_hysteresis.control.json"),
    "pmsm_foc": ("closed_loop/pmsm_foc.json", "closed_loop/pmsm_foc.control.json"),
}
DATA_DIR = "share/circuit-drawer/examples"
# Two grid periods allow APFC measurements after startup. The motor preview covers its first 20 ms.
DEMO_STOP = {"boost_apfc": "40m", "pmsm_foc": "20m"}


@lru_cache(maxsize=len(CASES))
def _case(case):
    files = CASES[case]
    roots = (Path(__file__).resolve().parents[2] / "examples",
             Path(getattr(sys, "_MEIPASS", sys.prefix)) / DATA_DIR,
             Path(sysconfig.get_path("data")) / DATA_DIR,
             Path(__file__).resolve().parents[1] / DATA_DIR,  # pip --target
             Path(sysconfig.get_path("data", scheme=sysconfig.get_preferred_scheme("user"))) / DATA_DIR)
    root = next((p for p in roots if (p / files[0]).is_file()), None)
    if root is None:
        raise ValueError(f"缺少随程序提供的示例工况 {case}")
    table = json.loads((root / files[0]).read_text(encoding="utf-8"))
    if len(files) == 2:
        table["closed_loop"]["control"] = json.loads((root / files[1]).read_text(encoding="utf-8"))
    return table


def drawing(case):
    return deepcopy({k:v for k,v in _case(case).items() if k not in ("simulation", "closed_loop")})


def guide(case):
    if case not in CASES:
        return None
    raw = _case(case)
    return {"devices": {e["id"]: e["kind"] for e in raw["elements"]}, "connections": raw["netlist"]}


def _nets(nets):
    groups = {}
    for pin, net in nets.items():
        groups.setdefault(net, set()).add(pin)
    return {frozenset(pins) for pins in groups.values()}


def match(layout, profile, preferred=None):
    if layout.extra.get("modules") or layout.extra.get("model_mapping"):
        return None
    kinds = {e.id: e.kind for e in layout.elements if e.kind != "port"}
    _, report = spice.connected_layout(layout, profile)
    actual_nets = _nets(report.nets)
    cases = sorted(CASES, key=lambda name: name != preferred)
    for case in cases:
        raw = _case(case)
        if kinds == {e["id"]: e["kind"] for e in raw["elements"]} and actual_nets == _nets(raw["netlist"]):
            settings = deepcopy({k: raw[k] for k in ("simulation", "closed_loop") if k in raw})
            if case in DEMO_STOP:
                settings["simulation"]["transient"]["stop"] = DEMO_STOP[case]
            return case, settings
    # The Buck/Boost matcher also recognises renamed devices and phase instances.
    for case in ("boost", "buck"):
        try:
            return case, {"simulation": spice.example_settings(layout, case, profile)}
        except ValueError:
            continue
    return None


def overlay(baseline, supplied):
    """Keep explicitly supplied values when filling missing example settings."""
    if supplied is None or supplied == "" or supplied == [] and isinstance(baseline, list):
        return deepcopy(baseline)
    if isinstance(baseline, dict) and isinstance(supplied, dict):
        result = deepcopy(baseline)
        result.update({k: overlay(result.get(k), v) for k, v in supplied.items()})
        return result
    return deepcopy(supplied)
