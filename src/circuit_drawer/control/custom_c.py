"""Scalar C steps with explicit old/next state, shared by native exporters.

Only the table schema is checked here. Loading or drawing a table never runs C.
The target compiler checks the user's statement body when the model is built.
"""
import math
import re

KEYS = ("body", "states")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_KEYWORDS = frozenset("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while".split())


def parse(raw, inputs, outputs, what):
    errors = []
    if not isinstance(raw, dict):
        return {}, [f"{what} 必须是对象，填写 body 和可选 states"]
    unknown = sorted(set(raw) - set(KEYS))
    if unknown:
        errors.append(f"{what}：未知字段 {', '.join(unknown)}；可用字段 {', '.join(KEYS)}")
    body, states = raw.get("body"), raw.get("states", {})
    if not isinstance(body, str) or not body.strip() or "\0" in body:
        errors.append(f"{what}.body 必须是非空 C 语句且不能含 NUL 字符")
    if not isinstance(states, dict):
        errors.append(f"{what}.states 须把状态名映射到有限初值")
        states = {}
    names = []
    for label, values in (("in", inputs), ("out", outputs), ("states", states)):
        if not isinstance(values, dict) or label != "states" and not values:
            errors.append(f"{what}.{label} 须为非空对象，使用具名标量信号" if label != "states"
                          else f"{what}.states 必须是对象")
            continue
        for name in values:
            if not isinstance(name, str) or not _ID.fullmatch(name) or name in _KEYWORDS or name.startswith(("next_", "cd_")):
                errors.append(f"{what}：无效 C 名称 {name!r}；以字母开头，使用字母、数字和下划线，避开 C 关键字及 next_/cd_ 前缀")
            if name in names:
                errors.append(f"{what}：输入、输出、状态名不能重复（{name!r}）")
            names.append(name)
    for name, value in states.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append(f"{what}.states.{name} 须为有限初值")
    return {"body": body, "states": {} if errors else dict(sorted(states.items()))}, errors


def is_step(step):
    return step.kind == "fn" and step.data.get("fn") == "c"


def field_reference():
    return (f"functions.<block>: fn='c', in/out map named scalar C variables to existing ports; "
            f"c keys: {', '.join(KEYS)}. body is nonempty C99 statements, states maps names to finite initial numbers. "
            "Read input/old state names; assign outputs and next_<state>. All names differ; use a letter first, "
            "then letters/digits/underscores, no C keywords or next_/cd_ prefixes. "
            "Use code.fs and code.arith='double'; no code.blocks overrides for C blocks. "
            "Use block.output names in code.outputs and closed_loop.outputs. "
            "Pure calculations only, explicit state, no static state or external effects. "
            "Native PLECS C-Script / Simulink C Function discrete export and C code generation only; "
            "ngspice, Python evaluation, digital timing/quantization and linear analysis do not support C bodies. "
            "Keep body/states/ports on structural edits; AI exchange does not accept new C source.")


def statements(spec, inputs, outputs, old, next_):
    """Bind user names in a local scope; targets decide when next state commits."""
    reads, writes = list(inputs.values()) + list(old.values()), list(outputs.values()) + list(next_.values())
    bridge = 'cd_bind'
    while any(bridge in expr for expr in reads + writes):
        bridge += '_'
    # User aliases such as s/in must not hide the enclosing generated names.
    lines = ["{", f"const double {bridge}_read[] = {{{', '.join(reads)}}};",
             f"double {bridge}_write[{len(writes)}];", "{"]
    lines += [f"const double {name} = {bridge}_read[{i}];" for i, name in enumerate(inputs)]
    lines += [f"const double {name} = {bridge}_read[{len(inputs)+i}];\ndouble next_{name} = {name};"
              for i, name in enumerate(old)]
    lines += [f"double {name} = 0.0;" for name in outputs]
    lines += ["/* user C body */", spec["body"], "/* end user C body */"]
    values = list(outputs) + ['next_'+name for name in next_]
    lines += [f"{bridge}_write[{i}] = {name};" for i, name in enumerate(values)]
    lines += ["}"] + [f"{expr} = {bridge}_write[{i}];" for i, expr in enumerate(writes)]
    return "\n".join(lines + ["}"])
