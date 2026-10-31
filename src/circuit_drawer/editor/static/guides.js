/* Smart guides while dragging a part, as in PowerPoint: the part snaps to
   other parts' pins, body centres and body edges, or to the spacing of its
   neighbours in a row or column, and the match is drawn as a dashed line.
   Schematic priority: a pin lined up with another pin (the wire between them
   stays straight) beats a centre or an edge, which beats equal spacing.  A
   hit beats the grid; hold Alt to drag on the grid alone.  Nothing is
   written to the table except the part's (and its wires') new coordinates.
   Free text is dragged the same way: its box's centre and edges snap to
   other texts, part labels and part bodies. */

// Snap reach in screen pixels, so it feels the same at any zoom (PowerPoint
// and Figma use about 5-8 px).
const GUIDE_PX = 6;
// Two parts are in one row (column) when their bodies overlap across it by
// at least this share of the smaller one, so a part above a row is not
// spaced with it.
const GUIDE_ROW_OVERLAP = 0.3;

function guideBox(id, pins) {
  const b = report?.geometry?.[id]?.bodies || [];
  const pts = Object.values(pins);
  const box = b.length ? [Math.min(...b.map(q => q[0])), Math.min(...b.map(q => q[1])),
                          Math.max(...b.map(q => q[2])), Math.max(...b.map(q => q[3]))]
    : [Math.min(...pts.map(p => p[0])), Math.min(...pts.map(p => p[1])),
       Math.max(...pts.map(p => p[0])), Math.max(...pts.map(p => p[1]))];
  // span: body and pins together, for telling whether two parts share a row
  const span = [Math.min(box[0], ...pts.map(p => p[0])), Math.min(box[1], ...pts.map(p => p[1])),
                Math.max(box[2], ...pts.map(p => p[0])), Math.max(box[3], ...pts.map(p => p[1]))];
  return {box, span, pins: pts, centre: [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]};
}

// A box with no pins: a text, or a part's label.  A label is not spaced with
// its neighbours, as it sits beside its own part.
function textGuideBox(box, label = false) {
  return {box, span: box, pins: [], label, centre: [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]};
}

// The part's own features and every other part's, captured when the drag
// starts (the others do not move).  The part's body is taken from the last
// check relative to its pins, so it moves with them during the drag.
function smartGuides(e) {
  const others = layout.elements.filter(o => o !== e && o.pins).map(o => ({id: o.id, ...guideBox(o.id, o.pins)}));
  return guidesFor(guideBox(e.id, e.pins), others);
}

// Free text i, by the box the last check drew it in (its anchor alone when
// there is none yet): other texts, every part's labels and bodies.  A text
// has no pins, so its centre and edges decide, then equal spacing among the
// texts and parts.
function textGuides(i) {
  const boxOf = j => { const a = layout.annotations[j];
    const projected = layout.modules ? Object.entries(viewInfo().root_decorations?.annotations || {}).find(([,source]) => source === j)?.[0] : j;
    const b = report?.annotations?.[projected];
    return textGuideBox(b || [a.at[0], a.at[1], a.at[0], a.at[1]]); };
  const texts = layout.annotations.map((_, j) => j).filter(j => j !== i).map(boxOf);
  const parts = layout.elements.filter(o => o.pins).flatMap(o => [{id: o.id, ...guideBox(o.id, o.pins)},
    ...(report?.geometry?.[o.id]?.labels || []).map(b => ({id: o.id, ...textGuideBox(b, true)}))]);
  return guidesFor(boxOf(i), [...texts, ...parts]);
}

// Snapping and the lines to draw for a dragged box ``me`` among ``others``.
function guidesFor(me, others) {
  const rect = svg.getBoundingClientRect();
  const reach = GUIDE_PX * (view ? view.w / Math.max(rect.width, 1) : 0.02);
  const shifted = (d, axis) => ({
    pins: me.pins.map(p => p[axis] + d), centre: me.centre[axis] + d,
    edges: [me.box[axis] + d, me.box[axis + 2] + d]});

  // best hit along one axis (0: x, 1: y) for a raw shift d; cross is the
  // shift across it, which decides the row the part is in
  function along(axis, d, cross) {
    const mine = shifted(d, axis);
    const tiers = [
      ["pin", mine.pins, others.flatMap(o => o.pins.map(p => ({v: p[axis], o})))],
      ["centre", [mine.centre], others.map(o => ({v: o.centre[axis], o}))],
      ["edge", mine.edges, others.flatMap(o => [o.box[axis], o.box[axis + 2]].map(v => ({v, o})))],
    ];
    for (const [kind, feats, targets] of tiers) {
      let best = null;
      for (const f of feats) for (const t of targets) {
        const gap = t.v - f;
        if (Math.abs(gap) <= reach && (!best || Math.abs(gap) < Math.abs(best.gap) - 1e-9)) best = {gap, v: t.v, o: t.o, kind};
      }
      if (best) {
        // every other part on the same line, for the drawn guide
        const near = others.filter(o => kind === "pin" ? o.pins.some(p => Math.abs(p[axis] - best.v) < 1e-6)
          : kind === "centre" ? Math.abs(o.centre[axis] - best.v) < 1e-6
          : Math.abs(o.box[axis] - best.v) < 1e-6 || Math.abs(o.box[axis + 2] - best.v) < 1e-6);
        return {d: d + best.gap, guide: {axis, v: best.v, kind, with: near}};
      }
    }
    return spacing(axis, d, cross);
  }

  // equal spacing: the part's centre continues or splits the gaps of its
  // neighbours in the same row (axis 0) or column (axis 1)
  function spacing(axis, d, cross) {
    const other = 1 - axis, lo = me.span[other] + cross, hi = me.span[other + 2] + cross;
    const row = others.filter(o => {
      if (o.label) return false;
      const overlap = Math.min(hi, o.span[other + 2]) - Math.max(lo, o.span[other]);
      return overlap > GUIDE_ROW_OVERLAP * Math.min(hi - lo, o.span[other + 2] - o.span[other]);
    }).sort((a, b) => a.centre[axis] - b.centre[axis]);
    const c = me.centre[axis] + d;
    let best = null;
    const consider = (v, chain) => { const gap = v - c;
      if (Math.abs(gap) <= reach && (!best || Math.abs(gap) < Math.abs(best.gap))) best = {gap, v, chain}; };
    for (let i = 0; i + 1 < row.length; i++) {
      const a = row[i], b = row[i + 1], g = b.centre[axis] - a.centre[axis];
      if (g < 1e-6) continue;
      consider(b.centre[axis] + g, [a, b, null]);       // after the pair
      consider(a.centre[axis] - g, [null, a, b]);       // before the pair
      consider((a.centre[axis] + b.centre[axis]) / 2, [a, null, b]);   // between them
    }
    if (!best) return {d, guide: null};
    return {d: d + best.gap, guide: {axis, v: best.v, kind: "space", chain: best.chain}};
  }

  const api = {lines: [], me, others,
    snap(rawX, rawY, free) {
      if (free) { api.lines = []; return [snap(rawX), snap(rawY)]; }
      // A pin lands on another pin, which is on the grid. Centre, edge and
      // spacing hits follow symbol bodies, which are not: their shift goes back
      // to the grid (within half a step of the guide) so the pins stay wireable.
      const settle = (hit, raw) => !hit.guide ? snap(raw) : !me.pins.length || hit.guide.kind === "pin" ? r4(hit.d) : r4(snap(hit.d));
      const hy = along(1, rawY, rawX), dy = settle(hy, rawY);
      const hx = along(0, rawX, dy), dx = settle(hx, rawX);
      api.lines = [hx.guide, hy.guide].filter(Boolean).map(g => ({...g, at: [dx, dy]}));
      return [dx, dy];
    }};
  return api;
}

// Dashed alignment lines in magenta and spacing marks, drawn over the
// drawing while the drag lasts.
function drawGuides() {
  const g = drag?.guides;
  if (!g || !g.lines.length) return;
  const root = el("g", {class: "smart-guides", "pointer-events": "none"}, svg);
  const stroke = {stroke: "#d6249f", "stroke-width": .02, fill: "none"};
  const pt = (axis, v, w) => axis === 0 ? [v, w] : [w, v];
  for (const line of g.lines) {
    const {axis, v, at} = line, other = 1 - axis;
    const box = [g.me.box[0] + at[0], g.me.box[1] + at[1], g.me.box[2] + at[0], g.me.box[3] + at[1]];
    if (line.kind === "space") {
      // the equal gaps, each with end ticks, along the row through the centres
      const cs = line.chain.map(o => o ? o.centre : [g.me.centre[0] + at[0], g.me.centre[1] + at[1]]);
      const w = cs.reduce((s, c) => s + c[other], 0) / cs.length;
      for (let i = 0; i + 1 < cs.length; i++) {
        const a = pt(axis, cs[i][axis], w), b = pt(axis, cs[i + 1][axis], w);
        el("line", {x1: a[0], y1: -a[1], x2: b[0], y2: -b[1], ...stroke, "data-guide": "space"}, root);
        for (const q of [a, b]) {
          const t1 = pt(axis, q[axis], w - .12), t2 = pt(axis, q[axis], w + .12);
          el("line", {x1: t1[0], y1: -t1[1], x2: t2[0], y2: -t2[1], ...stroke}, root);
        }
      }
      continue;
    }
    const spans = [box[other], box[other + 2], ...line.with.flatMap(o => [o.box[other], o.box[other + 2]])];
    const a = pt(axis, v, Math.min(...spans) - .2), b = pt(axis, v, Math.max(...spans) + .2);
    el("line", {x1: a[0], y1: -a[1], x2: b[0], y2: -b[1], ...stroke, "stroke-dasharray": ".08 .06",
                "data-guide": line.kind}, root);
  }
}
