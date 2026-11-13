"""One drawing frame references the circuit's existing closed-loop controller.

The reference remains ``closed_loop.control`` (inline table or relative path).
Only the frame id and display state are stored here, never a controller copy.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
import base64
import json
import tempfile

from .blocks import Block, safe_blocks
from .geometry import Box, Scale, block_rect, control_view_origin, has_wide
from .profiles import Profile

KEYS = ("block", "display")
DISPLAYS = ("collapsed", "expanded")
PAD_CM = 0.12  # printed clearance between the controller's ink and its frame


def problems(layout):
    if "control_view" not in layout.extra:
        return []
    cfg = layout.extra["control_view"]
    if cfg == {}:
        return []
    if not isinstance(cfg, dict):
        return ["control_view 必须是对象，填写 block 和 display"]
    unknown = sorted(set(cfg) - set(KEYS))
    if unknown:
        return [f"control_view：未知字段 {', '.join(unknown)}；可用字段 {', '.join(KEYS)}"]
    if not isinstance(cfg.get("block"), str) or not any(b.id == cfg["block"] for b in safe_blocks(layout)):
        return ["control_view.block 须引用本图 blocks 中已有的方框 id"]
    if cfg.get("display", "collapsed") not in DISPLAYS:
        return [f"control_view.display 须为 {', '.join(DISPLAYS)}"]
    loop = layout.extra.get("closed_loop")
    if not isinstance(loop, dict) or not loop.get("control"):
        return ["control_view 需要 closed_loop.control；先设置闭环控制表"]
    return []


@dataclass
class View:
    block: Block
    display: str
    raw: dict
    profile: Profile
    report: object = None
    bounds: Box | None = None

    def tikz(self, scale, mono=False):
        from .control.emit import body, _n
        origin = control_view_origin(self.block, scale, self.bounds)
        unit = _n(self.report.geometry.s)
        return (rf"\begin{{scope}}[shift={{({_n(origin[0])}cm,{_n(origin[1])}cm)}}, "
                rf"x={unit}cm,y={unit}cm,line join=round]" + "\n"
                + body(self.report, self.profile, mono) + "\\end{scope}\n")


def prepare(layout, profile, scale, base_dir=None):
    """Validate the same source used by simulation, then fit spacing, never type."""
    if "control_view" not in layout.extra:
        return None
    errors = problems(layout)
    if errors:
        raise ValueError("；".join(errors))
    from .closed_loop import controller
    from .control.check import check, extent
    cfg = layout.extra["control_view"]
    if not cfg:
        return None
    try:
        raw, _, _ = controller(layout.extra["closed_loop"]["control"], base_dir)
    except KeyError as exc:
        raise ValueError("closed_loop.control：" + str(exc)) from None
    block = next(b for b in safe_blocks(layout) if b.id == cfg["block"])
    view = View(block, cfg.get("display", "collapsed"), raw, profile)
    if view.display == "collapsed":
        return view
    frame = block_rect(block, scale)
    # Translation must not invalidate the cache through subtraction roundoff.
    width = round(frame.x1 - frame.x0 - 2 * PAD_CM, 8)
    height = round(frame.y1 - frame.y0 - 2 * PAD_CM, 8)
    if width <= 0 or height <= 0:
        raise ValueError(f"控制方框 {block.id} 太小；请增大方框或折叠显示")
    view.profile = replace(profile, width_cm=width)
    view.report = check(raw, view.profile)
    view.bounds = extent(view.report.diagram, view.report.geometry, view.profile)
    if not view.report.ok or view.bounds is None or view.bounds.y1 - view.bounds.y0 > height:
        detail = "；".join(view.report.errors)
        raise ValueError(f"控制方框 {block.id} 无法完整展开；请增大方框或折叠显示" + (f"：{detail}" if detail else ""))
    return view


def texts(view):
    from .control.emit import _texts
    return _texts(view.report.diagram) if view and view.display == "expanded" else []


def preview(view, scale):
    """A vector preview of the same checked ink, cached independently of position."""
    frame = block_rect(view.block, scale)
    return _preview(json.dumps(view.raw, sort_keys=True), view.profile,
                    round(frame.x1 - frame.x0, 8), round(frame.y1 - frame.y0, 8))


@lru_cache(maxsize=16)
def _preview(source, profile, width, height):
    from .blocks import Block
    from .compile import build
    from .control.check import check, extent
    from .emit import preamble
    raw = json.loads(source)
    report = check(raw, profile)
    view = View(Block("control", 0, 0, width, height), "expanded", raw, profile,
                report, extent(report.diagram, report.geometry, profile))
    cjk = any(has_wide(t) for t in texts(view))
    tex = preamble(profile, cjk).replace("border=1pt", "border=0pt").replace(
        r"\usepackage{circuitikz}", "\\usepackage{circuitikz}\n\\usetikzlibrary{arrows.meta}")
    tex += ("\\begin{tikzpicture}[x=1cm,y=1cm,line width=" + str(profile.line_pt) + "pt]\n"
            + f"\\path[use as bounding box] (0,0) rectangle ({width},{height});\n"
            + view.tikz(Scale(1, 1)) + "\\end{tikzpicture}\n"
            + ("\\end{CJK}\n" if cjk else "") + "\\end{document}\n")
    with tempfile.TemporaryDirectory() as folder:
        files = build(tex, Path(folder), "control", ("svg",))
        return "data:image/svg+xml;base64," + base64.b64encode(files["svg"].read_bytes()).decode()
