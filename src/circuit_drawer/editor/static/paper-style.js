// Draw the same inset bands as TikZ in printed centimetres, also for unequal axes.
function drawPaperInset(el, parent, x, y, width, height, decoration) {
  if (!decoration?.inset?.length) return;
  const [sx, sy] = decoration.scale;
  const group = el('g', {transform: `translate(${x} ${y}) scale(${1/sx} ${1/sy})`,
    'pointer-events': 'none', 'aria-hidden': 'true', 'data-paper-inset': ''}, parent);
  for (const band of decoration.inset) {
    el('rect', {x: band.depth, y: band.depth, width: width*sx - 2*band.depth,
      height: height*sy - 2*band.depth, rx: band.radius, ry: band.radius,
      fill: 'none', stroke: 'black', 'stroke-opacity': band.opacity, 'stroke-width': band.width}, group);
  }
}
