"""A checked subset of the custom C block body, executed outside a C compiler.

Native PLECS / Simulink exports and generated C still compile the user's body
as written. ngspice and the Python reference cannot compile C, so they run the
same body through this subset: ``double`` and ``int`` scalars, assignments,
``if``/``else``, ``for``/``while`` with ``break``/``continue``, the C operators
on scalars and a short list of <math.h> functions. Anything outside the subset
is rejected with the construct named, never approximated.

Semantics follow C for these constructs:

1. ``int`` values truncate toward zero on assignment and cast; ``int / int``
   and ``%`` truncate toward zero as in C99. Literals without a decimal point
   or exponent are ``int``.
2. ``&&``, ``||`` and ``?:`` evaluate their right side only when needed.
3. A comparison or logical operator yields int 0 or 1.
4. Each step starts with outputs at 0 and ``next_<state>`` equal to the old
   state. The block commits ``next_<state>`` after its body, once per sample.

Known differences, written into the field reference: the subset requires an
initializer on every declaration (C leaves it indeterminate), ``int`` is not
wrapped at 32 bits, and ngspice stops with an error on a zero divisor or a
math domain error where C would return inf or NaN.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

MAX_ITERATIONS = 1_000_000   # loop passes per sample in the Python reference
TYPES = ("double", "int")
FUNCTIONS = {
    # name: (arity, Python, ngspice template)
    "fabs": (1, abs, "abs({0})"),
    "sqrt": (1, math.sqrt, "sqrt({0})"),
    "exp": (1, math.exp, "exp({0})"),
    "log": (1, math.log, "ln({0})"),
    "sin": (1, math.sin, "sin({0})"),
    "cos": (1, math.cos, "cos({0})"),
    "atan": (1, math.atan, "atan({0})"),
    "tanh": (1, math.tanh, "tanh({0})"),
    "floor": (1, math.floor, "floor({0})"),
    "ceil": (1, math.ceil, "ceil({0})"),
    "fmin": (2, min, "min({0}, {1})"),
    "fmax": (2, max, "max({0}, {1})"),
    "pow": (2, math.pow, "(({0}) ^ ({1}))"),
}
_TOKEN = re.compile(r"""
    (?P<space>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)(?P<suffix>[A-Za-z_]\w*)?
  | (?P<name>[A-Za-z_]\w*)
  | (?P<op>\+\+|--|\+=|-=|\*=|/=|%=|==|!=|<=|>=|&&|\|\||[-+*/%<>=!?:;,(){}\[\]])
""", re.S | re.X)
_KEYWORDS = {"if", "else", "for", "while", "break", "continue", "const", *TYPES}
_UNSUPPORTED_WORDS = {"return", "do", "switch", "case", "goto", "static", "float", "long", "short",
                      "unsigned", "signed", "char", "struct", "union", "enum", "sizeof", "void",
                      "extern", "register", "volatile", "typedef", "default"}
_COMPARE = {"<": "lt", ">": "gt", "<=": "le", ">=": "ge", "==": "eq", "!=": "ne"}


class CSubsetError(ValueError):
    pass


@dataclass
class Sym:
    name: str
    index: int
    type: str
    role: str          # input, state, next, output, local
    const: bool = False


@dataclass
class Expr:
    kind: str          # num, var, unary, binary, cond, call, cast
    type: str
    value: object = None
    args: list = field(default_factory=list)


@dataclass
class Stmt:
    kind: str          # block, assign, if, loop, break, continue
    target: Sym | None = None
    expr: Expr | None = None
    body: list = field(default_factory=list)
    other: list = field(default_factory=list)
    step: list = field(default_factory=list)


@dataclass
class Program:
    inputs: list[Sym]
    states: list[Sym]
    nexts: list[Sym]
    outputs: list[Sym]
    symbols: list[Sym]
    body: list[Stmt]
    initial: dict[str, float]


def _tokens(text: str):
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            ch = text[pos]
            line = text.count("\n", 0, pos) + 1
            what = "预处理指令" if ch == "#" else f"字符 {ch!r}"
            raise CSubsetError(f"第 {line} 行：不支持{what}")
        pos = m.end()
        if m.group("space"):
            continue
        line = text.count("\n", 0, m.start()) + 1
        if m.group("number"):
            if m.group("suffix"):
                raise CSubsetError(f"第 {line} 行：数字 {m.group(0)!r} 带后缀或不是十进制；请写成 1.0、2e-3 这样的十进制数")
            raw = m.group("number")
            out.append(("number", raw, line))
        elif m.group("name"):
            out.append(("name", m.group("name"), line))
        else:
            out.append(("op", m.group("op"), line))
    out.append(("end", "", text.count("\n") + 1))
    return out


class _Parser:
    def __init__(self, text, inputs, outputs, states):
        self.toks = _tokens(text)
        self.pos = 0
        self.symbols: list[Sym] = []
        self.scopes: list[dict[str, Sym]] = [{}]
        self.loops = 0
        self.inputs = [self._declare(n, "double", "input", True) for n in inputs]
        self.states = [self._declare(n, "double", "state", True) for n in states]
        self.nexts = [self._declare("next_" + n, "double", "next") for n in states]
        self.outputs = [self._declare(n, "double", "output") for n in outputs]

    # ---- tokens
    def peek(self, offset=0):
        return self.toks[min(self.pos + offset, len(self.toks) - 1)]

    def take(self):
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def error(self, message, tok=None):
        tok = tok or self.peek()
        raise CSubsetError(f"第 {tok[2]} 行：{message}")

    def expect(self, value):
        tok = self.take()
        if tok[1] != value or tok[0] == "number":
            self.error(f"此处应为 {value!r}，实际为 {tok[1] or '结尾'!r}", tok)
        return tok

    def at(self, value):
        tok = self.peek()
        return tok[0] in ("op", "name") and tok[1] == value

    # ---- names
    def _declare(self, name, type_, role, const=False):
        sym = Sym(name, len(self.symbols), type_, role, const)
        self.symbols.append(sym)
        self.scopes[-1][name] = sym
        return sym

    def lookup(self, tok):
        for scope in reversed(self.scopes):
            if tok[1] in scope:
                return scope[tok[1]]
        if tok[1] in FUNCTIONS:
            self.error(f"{tok[1]} 是函数，须写成 {tok[1]}(...)", tok)
        self.error(f"未声明的名称 {tok[1]!r}；可用输入、状态、next_状态、输出和已声明的局部变量", tok)

    # ---- statements
    def program(self):
        body = []
        while self.peek()[0] != "end":
            body.append(self.statement())
        return body

    def statement(self) -> Stmt:
        tok = self.peek()
        if tok[0] == "name" and tok[1] in _UNSUPPORTED_WORDS:
            hint = "；直接给输出赋值，语句执行到结尾即完成本拍" if tok[1] == "return" else \
                "；ngspice 与 Python 执行子集只支持 double、int 标量和 if/for/while"
            self.error(f"不支持 {tok[1]}{hint}")
        if self.at("{"):
            self.take()
            self.scopes.append({})
            body = []
            while not self.at("}"):
                if self.peek()[0] == "end":
                    self.error("缺少 '}'")
                body.append(self.statement())
            self.take()
            self.scopes.pop()
            return Stmt("block", body=body)
        if self.at(";"):
            self.take()
            return Stmt("block")
        if self.at("const") or self.at("double") or self.at("int"):
            stmt = self.declaration()
            self.expect(";")
            return stmt
        if self.at("if"):
            self.take()
            self.expect("(")
            cond = self.expression()
            self.expect(")")
            then = self.statement()
            other = None
            if self.at("else"):
                self.take()
                other = self.statement()
            return Stmt("if", expr=cond, body=[then], other=[other] if other else [])
        if self.at("while"):
            self.take()
            self.expect("(")
            cond = self.expression()
            self.expect(")")
            self.loops += 1
            body = self.statement()
            self.loops -= 1
            return Stmt("loop", expr=cond, body=[body])
        if self.at("for"):
            self.take()
            self.expect("(")
            self.scopes.append({})
            init = []
            if not self.at(";"):
                init = [self.declaration()] if self.at("const") or self.at("double") or self.at("int") \
                    else self.simple_list(";")
            self.expect(";")
            cond = Expr("num", "int", 1) if self.at(";") else self.expression()
            self.expect(";")
            step = [] if self.at(")") else self.simple_list(")")
            self.expect(")")
            self.loops += 1
            body = self.statement()
            self.loops -= 1
            self.scopes.pop()
            return Stmt("block", body=[*init, Stmt("loop", expr=cond, body=[body], step=step)])
        if self.at("break") or self.at("continue"):
            word = self.take()
            if not self.loops:
                self.error(f"{word[1]} 只能出现在 for/while 循环里", word)
            self.expect(";")
            return Stmt(word[1])
        stmts = self.simple_list(";")
        self.expect(";")
        return stmts[0] if len(stmts) == 1 else Stmt("block", body=stmts)

    def declaration(self) -> Stmt:
        const = False
        if self.at("const"):
            self.take()
            const = True
        type_tok = self.take()
        if type_tok[1] not in TYPES:
            self.error("声明须为 double 或 int", type_tok)
        stmts = []
        while True:
            tok = self.take()
            if tok[0] != "name" or tok[1] in _KEYWORDS or tok[1] in _UNSUPPORTED_WORDS:
                self.error(f"无效变量名 {tok[1]!r}", tok)
            if tok[1] in self.scopes[-1]:
                self.error(f"同一作用域里重复声明 {tok[1]!r}", tok)
            if tok[1].startswith("cd_") or tok[1] in FUNCTIONS:
                self.error(f"变量名 {tok[1]!r} 与保留名称冲突", tok)
            if self.at("["):
                self.error("不支持数组")
            if not self.at("="):
                self.error(f"局部变量 {tok[1]!r} 须在声明时赋初值，例如 double x = 0.0;", tok)
            self.take()
            value = self.expression()
            sym = self._declare(tok[1], type_tok[1], "local", const)
            stmts.append(Stmt("assign", target=sym, expr=value))
            if not self.at(","):
                break
            self.take()
        return stmts[0] if len(stmts) == 1 else Stmt("block", body=stmts)

    def simple_list(self, stop):
        out = [self.simple()]
        while self.at(","):
            self.take()
            out.append(self.simple())
        if not self.at(stop):
            self.error(f"此处应为 {stop!r}；一条语句只能是赋值、++/--、if、for、while、break 或 continue")
        return out

    def simple(self) -> Stmt:
        tok = self.peek()
        if self.at("++") or self.at("--"):
            op = self.take()[1]
            sym = self.assignable(self.take())
            return Stmt("assign", target=sym, expr=self._binary(op[0], Expr("var", sym.type, sym), Expr("num", "int", 1)))
        if tok[0] != "name":
            self.error(f"语句须以变量名开头，实际为 {tok[1]!r}")
        if self.peek(1)[1] == "(" and tok[1] in FUNCTIONS:
            self.error(f"单独调用 {tok[1]}() 没有作用；请把结果赋给变量")
        sym = self.assignable(self.take())
        op = self.take()
        if op[1] in ("++", "--"):
            return Stmt("assign", target=sym, expr=self._binary(op[1][0], Expr("var", sym.type, sym), Expr("num", "int", 1)))
        if op[1] == "=":
            value = self.expression()
        elif op[1] in ("+=", "-=", "*=", "/=", "%="):
            value = self._binary(op[1][0], Expr("var", sym.type, sym), self.expression())
        else:
            self.error(f"{sym.name} 后应为赋值运算符，实际为 {op[1]!r}", op)
        if self.at("="):
            self.error("不支持连续赋值；请拆成多条语句")
        return Stmt("assign", target=sym, expr=value)

    def assignable(self, tok) -> Sym:
        if tok[0] != "name":
            self.error(f"只能给变量赋值，实际为 {tok[1]!r}", tok)
        sym = self.lookup(tok)
        if sym.role == "input":
            self.error(f"{sym.name} 是输入，只读", tok)
        if sym.role == "state":
            self.error(f"{sym.name} 是本拍旧状态，只读；请给 next_{sym.name} 赋下一拍的值", tok)
        if sym.const:
            self.error(f"{sym.name} 声明为 const，不能再赋值", tok)
        return sym

    # ---- expressions
    def expression(self) -> Expr:
        cond = self.logical_or()
        if not self.at("?"):
            if self.at("=") or self.at("++") or self.at("--") or (self.peek()[1].endswith("=") and self.peek()[1] not in ("==", "!=", "<=", ">=") and self.peek()[0] == "op"):
                self.error("表达式里不能赋值或自增；请写成单独语句")
            return cond
        self.take()
        a = self.expression()
        self.expect(":")
        b = self.expression()
        return Expr("cond", "int" if a.type == b.type == "int" else "double", None, [cond, a, b])

    def _chain(self, ops, below):
        left = below()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            op = self.take()[1]
            left = self._binary(op, left, below())
        return left

    def logical_or(self):
        return self._chain(("||",), self.logical_and)

    def logical_and(self):
        return self._chain(("&&",), self.equality)

    def equality(self):
        return self._chain(("==", "!="), self.relation)

    def relation(self):
        return self._chain(("<", ">", "<=", ">="), self.additive)

    def additive(self):
        return self._chain(("+", "-"), self.term)

    def term(self):
        return self._chain(("*", "/", "%"), self.unary)

    def _binary(self, op, a, b) -> Expr:
        if op == "%" and not (a.type == b.type == "int"):
            self.error("% 只能用于 int；double 取余请用 x - floor(x/y)*y 等显式公式")
        if op in _COMPARE or op in ("&&", "||"):
            type_ = "int"
        else:
            type_ = "int" if a.type == b.type == "int" else "double"
        return Expr("binary", type_, op, [a, b])

    def unary(self) -> Expr:
        if self.peek()[0] == "op" and self.peek()[1] in ("-", "+", "!"):
            op = self.take()[1]
            x = self.unary()
            if op == "+":
                return x
            return Expr("unary", "int" if op == "!" else x.type, op, [x])
        if self.at("++") or self.at("--"):
            self.error("表达式里不能自增或自减；请写成单独语句")
        if self.at("(") and self.peek(1)[1] in TYPES and self.peek(2)[1] == ")":
            self.take()
            type_ = self.take()[1]
            self.take()
            return Expr("cast", type_, None, [self.unary()])
        return self.primary()

    def primary(self) -> Expr:
        tok = self.take()
        if tok[0] == "number":
            text = tok[1]
            if re.fullmatch(r"\d+", text):
                if len(text) > 1 and text[0] == '0':
                    self.error("只支持十进制整数，请去掉前导 0", tok)
                value = int(text)
                if value > 2 ** 31 - 1:
                    self.error(f"整数 {text} 超出 int 范围；请写成 {text}.0", tok)
                return Expr("num", "int", value)
            value = float(text)
            if not math.isfinite(value):
                self.error(f"数值 {text} 必须有限", tok)
            return Expr("num", "double", value)
        if tok[0] == "op" and tok[1] == "(":
            x = self.expression()
            self.expect(")")
            return x
        if tok[0] == "name":
            if tok[1] in _KEYWORDS or tok[1] in _UNSUPPORTED_WORDS:
                self.error(f"表达式里不能出现 {tok[1]}", tok)
            if self.at("("):
                if tok[1] not in FUNCTIONS:
                    self.error(f"不支持函数 {tok[1]}()；可用 {', '.join(FUNCTIONS)}", tok)
                self.take()
                args = []
                if not self.at(")"):
                    args.append(self.expression())
                    while self.at(","):
                        self.take()
                        args.append(self.expression())
                self.expect(")")
                if len(args) != FUNCTIONS[tok[1]][0]:
                    self.error(f"{tok[1]}() 需要 {FUNCTIONS[tok[1]][0]} 个参数", tok)
                return Expr("call", "double", tok[1], args)
            if self.at("["):
                self.error("不支持数组或下标")
            sym = self.lookup(tok)
            return Expr("var", sym.type, sym)
        self.error(f"此处应为数值、变量或括号，实际为 {tok[1] or '结尾'!r}", tok)


def parse(spec: dict, inputs: list[str], outputs: list[str]) -> Program:
    """Check one C block's body against the subset; ``spec`` as custom_c.parse returns."""
    states = list(spec.get("states", {}))
    parser = _Parser(spec["body"], inputs, outputs, states)
    body = parser.program()
    return Program(parser.inputs, parser.states, parser.nexts, parser.outputs, parser.symbols, body,
                   {k: float(v) for k, v in spec.get("states", {}).items()})


# ---- Python reference ----------------------------------------------------------------------
def _trunc(x):
    if isinstance(x, int):
        return x
    if not math.isfinite(x):
        raise CSubsetError(f"把 {x} 转成 int 没有定义")
    return math.trunc(x)


def _idiv(a, b):
    if b == 0:
        raise CSubsetError("int 除以 0")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _imod(a, b):
    return a - b * _idiv(a, b)


def _ddiv(a, b):
    if b == 0:
        raise CSubsetError("double 除以 0")
    return a / b


def _call(name, *args):
    try:
        value = FUNCTIONS[name][1](*map(float, args))
    except (ValueError, OverflowError) as exc:
        raise CSubsetError(f"{name} 的参数超出定义域或数值范围") from exc
    if not math.isfinite(value):
        raise CSubsetError(f"{name} 产生非有限值")
    return float(value)


class _PyWriter:
    def __init__(self, program: Program):
        self.p = program
        self.lines: list[str] = []
        self.loops: list[list[Stmt]] = []

    def var(self, sym):
        return f"v{sym.index}"

    def expr(self, e: Expr) -> str:
        if e.kind == "num":
            return repr(e.value)
        if e.kind == "var":
            return self.var(e.value)
        if e.kind == "unary":
            x = self.expr(e.args[0])
            return f"int(not {x})" if e.value == "!" else f"(-{x})"
        if e.kind == "cast":
            x = self.expr(e.args[0])
            return f"_trunc({x})" if e.type == "int" else f"float({x})"
        if e.kind == "call":
            return f"_call({e.value!r}, " + ", ".join(self.expr(a) for a in e.args) + ")"
        if e.kind == "cond":
            c, a, b = (self.expr(x) for x in e.args)
            return f"({a} if {c} else {b})"
        a, b = (self.expr(x) for x in e.args)
        op = e.value
        if op in _COMPARE:
            return f"int({a} {op} {b})"
        if op == "&&":
            return f"int(bool({a}) and bool({b}))"
        if op == "||":
            return f"int(bool({a}) or bool({b}))"
        if op == "/":
            return f"_idiv({a}, {b})" if e.type == "int" else f"_ddiv({a}, {b})"
        if op == "%":
            return f"_imod({a}, {b})"
        if e.type == "double":
            return f"(float({a}) {op} {b})"
        return f"({a} {op} {b})"

    def assign(self, sym, e, indent):
        value = self.expr(e)
        if sym.type == "int" and e.type != "int":
            value = f"_trunc({value})"
        elif sym.type == "double" and e.type == "int":
            value = f"float({value})"
        self.lines.append(" " * indent + f"{self.var(sym)} = {value}")

    def block(self, stmts, indent):
        start = len(self.lines)
        for s in stmts:
            self.stmt(s, indent)
        if len(self.lines) == start:
            self.lines.append(" " * indent + "pass")

    def stmt(self, s: Stmt, indent):
        pad = " " * indent
        if s.kind == "block":
            for x in s.body:
                self.stmt(x, indent)
        elif s.kind == "assign":
            self.assign(s.target, s.expr, indent)
        elif s.kind == "if":
            self.lines.append(pad + f"if {self.expr(s.expr)}:")
            self.block(s.body, indent + 1)
            if s.other:
                self.lines.append(pad + "else:")
                self.block(s.other, indent + 1)
        elif s.kind == "loop":
            self.lines.append(pad + f"while {self.expr(s.expr)}:")
            self.lines.append(pad + " _n += 1")
            self.lines.append(pad + " if _n > MAX_ITERATIONS: raise CSubsetError('C 代码一拍内的循环次数超过 %d' % MAX_ITERATIONS)")
            self.loops.append(s.step)
            self.block(s.body, indent + 1)
            self.loops.pop()
            for x in s.step:
                self.stmt(x, indent + 1)
        elif s.kind == "break":
            self.lines.append(pad + "break")
        elif s.kind == "continue":
            for x in self.loops[-1]:
                self.stmt(x, indent)
            self.lines.append(pad + "continue")

    def source(self):
        p = self.p
        head = ["def step(inputs, old):", " _n = 0"]
        head += [f" {self.var(s)} = float(inputs[{i}])" for i, s in enumerate(p.inputs)]
        head += [f" {self.var(s)} = float(old[{i}])" for i, s in enumerate(p.states)]
        head += [f" {self.var(n)} = {self.var(s)}" for n, s in zip(p.nexts, p.states)]
        head += [f" {self.var(s)} = 0.0" for s in p.outputs]
        self.lines = head
        self.block(p.body, 1)
        outs = ", ".join(self.var(s) for s in p.outputs)
        nexts = ", ".join(self.var(s) for s in p.nexts)
        self.lines.append(f" return [{outs}], [{nexts}]")
        return "\n".join(self.lines) + "\n"


def python_step(program: Program):
    """step(inputs, old_states) -> (outputs, next_states), all floats, in declaration order."""
    env = {"_trunc": _trunc, "_idiv": _idiv, "_imod": _imod, "_ddiv": _ddiv, "_call": _call,
           "CSubsetError": CSubsetError, "MAX_ITERATIONS": MAX_ITERATIONS, "__builtins__": {
               "int": int, "float": float, "bool": bool}}
    exec(compile(_PyWriter(program).source(), "<custom C subset>", "exec"), env)
    return env["step"]


class Runner:
    """Stateful wrapper: one call per sample, committing next state after the body."""

    def __init__(self, spec, inputs, outputs):
        self.program = parse(spec, inputs, outputs)
        self.step_fn = python_step(self.program)
        self.reset()

    def reset(self):
        self.state = [self.program.initial[s.name] for s in self.program.states]

    def __call__(self, values):
        outputs, nexts = self.step_fn(values, self.state)
        for value in [*outputs, *nexts]:
            if not math.isfinite(value):
                raise CSubsetError("C 代码产生了非有限值（inf 或 NaN）；请检查除数和函数定义域")
        self.state = [float(x) for x in nexts]
        return [float(x) for x in outputs]


# ---- ngspice control script --------------------------------------------------------------
def _number(value) -> str:
    return str(value) if isinstance(value, int) else f"{value:.17g}"


class SpiceWriter:
    """Control-language lines for one block; ``names`` maps Sym index -> vector name."""

    def __init__(self, program: Program, prefix: str):
        self.p = program
        self.prefix = prefix
        self.names = {s.index: f"{prefix}_{s.index}" for s in program.symbols}
        self.flags: list[str] = []
        self.flag_names: list[str] = []
        self.lines: list[str] = []

    def var(self, sym):
        return self.names[sym.index]

    def expr(self, e: Expr) -> str:
        """Atoms stay bare; every composite is parenthesized once."""
        if e.kind == "num":
            return _number(e.value)
        if e.kind == "var":
            return self.var(e.value)
        if e.kind == "unary":
            x = self.expr(e.args[0])
            return f"({x} eq 0)" if e.value == "!" else f"(-{x})"
        if e.kind == "cast":
            x = self.expr(e.args[0])
            return self.trunc(x) if e.type == "int" and e.args[0].type != "int" else x
        if e.kind == "call":
            return FUNCTIONS[e.value][2].format(*(self.expr(a) for a in e.args))
        if e.kind == "cond":
            c, a, b = (self.expr(x) for x in e.args)
            return f"({self.truth(e.args[0], c)} ? {a} : {b})"
        a, b = (self.expr(x) for x in e.args)
        op = e.value
        if op in _COMPARE:
            return f"({a} {_COMPARE[op]} {b})"
        # ngspice's 'and'/'or' evaluate both sides; its '?:' does not.
        if op == "&&":
            return f"({self.truth(e.args[0], a)} ? {self.truth(e.args[1], b)} : 0)"
        if op == "||":
            return f"({self.truth(e.args[0], a)} ? 1 : {self.truth(e.args[1], b)})"
        if op == "/" and e.type == "int":
            return self.trunc(f"({a} / {b})")
        if op == "%":
            return f"({a} - {b} * {self.trunc(f'({a} / {b})')})"
        return f"({a} {op} {b})"

    @staticmethod
    def truth(e: Expr, text: str) -> str:
        """0/1 of a C condition; comparisons and logic already are."""
        if e.kind == "binary" and (e.value in _COMPARE or e.value in ("&&", "||")) or \
                e.kind == "unary" and e.value == "!":
            return text
        return f"({text} ne 0)"

    @staticmethod
    def trunc(x: str) -> str:
        return f"(({x} ge 0) ? floor({x}) : ceil({x}))"

    def assign(self, sym, e, pad):
        value = self.expr(e)
        if sym.type == "int" and e.type != "int":
            value = self.trunc(value)
        self.lines.append(f"{pad}let {self.var(sym)} = {value}")

    @staticmethod
    def flat(stmts):
        out = []
        for s in stmts:
            out += SpiceWriter.flat(s.body) if s.kind == "block" else [s]
        return out

    @staticmethod
    def jumps(s: Stmt) -> bool:
        """Whether a break/continue in ``s`` targets the enclosing loop."""
        if s.kind in ("break", "continue"):
            return True
        if s.kind == "loop":
            return False
        return any(SpiceWriter.jumps(x) for x in [*s.body, *s.other])

    def block(self, stmts, pad):
        """ngspice's own break/continue misbehave inside nested while/if, so
        jumps set the loop's flag and later statements run only while it is 0."""
        stmts = self.flat(stmts)
        for i, s in enumerate(stmts):
            self.stmt(s, pad)
            if self.jumps(s) and i + 1 < len(stmts):
                self.lines.append(f"{pad}if {self.flags[-1]} eq 0")
                self.block(stmts[i + 1:], pad + "  ")
                self.lines.append(f"{pad}end")
                return

    def stmt(self, s: Stmt, pad):
        if s.kind == "block":
            self.block(s.body, pad)
        elif s.kind == "assign":
            self.assign(s.target, s.expr, pad)
        elif s.kind == "if":
            self.lines.append(f"{pad}if {self.truth(s.expr, self.expr(s.expr))}")
            self.block(s.body, pad + "  ")
            if s.other:
                self.lines.append(f"{pad}else")
                self.block(s.other, pad + "  ")
            self.lines.append(f"{pad}end")
        elif s.kind == "loop":
            cond = self.truth(s.expr, self.expr(s.expr))
            if not any(self.jumps(x) for x in s.body):
                self.lines.append(f"{pad}while {cond}")
                self.block([*s.body, *s.step], pad + "  ")
                self.lines.append(f"{pad}end")
                return
            flag = f"{self.prefix}_j{len(self.flag_names)}"
            self.flag_names.append(flag)
            self.flags.append(flag)
            # 0 runs on, 1 continues (step, then the condition), 2 breaks
            # without evaluating the condition again: '?:' is lazy.
            self.lines += [f"{pad}let {flag} = 0", f"{pad}while ({flag} lt 2) ? {cond} : 0",
                           f"{pad}  let {flag} = 0"]
            self.block(s.body, pad + "  ")
            if s.step:
                self.lines.append(f"{pad}  if {flag} lt 2")
                self.block(s.step, pad + "    ")
                self.lines.append(f"{pad}  end")
            self.lines.append(f"{pad}end")
            self.flags.pop()
        elif s.kind == "break":
            self.lines.append(f"{pad}let {self.flags[-1]} = 2")
        elif s.kind == "continue":
            self.lines.append(f"{pad}let {self.flags[-1]} = 1")

    def declarations(self) -> list[str]:
        """Created before the run so every vector exists in the const plot."""
        initial = {s.index: self.p.initial[s.name] for s in self.p.states}
        if not self.flag_names:
            self.step([s.name for s in self.p.inputs])
        return [f"let {self.var(s)} = {_number(initial.get(s.index, 0.0))}" for s in self.p.symbols
                if s.role != "input"] + [f"let {flag} = 0" for flag in self.flag_names]

    def step(self, inputs: list[str]) -> list[str]:
        """One sample: bind inputs, reset outputs and next state, run the body, commit."""
        p, self.lines, self.flag_names = self.p, [], []
        self.lines += [f"let {self.var(s)} = {x}" for s, x in zip(p.inputs, inputs)]
        self.lines += [f"let {self.var(n)} = {self.var(s)}" for n, s in zip(p.nexts, p.states)]
        self.lines += [f"let {self.var(s)} = 0" for s in p.outputs]
        for s in p.body:
            self.stmt(s, "")
        self.lines += [f"let {self.var(s)} = {self.var(n)}" for n, s in zip(p.nexts, p.states)]
        return self.lines

    def output(self, name: str) -> str:
        return self.var(next(s for s in self.p.outputs if s.name == name))


def field_reference() -> str:
    return ("ngspice and the Python reference run custom C bodies through a checked subset: double/int scalars "
            "declared with an initializer, finite decimal literals (no leading-zero integers), "
            "=, +=, -=, *=, /=, %=, ++, --, if/else, for, while, break, continue, "
            "C operators (?: && || ! comparisons + - * / %), casts (int)/(double), and "
            f"{', '.join(FUNCTIONS)}. No arrays, pointers, return, static or other functions. "
            "int division truncates as C; a zero divisor or math domain error stops the checked run.")
