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
  const others = layout.elements.filter(o => o.id !== e.id && o.pins).map(o => ({id: o.id, ...guideBox(o.id, o.pins)}));
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

// Drawing objects use the visible geometry, including module children, as
// targets. Only the source object's coordinates are edited by the caller.
function drawingGuides(kind, index, point = false) {
  const shown = layout.modules && report?.module_info ? currentView(layout, report.module_info) : layout;
  const others = shown.elements.filter(e => e.pins).map(e => guideBox(e.id, e.pins));
  shown.wires.forEach((wire, i) => {
    if (kind === "wire" && sourceObjectIndex("wire", i) === index) return;
    for (const p of wire) others.push({...textGuideBox([p[0], p[1], p[0], p[1]]), pins: [p]});
  });
  for (const section of ["regions", "blocks"]) {
    (shown[section] || []).forEach((_, i) => {
      if (kind === section && sourceObjectIndex(section, i) === index) return;
      const box = section === "regions" ? report?.regions?.[i]?.rects?.[0] : report?.blocks?.[i]?.rect;
      if (box) others.push(textGuideBox(box));
    });
  }
  let me;
  if (point) {
    // A zero-origin point lets snap() keep the existing absolute grid for corners.
    me = textGuideBox([0, 0, 0, 0]);
    if (kind === "wire") me.pins = [[0, 0]];
  } else {
    const {from: a, to: b} = layout[kind][index];
    me = textGuideBox([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]);
  }
  return guidesFor(me, others);
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

// Arrow points align to their neighbours and to visible straight segments.
// Keep the reference geometry fixed for this gesture, just like box guides.
function arrowGuides(points, index, sourceIndex = -1) {
  const anchors = [points[index - 1], points[index + 1]].filter(Boolean);
  const shown = layout.modules && report?.module_info ? currentView(layout, report.module_info) : layout;
  const segments = [];
  function addSegments(path, moving = -1) {
    for (let i = 1; i < path.length; i++) {
      if (i !== moving && i - 1 !== moving) segments.push([path[i - 1], path[i]]);
    }
  }
  addSegments(points, index);
  shown.wires.forEach(path => addSegments(path));
  (shown.callouts || []).forEach((c, i) => {
    if (sourceObjectIndex('callouts', i) !== sourceIndex && c.style === 'line' && !c.bend)
      addSegments([c.from, ...(c.via || []), c.to]);
  });
  const api = {lines: [], snap(p, event) {
    api.lines = [];
    const grid = p.map(v => r4(snap(v)));
    if (!anchors.length) return grid;
    const reach = GUIDE_PX * view.w / Math.max(svg.getBoundingClientRect().width, 1);
    const line = (kind, a, b, reference) => ({kind, from:a, to:b, reference});
    if (event.shiftKey) {
      const a = anchors[0], horizontal = Math.abs(p[0] - a[0]) >= Math.abs(p[1] - a[1]);
      const q = horizontal ? [grid[0], a[1]] : [a[0], grid[1]];
      api.lines = [line(horizontal ? 'horizontal' : 'vertical', a, q)];
      return q;
    }
    if (event.altKey) return grid;
    // Both neighbours can constrain a corner, keeping each adjoining leg square.
    const q = grid.slice();
    for (const axis of [0, 1]) {
      const matches = anchors.filter(a => Math.abs(p[axis] - a[axis]) <= reach && Math.abs(p[1-axis] - a[1-axis]) > reach);
      matches.sort((a, b) => Math.abs(p[axis] - a[axis]) - Math.abs(p[axis] - b[axis]));
      if (matches.length) {
        q[axis] = matches[0][axis];
        api.lines.push(line(axis === 0 ? 'vertical' : 'horizontal', matches[0], q));
      }
    }
    if (api.lines.length) return q;
    let best = null;
    for (const reference of segments) {
      const [a, b] = reference, dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx, dy);
      // Axis-aligned references are already covered by the neighbouring anchors.
      if (Math.abs(dx) < 1e-9 || Math.abs(dy) < 1e-9) continue;
      for (const [ux, uy, kind] of [[dx/length, dy/length, 'parallel'], [-dy/length, dx/length, 'perpendicular']]) {
        for (const anchor of anchors) {
          const distance = (p[0] - anchor[0])*ux + (p[1] - anchor[1])*uy;
          const projected = [anchor[0] + distance*ux, anchor[1] + distance*uy];
          const gap = Math.hypot(p[0] - projected[0], p[1] - projected[1]);
          if (Math.abs(distance) > reach && gap <= reach && (!best || gap < best.gap - 1e-9))
            best = {gap, point:projected.map(r4), anchor, kind, reference};
        }
      }
    }
    if (!best) return grid;
    api.lines = [line(best.kind, best.anchor, best.point, best.reference)];
    return best.point;
  }};
  return api;
}

// Dashed alignment lines in magenta and spacing marks, drawn over the
// drawing while the drag lasts.
function drawGuides() {
  const g = drag?.guides || boxDraft?.guides;
  if (!g || !g.lines.length) return;
  const root = el("g", {class: "smart-guides", "pointer-events": "none"}, svg);
  const stroke = {stroke: "#d6249f", "stroke-width": .02, fill: "none"};
  const pt = (axis, v, w) => axis === 0 ? [v, w] : [w, v];
  for (const [index, line] of g.lines.entries()) {
    if (line.from) {
      const pixel = view.w / Math.max(svg.getBoundingClientRect().width, 1);
      for (const [a, b] of [line.reference, [line.from, line.to]].filter(Boolean)) {
        el('line', {x1:a[0], y1:-a[1], x2:b[0], y2:-b[1], ...stroke,
          'stroke-width':1.4*pixel, 'stroke-dasharray':`${5*pixel} ${4*pixel}`, 'data-guide':line.kind}, root);
      }
      const label = el('text', {x:line.to[0]+8*pixel, y:-line.to[1]-(8+16*index)*pixel,
        fill:stroke.stroke, 'font-size':12*pixel, 'font-family':'sans-serif',
        stroke:'white', 'stroke-width':3*pixel, 'paint-order':'stroke', 'data-guide-label':line.kind}, root);
      const text = {horizontal:'水平', vertical:'竖直', parallel:'平行', perpendicular:'垂直'}[line.kind];
      label.textContent = window.cdI18n ? cdI18n.translate(text) : text;
      continue;
    }
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
