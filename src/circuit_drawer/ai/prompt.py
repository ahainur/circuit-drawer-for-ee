"""What the model is told before it writes a coordinate table.

The model is the hand that places coordinates; the validator is the judge.
The prompt therefore states the table format, the hard rules the validator
enforces, the absolute symbol sizes, and shows calibrated tables taken from
the hand-written templates.  It never asks the model to call a layout engine.
"""

from __future__ import annotations

import json
import re

from .. import blocks, regions, schema
from ..geometry import BODY_FRAC, HALF_HEIGHT, MIN_LEAD, WINDING_GAP
from ..glyphs import KINDS as GLYPH_KINDS
from ..layout import PIN_NAMES
from ..profiles import get_profile
from ..source_waves import SCALE_RANGE as WAVE_SCALE, WAVES

_FORMAT = """\
# Coordinate table (JSON)

{
 "name": "<snake_case figure name>",
 "elements": [{"id": "...", "kind": "...", "pins": {"<pin>": [x, y], ...},
               "label": "$...$", "label_pos": "@LABEL_POS@", "opts": {...}}],
 "wires": [[[x, y], [x, y], ...], ...],      // orthogonal polylines, every corner listed
 "hops": [[x, y], ...],                      // crossings that do NOT connect
 "annotations": [{"at": [x, y], "text": "$...$", "arrow": "@ARROW@", "side": "@SIDE@",
                  "color": "magenta"}],     // color optional
 "netlist": {"<id>.<pin>": "<net name>", ...},  // REQUIRED: every pin of every element
 "regions": [{"from": [x0, y0], "to": [x1, y1], "fill": "@FILLS@",
              "label": "...", "members": ["<id>", ...]}],  // optional: one per module
 "callouts": [{"from": [x, y], "to": [x, y], "style": "@CALLOUT_STYLES@"}],  // optional annotation arrows
 "blocks": [{"id": "pwm", "from": [x0, y0], "to": [x1, y1], "text": "PWM"}],  // optional control boxes, not electrical
 "wave_symbols": [{"at": [x, y], "wave": "@WAVES@", "color": "blue", "scale": 1}]
}

For real repeated circuits, use top-level modules (version 1), rather than
pretending that background panels add devices. Default module_view is "expanded";
"folded" hides all but the first instance of each stack_usage instance group.
Example shape: "modules":{"version":1,"root_ports":{"P.p":"P"},
"definitions":{"cell":{"layout":{...},"ports":{"input":"IN.p"},
"defaults":{"R1":{"value":"10"}}}},"instances":[{"id":"A",
"definition":"cell","at":[0,0],"bindings":{"input":"P"},"overrides":{}}]}.
Definitions are checked once before assembly. Problems beginning with "模块定义"
refer to definitions/layout local coordinates, not instance at. Fix that shared
definition first; assembled diagnostics name the instance and definition.
Every definition has a complete ordinary coordinate table (including netlist),
and real devices. Public ports refer to actual port-kind elements, pin p.
definitions.layout allows name/elements/wires/hops/annotations/netlist/regions/
callouts/wave_symbols; no nested modules or modes. Instance IDs/local device IDs
start with a letter and use ASCII letters/digits/_/-. Definitions are reused;
at is an explicit [dx,dy] translation, never a placement hint. An instance
may have a label: "label":{"at":[3,4],"text":"Module A"}, in local
coordinates, to identify copies without changing shared part labels. All ports
must be bound; each global binding name is used by at least two terminals.
root_ports maps top-level terminal pin references to these names. The renderer
prints the binding names on terminals; electrical joins use only these explicit
ports, never arbitrary matching labels. Internal nodes/IDs get an instance prefix
(A/S1), so separate modules cannot accidentally share internal nodes.
defaults and instance overrides are simulation parameters per local device ID;
top-level simulation.components covers ONLY top-level devices. Other simulation
pin/device references may use A/S1.d or A/L1. Never infer a 120-degree phase shift:
the author specifies each drive override. Preserve physical defaults/overrides
during drawing edits. Geometry and bindings belong in modules; no flattened
generated children may be written to top-level elements. The editor shows a
projection; source edits go through module JSON or AI. Module v1 supports static
figures and SPICE-supported devices, not cross-module operating-mode overlays.
Use modules when explicitly asked for real reused instances/full simulation;
ordinary coloured regions alone need no modules. Do not invent parameter values.
PORT LINKS (preferred for module figures; write connections, not coordinates):
modules.links is a list of name pairs, like a netlist: ["Vin.b","M1.P"],
["M1.ON","M2.OP"]. Each end is an instance port (instance.PORT) or a top-level
part pin (Vin.b). The program draws every link as an orthogonal wire from the
current port positions, so never write wires, hops or coordinates for links.
Library modules need no definitions: "definition":"buck", "boost", "llc_halfbridge" or "dab"
(ports P, N on the left; OP, ON on the right). Omit "at" and the program
stacks the instances top to bottom. modules.parts lists two-pin parts the
program places beside the ports they link to, e.g. [{"id":"Vin","kind":"V",
"label":"$V_{in}$"},{"id":"RL","kind":"R","label":"$R_L$"}]; V pin b is +.
Each instance has "display":"expanded" (default, full circuit), "collapsed"
(a small 2x2 box, inputs on the left edge, outputs on the right) or "partial"
(llc_halfbridge only: switch bridge and rectifier become small boxes with a
MOSFET / diode mark, the resonant tank and transformer stay drawn). The
netlist is identical in all three. A collapsed box may carry "symbol":
"dcdc", "acdc", "dcac" (IEC 60617 converter: diagonal with the two sides
labelled), "mosfet", "igbt", "diode" or "text" (name only); without it the
library default is used. "collapsed_size":{"w":3,"h":4} enlarges the box and
re-spaces its ports evenly; leave it out for the default size. Every size must
pass routing validation. Every instance port must appear in a link or bindings, otherwise it is
reported as dangling; a misspelt port name is rejected with the valid names.
Do not mix bindings and links on a collapsed instance. Keep what the editor
wrote: modules.routes {"A|B":[[x,y],...]} holds bend points the user drew for a
link (drop a key only when its link is removed); an instance's "appearance"
{"fill","border","border_color","title"} styles its collapsed box; "group"
{"members":[same-definition instance IDs],"fill","label"} draws one instance
for several (the members become stacked panels behind it, still simulated); a
definition's "source" {"library","name","revision"} records the module library it
was copied from.
Two LLC modules, input parallel, output series, collapsed:
{"name":"llc_ipos","elements":[],"wires":[],"netlist":{},"modules":{"version":1,
"instances":[{"id":"M1","definition":"llc_halfbridge","display":"collapsed"},
{"id":"M2","definition":"llc_halfbridge","display":"collapsed"}],
"parts":[{"id":"Vin","kind":"V","label":"$V_{in}$"},{"id":"RL","kind":"R","label":"$R_L$"}],
"links":[["Vin.b","M1.P"],["Vin.a","M1.N"],["M1.P","M2.P"],["M1.N","M2.N"],
["M1.OP","RL.a"],["M1.ON","M2.OP"],["M2.ON","RL.b"]]}}
Input parallel = link P to P and N to N; output series = link ON of one
module to OP of the next; the load spans the first OP and the last ON.
For folding, top-level stack_usage maps a region ID to
{"purpose":"instances","instances":["A","B","C"]}; the first is visible,
the remaining instances must really exist, and the region's members name only
the representative's devices (A/S1...). stack.count is group size minus one.
Pure decorative layers use {"purpose":"decoration"}. Default is unconfirmed,
which permits drawing but blocks SPICE. All hidden devices are still validated.

regions are optional light background blocks, drawn behind everything.
Use "fit":"members" to derive a region from its non-empty members list:
{"id":"bridge","fit":"members","members":["S1","S2"],"fill":"green"}.
from/to can be omitted; if also present they are ignored with a warning.
The symbol and label bounds get 0.3 layout units of padding, rounded outward
onto a 0.05 grid at the current print scale. Derived corners are never saved.
This also works inside module definitions (local IDs) and with expanded IDs
such as A/S1 at the top level. Without fit, explicit from/to keep their meaning.
Non-member symbols must still not intersect the fitted boundary; no parts move.
glyphs are optional function marks drawn inside a box, e.g. for a block diagram:
{"kind":"dcdc","from":[x0,y0],"to":[x1,y1]} (kinds acdc, dcdc, dcac draw the
IEC converter diagonal with side labels; mosfet, igbt, diode draw a device).
Stack layers are a drawing convention and never create electrical copies.
Preserve any existing stack_usage declarations during drawing edits; never call
omitted phases "decoration" to make simulation pass. Missing phase/module data
must be explicitly supplied, not inferred from labels, colour, or stack.count.
Use one region per module only when the circuit is built from modules (several DAB/LLC cells,
MMC phases or arms): members lists the ids of the module's parts, each of which
must lie wholly inside the rectangle (labels too); no region edge may cut a
symbol; the label (below the block by default) obeys the text rules.  Name a
module by what it is ("LLC 模块 1", "双有源桥 2"), never by its colour, and number
modules top to bottom, then left to right.  Keep the shared buses (input rails,
series links between outputs, the load) outside the blocks, between or beside them.
Only when asked: "stack": {"count": 2, "dx": 0.3, "dy": 0.3, "fills": ["blue", "teal"]}
on a region draws offset panels behind it (identical phases or modules).  The panels
stand for the copies: draw the circuit of the front block once and do not also draw
the copies beside it.  A region with "fill": "none", "border": "dashed" is a dashed box
(around a switch, or a zoomed detail); "callouts": [{"from": [x, y], "to": [x, y],
"style": "block|line|wave"}] draws an arrow, e.g. from a dashed box to its zoomed detail;
draw every such arrow as a callout, never as an annotation or a LaTeX arrow symbol.
An arrow that ends inside a region it starts outside is taken to lead to a zoomed
detail, which may not be wired to the circuit.  Only when the user says the arrow
merely points at an area of the real circuit (a shaded input stage, not a redrawn
copy) add "detail": false to that callout.
For block arrows, border_color/border_width control the outline, fill_opacity
controls only the fill. Keep foreground and over_parts absent unless the user
explicitly asks to draw the arrow above the circuit or to cover symbols/text,
respectively. Never add over_parts just to silence a layout error. These options
are block-only; keep line and wave arrows clear of parts.
For a wavy pointer use "style":"wave", optionally "wave_amplitude":0.06 (printed cm)
and "wave_cycles":2 (integer 1..8). This is an annotation, NOT a simulated waveform.
For a long routed pointer use "style":"line", "via":[[x1,y1],[x2,y2]] with explicit
corner coordinates in travel order. Avoid components and their labels. Do not use
electrical wires for annotation arrows. For a label interrupting the arrow itself,
use "label_pos":"inline" and optional "label_at":0.5 (fraction of path length).
The renderer cuts a genuine gap in the shaft; do not add white rectangles or extra
text annotations. Keep both endpoints clear of the label; use exterior labels on
short arrows. Wave options require wave style; via requires line style without bend.
Control part of a figure (driver, PWM, controller, sensing, as papers draw it under
or beside the power stage): each box is a "blocks" entry with a unique id and its one
line of text centred inside ("Resonant Driver", "PWM", "$f_r$ calculation"); fill
@BLOCK_FILLS@ (default white, which hides a grey area behind it), border
@BLOCK_BORDERS@, color an ink (default black).  Blocks are not elements: no pins, no
netlist, never joined to the circuit's nets.  Make the box wide enough for its text.
Signal lines between blocks and to the circuit are line callouts with "color":"black"
that end on a box edge (never inside a box, never on a symbol); "dash": @DASHES@
("dashed" for a feedback or sampled signal), "head": @HEADS@, and "isolation": 0.5
draws the isolation mark (two short bars, the line cut between them) at that fraction
of its length; route around boxes with "via".  Name a signal with "label" ("$V_o$",
"$f_s$").  A grey controller area (DSP, MCU) behind the blocks is a region with
"fill":"gray" and its label.  Never draw control boxes with wires, annotations or glyphs.
Example: {"from": [9, -1], "to": [6.2, -3.5], "style": "line", "color": "black",
"dash": "dashed", "isolation": 0.3, "via": [[9, -3.5]], "label": "$V_o$"}.
Colours are the palette names above; write #rrggbb only when the user gives one.
Ink colours, only when the user asks for coloured marks (as review papers do):
"opts": {"color": "magenta"} on an element draws its symbol and label in that
colour (highlight one device); "color" on an annotation colours the text and its
current arrow (a coloured i or v next to the highlighted part).  Inks:
@INKS@, or #rrggbb; 品红 = magenta.
Wires stay black; colour only what the user names.
A curved arrow (a voltage between two points such as v_GS from gate to source, or
an arrow pointing at a part from its note) is a line callout with "bend": degrees
it leaves the straight line by, positive = bends to the left of travel, at most
120; its "color" is an ink and its label takes the same colour.  End it just
short of the part, never on the symbol.
Example: {"from": [0, -1], "to": [2, -1], "style": "line", "bend": 45,
"color": "blue", "label": "$v_\\mathrm{GS}$", "label_pos": "inline"}.
Source waveform marks: on V or I use opts.wave = @WAVES@ to add a small
upright waveform beside the source, retaining its polarity/current direction.
opts.wave_pos = @WAVE_POS@ (auto uses the side opposite the source label),
opts.wave_scale = @WAVE_SCALE@. Keep room
for the mark; it cannot overlap wires, parts or text. To draw a free-standing
waveform mark without an electrical source, use the top-level wave_symbols
list above; at is the centre. Add text separately in annotations. These marks
never connect nets or configure SPICE.  Only when the user asks for a mark drawn
on a wire (a PWM pulse on a signal line), add "on_wire": true to that mark: the
wire under it is hidden in the drawing and stays connected; never use it to fix a
line-through-body error the user did not ask for. Preserve simulation during drawing edits;
the user configures real initial/final values and timing in the SPICE dialog.
IGBT simulation.components.Q1 uses model="simplified_igbt", explicit ron, roff,
vce0 (nonnegative forward-voltage intercept), frequency, duty, delay, rise, fall.
Its forward c->e channel approximates Vce0 + Ron*I; reverse current uses a
separate generic e->c diode unless opts.diode=false. Finite Roff leakage remains.
This is not a vendor device, tail-current, switching-loss or ZVS model.
V/I support simulation.components.<id>.wave={"kind":"sine","offset":"0",
"amplitude":"24","frequency":"50","delay":"0","phase":"-120"}.
amplitude is positive PEAK V/A, not RMS; phase is degrees (-360..360), negative
means lag. vac requires this explicit sine wave. dc remains required as the DC
operating-point value; transient uses wave. Voltage is b-a, source current I is
a->b. A current probe on V/vac measures b->a; on igbt it measures total c->e
terminal current including its antiparallel diode. drive probes accept nmos/igbt/scr.
Current probes on D measure a->b, so reverse breakdown reads negative; the
same probe includes both diode branches in Simulink and PLECS.
A thyristor is {"model":"simplified_thyristor"} with ron, roff, vf (forward drop,
volts, >= 0) and the PWM gate fields: delay is the first firing instant (firing
angle alpha at frequency f: alpha/360/f), duty the gate pulse width / period. It
conducts a->b once fired and turns off when its current would reverse; a current
probe on it measures a->b. Optional holding_current and latching_current (A,
default 0: off only at zero current, latched at once; latching_current >= holding_current):
it turns off below the holding current, and a gate pulse that ends below the latching
current leaves it off. Optional turn_off_time (s, default 0): after it turns off it
must be reverse biased for that long; forward voltage sooner fires it again without a
gate pulse (commutation failure). Only fill values the user gives; no closed-loop drive.
Sine is transient excitation, not small-signal AC analysis. No simulation values
are inferred from the source symbol, colour, labels or omitted phases.
Transformer simulation is explicit and separate from opts.ratio (display text).
In simulation.components.T the supported model is
{"model":"ideal_transformer","ratio":"1","polarity":"same","reference_resistance":"1T"}.
ratio > 0 is Ns(total)/Np; a centre tap divides it into two equal halves.
polarity is same (p1/s1) or opposite (p1/s2), and must match visible opts.dots.
reference_resistance >= 1G joins sct (or s2) to p2 only as a numerical reference;
it is not a physical isolation model. Optional magnetizing_inductance (across the
primary) and leakage_inductance (in series with p1), both in henries referred to
the primary, default "0" = not modelled; fill them only with values the user gives.
A separately drawn L element still works. A coupled inductor with L1, L2 and
coupling k is a transformer with opts.core=false and ratio=sqrt(L2/L1)/k,
magnetizing_inductance=k^2*L1, leakage_inductance=(1-k^2)*L1 (exact equivalent).
Transformer current probes require winding: primary, or secondary for two windings,
or upper/lower for a centre tap (directions p1->p2, s1->s2, s1->sct, sct->s2).
simulation.transient.node_capacitance is optional, defaults to 0 (disabled), and
must be nonnegative. E.g. "node_capacitance":"1n" adds that numerical capacitance
from EVERY electrical net to simulation ground; it changes fast transients and is
not device parasitics or evidence for ZVS. Never silently enable this to fix a run.
Preserve all these simulation parameters during drawing edits; do not guess values
from labels or topology names. The user sets them in the simulation dialog.
Optional simulation.components.L.series_resistance is a nonnegative resistance
in ohms, default 0; it models winding resistance in series with the existing L.
V and vac accept series_resistance (ohms) and series_inductance (henries), both
default 0: the source's internal impedance between its EMF and the + terminal b
(a battery's internal resistance, a grid's short-circuit inductance). Only fill
them when the user gives them. For a balanced three-phase supply given as a
line-to-line RMS voltage V_LL, each vac's peak is V_LL*sqrt(2)/sqrt(3) and the
phases are 0, -120, 120 degrees (positive sequence).
PWM nmos/igbt accept snubber_resistance (ohms) and snubber_capacitance (farads),
both positive to enable a series RC across d-s/c-e, or both 0 (default) to disable.
These are explicit damping components, not automatically fitted device parasitics.
PWM nmos/igbt also accept output_capacitance (farads, linear Coss/Coes across the
switch, default 0 = not modelled) and the body / antiparallel diode's diode_is,
diode_n (both > 0, defaults 1n and 1) and diode_rs (ohms, default 0.01). Fill them
only from user-given or datasheet values, e.g. "output_capacitance":"150p".
PWM phase_shift is an additional phase in degrees [-180,180], positive lagging,
default 0; phase_ramp is seconds, default 0 (fixed phase). A positive phase_ramp
linearly increases phase from zero at simulation time 0 to phase_shift at
phase_ramp, after which phase is fixed. Example on an existing switch:
"phase_shift":"30","phase_ramp":"1m","snubber_resistance":"47","snubber_capacitance":"2.2n".
Use only when the user asks for physical damping or phase startup; preserve them
in drawing edits, and never infer their values from a topology name.
The top-level waveform section (CSV binding, time intervals and its notes /
label_offsets: hand-placed text, arrows and key-point circles on the measured
waveform chart) is edited only in the editor's waveform panel. Never write or
change it; copy it unchanged. A request for a note on the waveform chart is
not a callout or annotation in the circuit: tell the user to use the panel's
"标注" buttons (文字 / 箭头 / 关键点).
Waveform notes may have color (INKS name or #rrggbb), font_pt (5..20),
line_pt (0.1..2, arrow/dot only), radius_cm (0.02..0.3, dot only), and offset
([dx,dy] in paper cm, each -3..3, x right / y up). Defaults are profile font,
0.8 * profile line for arrows, profile line for circles, and radius 0.06 cm.
For example a preserved note may be {"kind":"dot","channel":"i_L_A",
"at":[0.001,3.4],"color":"blue","font_pt":8,"line_pt":0.5,"radius_cm":0.08,
"offset":[0.1,0.1],"text":"peak"}. These are manual display settings, never
simulation parameters. Unknown note fields are rejected. Preserve them as-is;
the user adjusts them through 标注属性, not through AI circuit edits.
The top-level closed_loop section links a control-diagram table (control:
its file path or the table), controller inputs (a simulation probe name or a
fixed value) and outputs (duty commands to PWM switches) for ngspice closed-loop
runs. The user sets it in the SPICE dialog; never write or change it, copy it
unchanged, and keep the probes and switches it names.
A zoomed detail is a separate small drawing beside the circuit, never wired to it:
its parts get their own ids and node names, its terminals end in ports, and the
arrow is the only link (a wire crossing into it is an error).  Panels, arrows and
labels are checked like the rest: no edge through a symbol, no arrow over one.

Units are abstract layout units (about 1 cm); y grows upwards.  Use a 0.05 grid,
preferably 0.1.  Labels are LaTeX math, e.g. "$L_r$", "$V_\\\\mathrm{in}$".

Element kinds and pins:
- R, L, C: pins a, b.  The two pins share x (vertical part) or y (horizontal part).
- D: a = anode, b = cathode. opts.zener=true draws CircuitikZ zD, only when a
  Zener symbol is requested. Simulation stays generic_diode with explicit
  breakdown_voltage, breakdown_current and zener_resistance (see generated
  reference); do not guess ratings from the symbol or its label. Reverse
  breakdown flows b->a and its a->b current probe reads negative.
- scr: thyristor (SCR), a = anode, b = cathode; horizontal or vertical only, never
  45 degrees. Its gate lead is drawn on the left of a->b (opts.mirror: the right);
  the gate is a simulation drive, not a pin. Pin spacing at least as for a switch.
- V: DC voltage source, a = minus, b = plus. opts.battery=true draws it as a
  battery (long plate at b); same pins and simulation fields.
- vac: sinusoidal (AC) voltage source, e.g. each phase of a three-phase supply;
  a = reference end (the star point side), b = the line side. A three-phase
  supply is three vac in star: their a pins on one neutral net.
- I: current source, current flows a -> b.
- port: a terminal (open circle) with one pin p where a wire ends, e.g. the
  input lines a/b/c or the P/O/N outputs; its label goes left unless label_pos says otherwise.
- nmos: n-MOSFET with body diode; d = drain, s = source. Its pins may share
  x OR y, in either order: rotating 90/180/270 degrees is supported. Keep
  the electrical d/s assignment when rotating. opts.mirror mirrors the gate
  side; opts.diode=false removes the body diode. Typical pin spacing is 1.0.
- igbt: same geometry; c = collector, e = emitter, also rotatable.
- transformer: p1, p2 = primary winding; s1, s2 = secondary winding. Both
  windings must be parallel and horizontal or vertical; rotation is supported.
  In the upright orientation p1/s1 are above p2/s2, secondary on the right;
  rotate ALL pins together, preserving this local frame. Optional sct is
  the centre tap between s1 and s2. opts.ratio e.g. "$1:n$";
  opts.dots = "same" (both dots at top), "opposite" (flyback) or false.
  opts.core = false draws a coupled inductor: the same windings and pins
  without core lines (for a coupled inductor, not a transformer).
  Windings are drawn on their pins, so wires leave the pins straight.
Switch labels default to the diode side and follow rotation/mirroring (right
of an upright unmirrored device). Explicit label_pos selects a page direction
above/below/left/right; along the pin axis the label uses the corner on the
diode side to avoid its own lead. For label collisions first change label_pos,
leaving pins and wires unchanged; do not move a device just to move its text.
All parts support opts.scale_length and opts.scale_width, each @SIZE@;
these scale the symbol along/across its pin axis, not the pin spacing or font.
Default label_pos is
above for horizontal parts and right for vertical parts.  Two-terminal parts
accept opts.current = "$i_L$" for a current arrow on the lead.
"""
# Value lists come from the declarations the validator uses, never retyped.
for _token, _values in {"LABEL_POS": schema.LABEL_POS.accepted(),
                        "ARROW": schema.ANNOTATION["arrow"].accepted(),
                        "SIDE": schema.ANNOTATION["side"].accepted(),
                        "FILLS": "|".join(regions.FILLS),
                        "CALLOUT_STYLES": "|".join(regions.CALLOUT_STYLES),
                        "BLOCK_FILLS": "|".join(blocks.FILLS),
                        "BLOCK_BORDERS": "|".join(blocks.BORDERS),
                        "DASHES": "|".join(regions.CALLOUT_DASHES),
                        "HEADS": "|".join(regions.CALLOUT_HEADS),
                        "WAVES": schema.OPTS["wave"].accepted(),
                        "WAVE_POS": schema.OPTS["wave_pos"].accepted(),
                        "WAVE_SCALE": schema.OPTS["wave_scale"].accepted(),
                        "INKS": "|".join(regions.INKS),
                        "SIZE": schema.OPTS["scale_length"].accepted()}.items():
    _FORMAT = _FORMAT.replace(f"@{_token}@", _values)

_RULES = """\
# Hard rules (enforced by the validator; any error rejects the whole table)

Callout appearance fields in the generated reference are block-only. Unknown
callout keys are rejected; respect the declared width/opacity bounds and booleans.

For SPICE export, declare every transformer pin including sct in the netlist.
Simulation rejects unsupported models, nonpositive ratios, polarity/dot conflicts,
reference_resistance below 1G, missing/wrong winding probes and negative node_capacitance.
IGBT requires simplified_igbt, 0 < ron < roff and vce0 >= 0; PWM uses the same
timing checks as nmos. Sine requires positive peak amplitude/frequency, delay >= 0,
phase in [-360,360], at least 100 output samples per period, and stop covering
delay plus one period. vac without explicit sine parameters is rejected.
These simulation checks run at export, separately from the drawing geometry check.
D with opts.zener=true requires breakdown_voltage > 0 for simulation; without
the drawing option, a positive breakdown_voltage is also allowed. breakdown_current
and zener_resistance must be positive, breakdown_voltage nonnegative. Preserve
these values and opts.zener during drawing-only edits. Simulink / PLECS use
the reverse tangent at breakdown_current; its intercept Vz-Zzt*Izt must be >= 0.
series_resistance and phase_ramp must be nonnegative; snubber R/C must both be
positive or both zero. Nonzero phase ramps must exceed the absolute phase time
(abs(phase_shift)/360/frequency) and leave at least one settled period before stop.
Fixed phase must leave a nonnegative effective delay and one full period.

Modules must have complete local netlists and actual devices. Bind every declared
terminal exactly once, use both ends of global networks, and namespace internal
devices. Bad definitions/bindings/view values produce bad-module; hidden instances
must remain in the full electrical graph. Collapsed boxes must not overlap each
other or visible external symbols, leads, labels or pins (module-box-overlap).
Only interior overlap counts; boundary contact and a box's own ports are allowed.
Decorative regions, NoConn crosses and electrical captions are not device bodies.
Resize or move instances, preserving links, public port names and all electrical
parameters. Hidden instances
must pass the same geometry/connection checks as visible ones. Do not use nested
modules or cross-module operating modes in version 1.

1. No automatic layout: you choose every coordinate. Use a topology-specific
   arrangement with a clear power path. Horizontal DC rails and vertical bridge
   legs are conventions for bridges, NOT restrictions on every circuit.
2. Every wire segment, two-terminal part, switch and winding is horizontal or vertical.
   Sole exception, only where the standard drawing has it (the Z-source capacitor X):
   a two-terminal part at exactly 45 degrees (|dx| = |dy|), extended by wires on its
   own line; where two such lines cross, put the crossing point in hops.
3. Wire endpoints must land on a pin or on another wire (a T-junction connects
   and gets a dot automatically).  No dangling wire ends, no unconnected pins.
   A wire end in the middle of a part's lead is an error.
4. A wire may not run through a symbol body, overlap another wire on the same
   line, or run parallel closer than 0.18 cm.
5. A wire that crosses another one without connecting must list the crossing
   point in "hops", and the point must lie on a horizontal wire.
   Undeclared crossings are rejected.  Prefer routes with no crossing at all.
6. Bodies keep 0.06 cm apart.  Labels, ratios and annotation text may not sit
   on a symbol, on a line, or on other text.
7. The netlist is compared pairwise with the connectivity derived from the
   geometry; anything connected that should not be (or vice versa) is rejected.
   Use the same net name for every pin that is electrically the same node.
8. Current arrows ("arrow" annotations) must sit on a wire.
9. The column width is an upper bound. Small circuits keep their natural width
   near 1 cm/unit; spacing may expand to clear fixed-size symbols and labels.
   A wide figure is compressed to fit; below 0.55 cm/unit it is too cramped.
   Symbols and text keep their absolute size.
"""


def _sizes(profile: str) -> str:
    prof = get_profile(profile)
    rows = []
    for k in ("R", "L", "C", "D", "V", "vac", "I"):
        body = prof.bipole_len * BODY_FRAC[k]
        rows.append(f"- {k}: body {body:.2f} cm long x {2 * HALF_HEIGHT[k]:.2f} cm wide; "
                    f"pin spacing >= {body + 2 * MIN_LEAD:.2f} cm")
    k = prof.transistor_scale / 0.9
    rows.append(f"- nmos/igbt: body about {0.8 * k:.2f} cm wide x {0.74 * k:.2f} cm tall, "
                f"its label needs about 0.6 cm to the right")
    rows.append(f"- transformer: primary and secondary axes {WINDING_GAP[0]}-"
                f"{WINDING_GAP[1]} cm apart; each winding body is {0.75 * BODY_FRAC['L']:.2f} cm tall plus leads")
    return (f"# Symbol sizes for profile {prof.name} (column {prof.width_cm:.2f} cm, "
            f"Times {prof.font_pt:g} pt)\n\n" + "\n".join(rows) + "\n\nSizes are in cm; divide by the cm/unit scale to get layout units.  An 8 pt "
            "character is about 0.19 cm wide (subscripts 0.13 cm).  The hand templates "
            "below run at 0.87-1.4 cm/unit in a single column; keep new drawings "
            "about 5-9 units wide and 3-4 units tall unless the circuit is large.\n")


def _alts(values) -> str:
    return "|".join("false" if v is False else str(v) for v in values)


def _field(f, kinds: bool = False) -> str:
    scope = f" [{', '.join(sorted(f.kinds))}]" if kinds and f.kinds != schema.ALL_KINDS else ""
    default = "" if f.default is None else f", default {_alts([f.default]).lower()}"
    return f"- {f.name}{scope}: {f.accepted()}{default}. {f.doc}"


def simulation_parameter_prompt(topology, inputs) -> str:
    """Parameter-only proposals use the same field declarations as the exporter."""
    import json
    from ..spice import FIELDS, OPTIONAL_FIELDS, SOURCE_WAVE_FIELDS, TRANSIENT_FIELDS
    intro = ("你是仿真参数建议助手。只返回一个完整 JSON 对象，不能输出坐标、接线、元件类型、"
             "模型映射或控制器，也不能输出 SPICE 语句、表达式、文件路径或可执行代码。"
             "缺少需求时给出明确标为假设的常见演示工况，不声称是器件额定值或最优设计。"
             "有需求时遵守它；固定要求优先于文字中的冲突要求。数值用 SI 或 SPICE 后缀（兆为 Meg）。"
             "参数会通过原导出校验器并做短 ngspice 检查；错误会回传，最多三次完整回答。\n")
    fields = json.dumps(inputs, ensure_ascii=False)
    if topology:
        return intro + (f"已按真实接线识别 {topology}。你只需从文字里提取需求，"
                        "返回 {\"requirements\":{...}}；未提及的字段省略，不能自己计算元件值。"
                        "随后由程序按 CCM 公式计算。字段（*_pct 为百分数）：" + fields)
    return intro + ("返回 {\"simulation\":{\"version\":1,\"ground\":\"真实元件.引脚\","
                    "\"components\":{...},\"transient\":{...},\"probes\":[...]},"
                    "\"requirements\":{...},\"reasons\":{\"components.L1.value\":\"计算或取值理由\",...},"
                    "\"assumptions\":[\"明确的工况假设和模型限制\"]}。"
                    "requirements 允许的字段：" + fields + "。所有固定要求必须保留。"
                    "reasons 对每个叶参数都给理由：ground、probes、components.<ID>.<字段>"
                    "（wave 用 components.<ID>.wave.<字段>）和 transient.<字段>。"
                    "components 必须与当前器件 ID 一一对应，不能增加、遗漏或改名。"
                    "参考地和电压探针只用现有引脚；不能用标签猜电气数值。"
                    "probes 可为 {name,kind:'voltage',positive,negative}、"
                    "{name,kind:'current',element}、{name,kind:'drive',element}；"
                    "变压器 current 需 winding（primary/secondary；中心抽头为 upper/lower）。"
                    "给出一个名为 v_out 的输出电压测量，以便显示目标偏差。"
                    "D 的 model 为 generic_diode，nmos 为 ideal_switch，IGBT 为 simplified_igbt，"
                    "变压器为 ideal_transformer，需 polarity=same/opposite。"
                    "V/vac 的电压是 b−a，I 电流 a→b；vac 需要 sine 激励，幅值为峰值。"
                    "保留已指定的模型映射，不要从映射模型降级。开关相移、互补、变压器极性应逐项核对连接。"
                    "0<max_step<=step<stop，stop/max_step<=1000000；初始 L/C 值明确填写。"
                    "短窗口不保证稳态，不得承诺损耗、ZVS 或额定值合格。\n"
                    "元件必填数值字段（第三列只为格式示例，不能照抄为所有拓扑的参数）：\n" +
                    json.dumps(FIELDS, ensure_ascii=False) + "\n可选字段：\n" +
                    json.dumps(OPTIONAL_FIELDS, ensure_ascii=False) + "\n激励 wave.kind 与数值字段：\n" +
                    json.dumps(SOURCE_WAVE_FIELDS, ensure_ascii=False) + "\n瞬态字段：\n" +
                    json.dumps(TRANSIENT_FIELDS, ensure_ascii=False))


def field_reference() -> str:
    """Every table field and accepted value, generated from the declarations
    the validator uses (schema.py and the modules owning each value list)."""
    from ..model_mapping import SCHEMA as MODEL_MAPPINGS
    from ..spice import FIELDS, OPTIONAL_FIELDS, SOURCE_WAVE_FIELDS, TRANSIENT_FIELDS
    names = lambda fields: ", ".join(name for name, _, _ in fields)
    lines = ["# Field reference (generated from the validator's declarations; "
             "other keys or values are rejected)", "",
             "Element kinds and pins: " + "; ".join(f"{k} {', '.join(p)}" for k, p in PIN_NAMES.items())
             + " (transformer may add sct).", _field(schema.LABEL_POS),
             "Element opts, for every kind unless [kinds] are listed (else bad-option):"]
    lines += [_field(f, kinds=True) for f in schema.ELEMENT_OPTS]
    lines += ["annotations (arrow, side, size, color; else bad-annotation):"]
    lines += [_field(f) for f in schema.ANNOTATION_FIELDS]
    lines += [f"Inks: {_alts(regions.INKS)}, or #rrggbb.",
              f"regions: fill {_alts(regions.FILLS)} (or #rrggbb); border {_alts(regions.BORDERS)}; "
              f"label_pos {_alts(regions.REGION_LABEL_POS)}; fit members; stack.count 1..{regions.STACK_MAX}.",
              f"callouts: style {_alts(regions.CALLOUT_STYLES)}; label_pos {_alts(regions.CALLOUT_LABEL_POS)}; "
              f"label_at 0..1 exclusive; color ink; bend -{regions.MAX_BEND}..{regions.MAX_BEND} degrees "
              f"(line only); wave_amplitude > 0 cm and wave_cycles 1..{regions.MAX_WAVE_CYCLES} (wave only); "
              f"dash {_alts(regions.CALLOUT_DASHES)} and head {_alts(regions.CALLOUT_HEADS)} (line only); "
              f"isolation 0..1 exclusive (straight line, via allowed, no bend); "
              f"detail false (only points, the region is not a zoomed detail). "
              + "Block-only appearance: " + "; ".join(f"{k}: {v}" for k, v in regions.CALLOUT_APPEARANCE_FIELDS.items()),
              f"blocks: {', '.join(blocks.KEYS)}; id unique; text one line; fill {_alts(blocks.FILLS)} "
              f"(or #rrggbb); border {_alts(blocks.BORDERS)}; color ink, default black; "
              f"each side at least {blocks.MIN_SIDE:g} units.",
              f"wave_symbols: wave {_alts(WAVES)}; scale {WAVE_SCALE[0]:g}..{WAVE_SCALE[1]:g}; color ink, default blue; "
              f"on_wire true|false (drawn on a wire on purpose).",
              f"glyphs: kind {_alts(GLYPH_KINDS)}.",
              "model_mapping: " + "; ".join(
                  f"{kind}: " + " | ".join(model + (f" ({', '.join(params)})" if params else "")
                                            for model, params in models.items())
                  for kind, models in MODEL_MAPPINGS.items()) + ".",
              "simulation.components.<id> fields by element kind (SPICE numbers such as \"500u\"; "
              "set by the user, never inferred):"]
    for kind, fields in FIELDS.items():
        optional = OPTIONAL_FIELDS.get(kind)
        lines.append(f"- {kind}: {names(fields)}" + (f"; optional {names(optional)}" if optional else ""))
    lines += ["simulation.components.<V|vac|I id>.wave kind: " + " | ".join(
                  f"{kind} ({names(fields)})" for kind, fields in SOURCE_WAVE_FIELDS.items()) + ".",
              f"simulation.transient: {names(TRANSIENT_FIELDS)}."]
    return "\n".join(lines) + "\n"


def _example(name: str, text: str = "") -> str:
    from ..pipeline import from_template
    return json.dumps(from_template(name, text).to_dict(), ensure_ascii=False)


_FORMAT += """
Project module authoring: modules.definitions is the project's reusable library,
including user names such as Boost or LLC_HB. Reuse a supplied definition by
name in instances and use its exact public port names in modules.links. Never
redraw that definition, invent ports or add coordinates when asked to connect
user modules. Instances may omit at, just like built-in modules.
Definition port_layout optionally maps every public port to {side, order}:
side is left or right; order is a unique nonnegative integer, top to bottom.
If omitted, port position determines side and vertical order. Example:
"port_layout":{"P":{"side":"left","order":0},"OP":{"side":"right","order":1}}.
Definition editable is an optional list of {element, field}, e.g.
"editable":[{"element":"L1","field":"value"}]. When present, only these
simulation fields may appear in instance overrides. defaults still holds all
shared physical values, including fields that are not independently editable.
An explicitly unused public port must be listed in instance unconnected, e.g.
"unconnected":["OP"]. Default is []; do not use it to suppress a missing link.
It cannot name a port already in links or bindings. Never invent a connection.
module_authoring is editor-owned provenance: aliases (expanded device ID to
original ID), order (original device ordering), terminals (synthetic port IDs),
and optional modes (operating modes parked during module editing). Preserve it
unchanged; it supplies no electrical connectivity. Do not generate it yourself.
"""

_RULES += """
A subscript longer than one character needs braces: $V_{in}$, $i_{Lr}$, never
$V_in$ (LaTeX lowers only the first character); subscript-braces warns about it.
"""

_RULES += """
Custom module port names must match definitions.ports exactly. port_layout must
cover every port, use left/right and unique nonnegative order integers. editable
must name a supported simulation field of a real internal device; overrides
may only change those fields. unconnected must contain unique existing ports
with no link or binding. Do not modify module_authoring to repair a circuit.
If a collapsed size cannot be routed, adjust collapsed_size or instance spacing;
preserve links, public port names and electrical parameters. A minimum size
does not guarantee that routing is possible in every arrangement.
"""


_FORMAT += """
Electrical editing (opt-in, top-level sections; omit for legacy numbering):
- electrical is program-owned metadata, not model-authored drawing data. Do NOT return
  an electrical section, node membership lists, IDs, or counters. The program recomputes
  connectivity after your edit and retains existing network names and branch numbers.
  Use the read-only node anchors and branch targets provided with the edit request.
  Network names are changed in the editor; they do not create a physical connection.
- no_connect: ["R1.b"] marks a deliberately open pin. Default []; not a wire end, not a substitute
  for an intended connection. Never mark a connected pin. The editor and exports draw a cross.
- model_mapping: global per-kind simulation choices, symbols unchanged; absent means legacy behavior.
  switch is {"model":"ideal_switch"}, {"model":"mosfet","vto":"3","kp":"10","gate_voltage":"10"},
  or {"model":"igbt","vce0":"1.5"}. These are generic models, not vendor loss models.
  R/L/C accept {"model":"ideal"}; parasitic variants require all positive explicit SI parameters:
  R: {"model":"parasitic","series_inductance":"10n","parallel_capacitance":"1p"};
  L: {"model":"parasitic","series_resistance":"0.1","parallel_capacitance":"1p"};
  C: {"model":"parasitic","series_resistance":"0.01","series_inductance":"1n"}.
  MOSFET gate_voltage > vto. Never infer parasitics from the symbol or add unknown keys.
  Mapped switches retain the device PWM phase_shift/phase_ramp and RC snubber.
  L parasitic mapping and a nonzero simulation.components[id].series_resistance
  cannot both be enabled: choose one explicit resistance configuration.
For an instruction such as '节点 A、B 之间加 10 Ω 电阻，放在 C1 左边', resolve A/B through
 the supplied node anchors, wire to actual pins, put the new resistor left of C1, and update netlist.
For '支路 3 的电感改成 22 μH', resolve the supplied branch target to the element ID and update
 simulation.components[id].value to "22u" as well as the visible label; leave its geometry fixed.
"""
_RULES += """
Electrical references must name existing pins and branches; unknown names are errors.
Do not write or repair the electrical ledger; NoConn is only for open pins.
Electrical captions must clear symbols, wires and other text. If electrical-label-space
is reported, make room in the nearby drawing; do not invent saved label coordinates.
Model mappings reject unknown keys, nonpositive parameters and gate_voltage <= vto.
An L parasitic mapping rejects nonzero per-device series_resistance; never silently
add or override the two resistances. Preserve existing damping unless asked to change it.
"""


def system_prompt(profile: str) -> str:
    return "\n".join([
        "You write coordinate tables for paper-ready power-electronics schematics "
        "(IEEE style, CircuitikZ backend).  A validator checks every table and "
        "either accepts it or returns a list of errors that name element ids and "
        "coordinates; you then rewrite the table.",
        "",
        _FORMAT, field_reference(), _RULES, _sizes(profile),
        "# Calibrated examples (hand-written templates that pass the validator)",
        "",
        "Flyback, output diode on the top line, current arrows:",
        _example("flyback", "currents"),
        "",
        "Dual active bridge (two full bridges; the line leaving leg A hops leg B):",
        _example("dab"),
        "",
        "Z-source three-phase inverter.  Note how the X-shaped impedance network is "
        "drawn with only horizontal and vertical lines (L1 on top, L2 at the bottom, "
        "C1 and C2 hanging under the two top nodes, their returns crossing once with a "
        "hop) and how phase lines leaving the legs hop the later legs:",
        _example("zsource_inverter"),
        "",
        "# Standard drawings",
        "",
        "Readers recognise a topology by its textbook drawing, so keep it: bridge legs "
        "as vertical columns between the DC rails, impedance networks symmetric (X "
        "shape as above), multilevel legs (NPC, flying capacitor) as one vertical "
        "column per leg with clamp parts inside the column, transformers in the middle "
        "of the power path, loads on the right.  If a reference figure is attached, "
        "follow its arrangement.",
        "",
        "# Reply format",
        "",
        "Reply with one ```json fenced block holding the complete table and nothing "
        "else after it.  A short sentence before the block is fine.  When you get a "
        "validator report, return the whole corrected table, not a patch, and change "
        "the circuit's connections only if an error is about them.",
    ])


PLAN_SYSTEM = """\
List a paper circuit for confirmation: no drawing, coordinates or JSON.

Part kinds and their pins:
- R, L, C: a, b.  D: a = anode, b = cathode.  V: DC source, a = minus, b = plus.
- vac: sinusoidal voltage source, a = reference, voltage b-a. I: current a -> b.
- nmos: d, s (body diode included unless stated).  igbt: c, e (anti-parallel diode
  included unless stated); igbt forward channel c->e, antiparallel diode e->c.
- Source peak/frequency/phase and IGBT physical model values are separate SPICE
  settings, not inferred from labels or source symbols. Preserve user values.
- Zener: D with opts.zener; reverse current b->a. Preserve explicit ratings;
  never infer them from the symbol.
- transformer: p1, p2 primary; s1, s2 secondary; optional sct centre tap.

Rules:
- Pick one concrete variant when the description allows several and say so under
  assumptions; keep to what a textbook figure of that topology shows (no drivers,
  sensors or snubbers unless asked).
- Name parts the usual way (Vin, S1.., D1.., L1, Lr, Cr, Co, RL) and nodes with short
  names (P, N, A, B, Vo...).  Pins that are electrically the same node share a name.
- One line per part with every pin's node; never repeat the connections elsewhere.
- Include sct for a centre-tapped transformer. Its turns text is not a simulation
  value; do not invent magnetising/leakage values or numerical solver parameters.
- When the circuit is built from repeated modules (multi-module converters, MMC
  phases or arms), group the list by module: a line 【模块 k：<name>】 before its parts.
  When stacked panels stand for identical copies (e.g. "后面叠两层表示 b、c 相"),
  for a drawing-only request identify the visible copy and the omitted copies.
  For real instances/full simulation list the shared definition's devices and
  every instance's port bindings and explicit parameter differences. Distinguish
  electrical instances from purely decorative panels; do not infer phase shifts.
  User-saved modules in the current project are equally reusable: list their
  definition names, instances and exact public port connections, without coordinates.
- A zoomed detail (放大图) goes last under 【放大图：<name>】 with its own node names;
  it is not connected to the main circuit.  An MMC half-bridge submodule: T1 and T2
  in series across C; terminal A at the T1/T2 midpoint, terminal B at T2's emitter
  (C's minus end).  A full-bridge submodule: two such legs across C, A and B at the
  two midpoints.
- No explanation of how the circuit works.

Reply in Chinese, plain text, exactly these three sections and nothing else:

拓扑名称：<one line>
器件与连接清单：
1. <id>（<kind>）：<pin>→<node>，<pin>→<node>
...
必要假设：
- <one line each; write "无" if none>
"""


PLAN_SYSTEM += "\nUse exact requested node/branch IDs and physical values; never guess IDs or rewrite the program-owned electrical ledger. Mention intentional NoConn and requested global model mappings.\n"
PLAN_SYSTEM += "Arrows: foreground/over_parts only when explicitly requested; keep electrical data unchanged.\n"


def plan_message(description: str) -> str:
    return ("Circuit to list:\n\n" + description.strip() + "\n\n"
            "If a reference figure is attached, follow it.  "
            "Reply with the three sections only.")


# Specific examples precede the generic modular fallback. Ship the file-based
# examples with the package too, so an installed wheel has the same prompts.
LIBRARY_MODULE = re.compile(r"llc|dab|双有源桥|谐振", re.I)
FOLDED = re.compile(r"折叠|方框|框图|collaps|partial", re.I)
MODULAR = re.compile(r"模块|module|ipos|isop|isos|iios|输入并联|输入串联|输出串联|输出并联|mmc|折叠|collaps", re.I)
MAX_EXAMPLES = 2  # Keep the request prompt bounded even when several intents match.
EXAMPLE_INDEX = (
    (re.compile(r"真实实例|完整仿真|real instances|full simulation", re.I), "three_boost"),
    (re.compile(r"叠放|三相|放大图|mmc|子模块|stacked|three.phase|zoom", re.I), "mmc_arm_stack"),
    (re.compile(r"llc|谐振", re.I), "llc"),
    (re.compile(r"dab|双有源桥", re.I), "dab_multi"),
)


def example_names(description: str) -> list[str]:
    names = []
    for pattern, name in EXAMPLE_INDEX:
        if pattern.search(description):
            if name == "llc":
                name = "llc_ipos" if MODULAR.search(description) else "llc_halfbridge"
            if name not in names:
                names.append(name)
    return (names or (["llc_ipos"] if MODULAR.search(description) else []))[:MAX_EXAMPLES]


def module_example(description: str) -> str:
    # Preserve the port-link guidance for collapsed library modules.
    if MODULAR.search(description) and LIBRARY_MODULE.search(description) and FOLDED.search(description):
        return ("\n\nThese modules are in the module library: write modules.instances, "
                "modules.parts and modules.links (port names only, no coordinates) as in "
                "the PORT LINKS example, with display set as asked (collapsed / partial) "
                "and a symbol such as dcdc when a function mark is wanted.")
    from pathlib import Path
    examples = []
    for name in example_names(description):
        if name in {"three_boost", "mmc_arm_stack"}:
            data = json.loads((Path(__file__).with_name("examples") / (name + ".json")).read_text(encoding="utf-8"))
            table = json.dumps(data, ensure_ascii=False)
        else:
            table = _example(name)
        examples.append(f"\n\nWorked example ({name}), validated for IEEE double column:\n" + table)
    return "".join(examples)


def first_message(description: str, structure: str | None = None) -> str:
    return _first_message(description, structure) + module_example(
        description + " " + (structure or ""))


def _first_message(description: str, structure: str | None = None) -> str:
    if structure:
        return ("Draw this circuit as a coordinate table:\n\n" + description.strip() +
                "\n\nThe user has confirmed this structure; draw exactly these parts "
                "and connections, nothing more:\n\n" + structure.strip() +
                "\n\nFollow the standard textbook drawing of this topology and declare "
                "the full netlist.  If the structure groups parts into 【模块】, add one "
                "region per module with its members and name as the label, and draw each "
                "module as a block: lay out one, then copy it at a fixed pitch.  "
                "For large circuits, draw repeated legs or cells as "
                "identical columns at a fixed pitch.  Control boxes, signal arrows and grey "
                "controller areas the description asks for are not parts: draw them as "
                "blocks, line callouts and regions.  Reply with the ```json block only.")
    return ("Draw this circuit as a coordinate table:\n\n" + description.strip() +
            "\n\nBefore the JSON block, write a short section in Chinese headed "
            "\"结构理解\" (at most 10 lines): which variant you are drawing, the parts and "
            "how they connect, and every assumption you made where the description is "
            "silent.  The user reads it to check your reading of the circuit.\n"
            "Then follow the standard textbook drawing of this topology.  Name parts the "
            "usual way (S1.., D1.., Lr, Cr, Co, RL, Vin) and declare the full netlist.\n"
            "For large circuits, plan a column grid first: draw repeated legs or cells as "
            "identical columns at a fixed pitch and reuse the same relative coordinates "
            "in each, so one leg that passes means all of them pass.  A circuit made of "
            "modules gets one region per module (members, label) and the same block "
            "copied at a fixed pitch.")


def _drawing_table(table: dict) -> dict:
    """Keep derived bookkeeping out of every initial and repair JSON prompt."""
    return {key: value for key, value in table.items() if key != "electrical"}


def _electrical_targets(table: dict) -> str:
    section = table.get("electrical")
    if not section:
        return ""
    anchors = {n.get("name", n["id"]): n["pins"][0] for n in section["nodes"]}
    branches = {str(number): ref for ref, number in section["branches"].items()}
    return ("\nRead-only electrical targets (do not return these maps):\n" +
            json.dumps({"node_anchors": anchors, "branch_targets": branches}, ensure_ascii=False) +
            "\nWire to the named node's anchor pin; other pins already connected to it are the same node. "
            "The program maintains electrical after your edit; omit it from the JSON.\n")


def tweak_message(instruction: str, table: dict, errors: list[str] | None = None,
                  profile: str = "ieee-single") -> str:
    """Change an existing table: the model edits it, it does not redraw."""
    from ..electrical import validate_instruction
    from ..layout import Layout
    validate_instruction(instruction, Layout.from_dict(table))
    diagnostics = ("\n\nCurrent validator report for " + profile + ":\n" +
                   ("\n".join(errors) if errors else "No connection/geometry errors.") +
                   "\nFor a general repair request (e.g. 返工修改), fix these listed errors. "
                   "For a specific edit, use them as context and preserve unrelated geometry. "
                   "Do not change the declared circuit to silence errors. If only column "
                   "width prevents a fit, explain the need for double column; do not drop parts.")
    return ("Here is the current coordinate table; it may have unresolved validator errors:\n"
            "```json\n" + json.dumps(_drawing_table(table), ensure_ascii=False) + "\n```\n\n" + _electrical_targets(table) +
            "Apply only this change:\n\n" + instruction.strip() + diagnostics + "\n\n"
            "Rules for a change:\n"
            "- Keep every element id, pin coordinate, label, wire, hop and annotation the "
            "change does not need to touch exactly as it is; do not tidy or re-space the rest.\n"
            "- When you move a part, move the wire vertices on its pins with it and keep "
            "the wires orthogonal; when you add or remove a part, update the netlist.\n"
            "- Do not return electrical or its IDs/counters/membership. Use the read-only node/branch targets; the program preserves and updates the ledger. Keep no_connect and model_mapping unless explicitly requested.\n"
            "- Preserve unrelated callout border_color/border_width/fill_opacity/foreground/over_parts. Only explicit requests to cover parts/text authorize over_parts; never use it merely to silence a layout error.\n"
            "- Keep the name and the same validator rules as always.\n"
            "- Keep opts.zener and breakdown_voltage/breakdown_current/zener_resistance unchanged unless explicitly asked to edit that diode or its ratings.\n"
            "- Keep regions (module blocks), blocks (control boxes) and callouts unless the change is about them; "
            "when you add a part to a module, add its id to that region's members. "
            "Preserve fit=members and its member IDs; do not replace derived bounds with coordinates.\n"
            "- Copy waveform (CSV binding, intervals, notes including color/font_pt/line_pt/radius_cm/offset, label_offsets) unchanged; it is edited only in the waveform panel.\n"
            "- For an explicit node/branch-targeted physical-value edit, update only the named simulation.components value and label; otherwise preserve simulation unchanged, including simplified_igbt/vce0, sine amplitude/phase/timing, ideal_transformer ratio/polarity/reference_resistance/magnetizing_inductance/leakage_inductance, winding probes, node_capacitance, series_resistance, snubber_resistance/snubber_capacitance, output_capacitance, diode_is/diode_n/diode_rs, holding_current/latching_current/turn_off_time and phase_shift/phase_ramp. Preserve all unrelated physical values and PWM during drawing edits.\n"
            "- For problems beginning with 模块定义, fix the shared definitions/layout local coordinates; do not move instance at.\n"
            "- For modules, edit definitions/layouts, instance placements or explicit bindings in the source JSON; never write expanded children to elements. Preserve module defaults, instance overrides, links and stack_usage. module_view is expanded or folded; per instance, display is expanded or collapsed. Links are port-name pairs; never add coordinates or wires for them.\n"
            "- Reuse user modules from the supplied project library by definition name and exact public port names. Preserve saved definitions, port_layout, editable and module_authoring. Never use unconnected to hide a missing connection.\n"
            "Before the JSON block, write a short section in Chinese headed \"改动说明\" "
            "(at most 6 lines) saying what you changed, then return the whole new table in "
            "one ```json block.")


def regression_message(report_lines: list[str], best_lines: list[str],
                       best_table: dict) -> str:
    hints = _hints(best_lines)
    return (f"That rewrite made things worse ({len(report_lines)} problems, the best table "
            f"so far had {len(best_lines)}).  Discard it and start again from the best "
            "table below.  Change only the elements and wires named in its problems; copy "
            "every other coordinate unchanged.\n\nBest table so far:\n```json\n" +
            json.dumps(_drawing_table(best_table), ensure_ascii=False) + "\n```\n\nIts problems:\n" +
            "\n".join(f"- {line}" for line in best_lines) +
            ("\n\nHow to fix them:" + hints if hints else "") +
            "\n\nReturn the whole corrected table in one ```json block.")


# how to fix each kind of problem, sent along with the report
HINTS = {
    "module-box-overlap": "Shrink collapsed_size or move the named instances/devices so their rendered boxes do not overlap each other or cover external symbols, leads, labels or pins. Boundary contact and the box's own ports are allowed. Preserve links, port names, hidden internals and electrical parameters; do not delete a device to silence the error.",
    "electrical-label-space": "Do not write electrical metadata. Make space around the named element by moving its requested geometry or nearby text; network names can be shortened in the editor.",
    "bad-electrical": "electrical is program-owned: omit it from your reply. Edit only the requested drawing geometry/netlist; the program reconciles saved identities.",
    "bad-no-connect": "Use no_connect only for existing intentionally open pins; remove connected or unknown pins.",
    "bad-model-mapping": "Use switch/R/L/C and documented model values/fields; all parameters positive, gate_voltage > vto. Do not combine L parasitic mapping with nonzero per-device series_resistance; choose one explicitly requested resistance configuration, preserving existing damping otherwise.",
    "module-definition": '“模块定义”开头的问题改 definitions 里的局部坐标，不动实例 at。'
                         '库模块应修正库定义；不要逐个改副本来掩盖共享错误。',
    "bad-region": 'For fit="members", supply a non-empty list of existing element IDs; '
                  'use local IDs in definitions or expanded IDs such as A/S1 in the assembled table. '
                  'Only fit="members" is supported; otherwise supply explicit from/to.',
    "region-fit-overrides": 'fit="members" takes precedence over from/to. Remove the stale corners, '
                            'or freeze the fit with the editor button if manual corners are intended.',
    "region-cuts-symbol": "The region boundary cuts the named symbol. Correct the members or manually "
                          "adjust the circuit/region; fitting never moves non-member parts.",
    "module-route": "a module link could not be routed and is drawn as a plain right-angle line; give it bend points in modules.routes[\"A|B\"] (layout units) clear of parts, or adjust collapsed_size / instance spacing; never drop the link",
    "bad-module": "Check modules version 1, definitions or library names (buck, boost, llc_halfbridge, dab), real devices, complete local netlists, public port.p references, bindings or links. A dangling port needs a link; a misspelt port must use one of the names listed in the error; parts need both pins linked to module ports on one side. Each global port name needs both ends. Use unique instance IDs; folded groups must reference existing instances. Do not drop hidden modules or change electrical parameters to silence errors. display is expanded / collapsed / partial (partial only for modules that define one); symbol is one of the listed marks; collapsed_size is {w, h} of at least 0.8. For 该尺寸无法布线, adjust collapsed_size or instance spacing while preserving links and parameters. User modules use their saved public port names. port_layout covers every port with side left/right and unique nonnegative order; editable lists allowed override fields. unconnected lists only explicitly unused existing ports. Preserve module_authoring; never hide devices in terminals or rewrite aliases to suppress an error.",
    "bad-option": "Use only the label_pos values and opts keys listed for that element kind in the "
                  "Field reference, with a value in the stated range; delete a misspelt or "
                  "inapplicable key instead of inventing one.",
    "bad-annotation": "Give the annotation " + ", ".join(
        f"{f.name} {f.accepted()}" for f in schema.ANNOTATION_FIELDS if not f.owner) + ", or omit the field.",
    "bad-block": "Give each blocks entry a unique id, from/to corners, one line of text and only the "
                 "listed fill/border/color values; delete unknown keys.",
    "block-text-overflow": "Widen (or heighten) the named block until its text fits, keeping it clear "
                           "of symbols, wires and other blocks; or shorten the text.",
    "block-on-symbol": "Move the block (and the arrows ending on it) clear of the circuit symbol; "
                       "control boxes sit outside the power stage.",
    "line-through-block": "Route the named wire around the block; a block is not part of the circuit.",
    "callout-through-block": "End the signal arrow on the block's edge, or add via corners so it goes around.",
    "block-overlap": "Move the two blocks apart; each control box stands alone.",
    "text-on-block": "Move the named text out of the block; text belonging in the box is the block's own text.",
    "isolation-on-label": "Move isolation or label_at so the isolation mark and the inline label are apart.",
    "bad-glyph": f"Each glyphs entry needs kind ({', '.join(GLYPH_KINDS)}) and from/to corners of a non-empty box; put the mark inside its block.",
    "too-short": "move one pin outward along the part's axis by the stated amount and "
                 "move the wire vertex on that pin with it",
    "text-on-symbol": "change label_pos (left/right/above/below) to the free side, or "
                      "move the parts further apart (vertical loads side by side need "
                      "about 0.9 units between axes when labels sit between them)",
    "text-on-line": "change label_pos to the side away from that wire, or move the wire",
    "callout-overlap-allowed": "user explicitly allowed this block arrow to cover parts/text; preserve over_parts unless asked to remove that permission",
    "transparency-export": "PDF preserves translucent fill; inspect flattening when converting to EPS or a print workflow",
    "text-overlap": "move one of the two labels to another side",
    "line-through-body": "a wire crosses a symbol body: move the named wire segment "
                         "outside the body's extent, preserving its endpoints and net. "
                         "If a recent device move caused this, undo that move; a hop "
                         "cannot fix a wire passing through a device",
    "subscript-braces": "wrap the whole subscript in braces, e.g. $V_{in}$ instead of $V_in$",
    "detail-wired": "a zoomed detail is a redrawn copy: end its terminals in ports and drop the "
                    "wires to the circuit, or move the arrow so it does not end inside the "
                    "region; add \"detail\": false only if the user says the region is the real circuit",
    "unmarked-crossing": "first consider a shorter route with no crossing; if the "
                         "crossing is necessary, declare it in hops on a horizontal wire",
    "netlist-open": "connect the pins with a wire, or fix the netlist if they should "
                    "not be connected",
    "netlist-short": "separate the two nets: a wire or T-junction joins them",
    "too-cramped": "the drawing is too wide for the column: make it narrower",
    "open-pin": "run a wire to the pin",
    "dangling-wire": "end the wire on a pin or on another wire",
}


def _hints(report_lines: list[str]) -> str:
    codes = ["module-definition"] if any("模块定义 " in line for line in report_lines) else []
    for line in report_lines:
        code = line.split(":", 1)[0]
        if code in HINTS and code not in codes:
            codes.append(code)
    return "".join(f"\n- {c}: {HINTS[c]}" for c in codes)


def identical_message(report_lines: list[str], best_table: dict,
                      best_lines: list[str]) -> str:
    return ("Your last table has exactly the same problems as the one before: nothing "
            "was fixed.  Start from this table and change the coordinates named in its "
            "problems:\n```json\n" + json.dumps(_drawing_table(best_table), ensure_ascii=False) +
            "\n```\n\nIts problems:\n" + "\n".join(f"- {line}" for line in best_lines) +
            "\n\nHow to fix them:" + _hints(best_lines) +
            "\n\nReturn the whole corrected table in one ```json block.")


def rework_message(report_lines: list[str], scale: tuple[float, float] | None,
                   profile: str, current: dict | None = None) -> str:
    head = "The validator rejected the table"
    if scale:
        head += f" (profile {profile}, {scale[0]:.3f} cm/unit horizontally)"
    base = ("" if current is None else
            "\n\nThe tool already widened a few parts that were a hair too short; "
            "continue from this table:\n```json\n" +
            json.dumps(_drawing_table(current), ensure_ascii=False) + "\n```")
    hints = _hints(report_lines)
    return (head + ":\n\n" + "\n".join(f"- {line}" for line in report_lines) + base +
            ("\n\nHow to fix them:" + hints if hints else "") +
            "\n\nFix every listed problem by moving only the elements, wires, labels and "
            "annotations named in it (and wires attached to a moved pin).  Copy every "
            "other coordinate unchanged: a fresh redraw usually creates new problems.  "
            "Return the whole corrected table in one ```json block (no 结构理解 section "
            "needed this time).")


CONTROL_HINTS = {
    "control-appearance": "按外观字段表修正颜色和线型；使用列出的颜色名、中文别名或 #rrggbb；新图未要求配色时省略外观键，编辑时保留未要求更改的配色。",
    "control-schema": "只使用控制结构协议字段；列表不能写成对象，不能夹带坐标表。",
    "control-coordinates": "AI 只写方块、端口连线、分区成员；删除 at/size/pole/via/from-to 坐标，交给模板和布局适配器。",
    "control-block": "使用唯一的合法方块 id；所有引用和分区成员必须存在。",
    "control-type": "图形类型只可为 block/limiter/tag/sum/mul/div/switch；PI、PR、LPF 是 block 的 text/tf，不是 type。",
    "control-endpoint": "连线端点写已存在的 id.w/id.e/id.n/id.s；开关用 id.c 或声明的 throws 接点名。",
    "control-dangling": "补齐缺失的输入或输出连接；只有真实外部输入/最终输出可列入 inputs/outputs，不可把中间控制器伪装成外部端口。",
    "control-input": "每个输入端口只接一个来源；合并用 sum，乘除用 mul/div；输入和输出不要复用同一个端口。",
    "control-sign": "sum.signs 必须覆盖全部输入端口且至少两个，每个值只能是 + 或 -；负反馈通常接负号端。",
    "control-algebraic-loop": "存在全由直通环节组成的有向环；检查误接线，只有需求中真实存在的积分、严格真有理动态或延时才可打断环，不得编造参数。",
    "control-tf": "复用 loop.tf 语法：pi(kp,ki)、pr(kp,kr,f0,fc)、lpf(fc)、gain 或 num/den；参数必须有限，引用量须定义在 loop.params。",
    "control-template": "template/snippets 必须使用目录中的名称；片段插入后补齐与整图的连接。",
    "control-layout": "几何检查未通过；优先使用现有模板/片段，缩短或分行文字、减少不必要分区。不要输出坐标；仍无法通过时保留原图交用户处理。",
}


def control_system_prompt(templates, snippets):
    """Control uses the same provider/rewrite engine with a coordinate-free format."""
    import json
    from ..schema import control_appearance_reference
    return r'''You draw control block diagrams. Return one complete JSON object, no coordinates.
FORMAT (AI exchange only; the saved file remains the ordinary kind=control table):
template: optional catalog name, e.g. "dual_loop". Prefer its authored geometry.
snippets: optional list of catalog names, e.g. ["foc_speed_pi"]. They are inserted
with the existing snippet machinery; reconnect exposed signals in links.
name/title: optional strings. blocks: a list of patches by unique id; unspecified
template/current blocks and fields are kept. remove: explicit block ids to remove.
Each block: id, type, text (LaTeX allowed), label, label_pos, signs, style,
fill/border/color/size_pt, optional tf, and for a switch throws (names only), on/toggle.
type is exactly block, limiter, tag, sum, mul, div or switch. PI/PR/LPF/PWM are
ordinary block text, never new types. Example:
{"template":"dual_loop","blocks":[{"id":"pii","text":"PR","tf":{"pr":{"kp":0.02,"kr":60,"f0":50,"fc":1}}}]}
This numerical example is syntax only: use values from the request or the selected
template, never invent design gains or plant parameters. When values are absent,
keep symbolic labels and say in title that analysis parameters still need filling.
links: optional COMPLETE replacement list of {from:"id.port",to:"id.port",label?}.
Ports w/e/n/s (optional existing numeric port offsets), switch c and throw names.
inputs/outputs: optional COMPLETE lists of external input/final output block ids.
stateful: optional COMPLETE list of block ids with known internal state and NO
direct feedthrough (e.g. PMSM current plant), when no numerical tf is supplied.
It is NOT valid for a PI with nonzero kp, gain, sum or arbitrary unknown block.
Never invent state merely to bypass an algebraic-loop error. Numeric tf takes
precedence: contradictions are rejected. These boundary/dynamics lists are saved
as control_ai metadata, e.g. {"control_ai":{"inputs":["ref"],"outputs":["pwm"],"stateful":[]}}.
control_ai adds no numeric dynamics and never substitutes for loop parameters.
dynamic_links: optional COMPLETE list of {from,to} pairs matching existing links,
only for real state-dependent measurements drawn as a tap on a power connection.
For example the FOC template's inverter-to-Clarke current tap measures PMSM current
state, not instantaneous inverter voltage. Preserve catalog annotations; never add
one to a purely algebraic feedback path. AI may only retain/remove dynamic_links
already authored in the selected template/current table; adding exemptions is
rejected. For a new dynamic path, use an actual dynamic block and its TF.
It is also saved under control_ai and is
not a numeric plant model. Unknown or removed link references are errors.
Never declare a missing internal input/output external merely to pass validation.
sum.signs maps EVERY incoming side to "+"/"-", with at least two signed inputs.
regions: optional COMPLETE list of {id,members:[block ids],label,fill,border,border_color,label_pos}.
Never output at, size, pole, via, coordinate endpoints, free text coordinates or code.
Do not delete/change unrelated blocks, labels, signs, connections, regions or tf on edits.
loop: existing control loop data only (params, tf, plant, breaks, closed, freq).
tf on a block is mapped into loop.tf under the same id; use the same pi/pr/lpf/gain/
num+den/model forms as loop analysis. Keep existing loop/code data unless a requested
structural removal invalidates that block. The table's functions section (Clarke, Park,
inverse Park, SVPWM, wrap blocks with their input/output ports) is kept as it is; keep
those blocks and the port offsets of their links, and never invent one. Do not call a controller linear if its
parameters are missing. Do not guess an absent plant to fabricate a Bode plot.
CHECKS: known types/ports, no dangling internal I/O, one driver per input, explicit
sum signs, no all-direct-feedthrough algebraic cycles, then the existing geometry
and loop parameter validator. Legitimate dynamic feedback is allowed. On failure,
use the error code + hint to rewrite the full proposal (max 3 model replies total).
Plan the structure internally; do not emit a separate coordinate-writing stage.
TEMPLATES (structure only):
''' + json.dumps(templates, ensure_ascii=False) + '\nSNIPPETS (structure only):\n' + json.dumps(snippets, ensure_ascii=False) + '\nERROR HINTS:\n' + json.dumps(CONTROL_HINTS, ensure_ascii=False) + '''
APPEARANCE FIELD REFERENCE (generated):
Only add/change appearance when the user requests colours or line styles. Otherwise
omit these optional keys and preserve existing fill/border/color/dash, including on
links that must be rerouted. Tag style sets defaults only; explicit colours win.
Link labels/arrowheads follow link colour; branch dots remain black. Appearance has
no effect on transfer functions, simulation or code generation. Free texts are kept
from the current table, never invent coordinates to recolour them.
Example: {"blocks":[{"id":"pi","fill":"lightblue","border":"blue","color":"red"}]}
''' + control_appearance_reference()
