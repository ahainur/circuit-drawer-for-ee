"""Algorithm figures use ordinary editable control blocks and links."""
from copy import deepcopy

PROMPT = """
This document is an algorithm flowchart. Its algorithm=true marker is read-only.
Describe the sequence of operations, decisions and iteration using ordinary block boxes.
Keep blocks in reading order, top to bottom. Use .s -> .n for forward arrows, separate
side ports for labelled branches and iteration returns. Start/end blocks have
corner_radius=0.18. Write a concise action or formula inside each block, splitting
long text with LaTeX \\\\. inputs declares starts, outputs declares ends.
Iteration return edges express procedural control flow. They require no stateful
or transfer-function declarations. Preserve every branch and its condition label.
Use the user's actual equations and steps. Ask for missing algorithm details.
Plan drawing, validation and figure export. This document records the algorithm
for publication. Numerical simulation and controller code require a separate
executable controller specification. Do not add loop/code/circuit sections.
"""


def blank(name="algorithm"):
    return {"kind": "control", "algorithm": True, "name": name, "profile": "ieee-single",
            "blocks": [], "links": [], "texts": [], "regions": []}


def place(blocks, spacing):
    """Explicit candidate placement in reading order, before the shared router."""
    from .arrange import COLUMN_GAP
    y = 0.0
    width = max(b.get("size", [.6, .6])[0] for b in blocks)
    for block in blocks:
        size = block.get("size", [.6, .6])
        if block["type"] in ("block", "tag"):
            block["size"] = [width, size[1]]
        y -= size[1] / 2
        if block["type"] == "switch":
            block["pole"] = [0, y]
            block["throws"] = {name: [-.6, y + (len(block["throws"]) - 1 - 2*j)*.4]
                               for j, name in enumerate(block["throws"])}
        else:
            block["at"] = [0, y]
        y -= size[1] / 2 + COLUMN_GAP * spacing


def sequence(raw, lines, profile):
    """Prepare an explicit replacement candidate; the editor adopts it separately."""
    from .ai_structure import ControlWorkflow
    if raw.get("algorithm") is not True:
        raise ValueError("请先新建算法流程图")
    if not isinstance(lines, list) or not 2 <= len(lines) <= 32:
        raise ValueError("请填写2到32个算法步骤")
    if any(not isinstance(line, str) or not line.strip() or len(line) > 1000 for line in lines):
        raise ValueError("每一步需要1到1000字的文字或公式")
    blocks = [{"id": f"step_{i+1}", "type": "block", "text": line.strip(),
               "corner_radius": .18 if i in (0, len(lines)-1) else 0}
              for i, line in enumerate(lines)]
    proposal = {"blocks": blocks, "inputs": [blocks[0]["id"]], "outputs": [blocks[-1]["id"]],
                "links": [{"from": a["id"] + ".s", "to": b["id"] + ".n"}
                          for a, b in zip(blocks, blocks[1:])]}
    base = deepcopy(raw)
    for name in ("blocks", "links", "regions", "texts"):
        base[name] = []
    base.pop("control_ai", None)
    import json
    result = ControlWorkflow(profile, base).evaluate(json.dumps(proposal))
    if result.errors:
        raise ValueError("\n".join(result.errors))
    return result.layout.table


def validate(raw):
    if "algorithm" in raw and type(raw["algorithm"]) is not bool:
        return ["algorithm需要是布尔值"]
    if raw.get("algorithm") and any(raw.get(key) for key in ("loop", "code", "digital", "functions", "circuit")):
        return ["算法流程图保存步骤与连接，请把数值控制器保存为独立控制图"]
    return []
