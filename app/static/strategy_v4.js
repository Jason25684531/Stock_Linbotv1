/* Render only saved, validated curves. Nulls break lines; no metric calculation. */
(() => {
  const source = document.getElementById('v4-chart-data');
  if (!source) return;
  const data = JSON.parse(source.textContent), svg = document.getElementById('v4-curve');
  function draw() {
    const cost = document.getElementById('v4-cost').value;
    const curves = [data.original?.[cost], data.fixed?.[cost], data.original?.benchmark];
    const values = curves.flatMap(x => x || []).filter(Number.isFinite);
    svg.replaceChildren();
    if (!values.length) return;
    const lo = Math.min(...values), hi = Math.max(...values);
    curves.forEach((curve, k) => {
      if (!curve) return;
      let path = '', connected = false;
      curve.forEach((y, i) => {
        if (!Number.isFinite(y)) { connected = false; return; }
        path += `${connected ? 'L' : 'M'}${20 + 680 * i / Math.max(1, curve.length - 1)},${220 - 200 * (y - lo) / (hi - lo || 1)} `;
        connected = true;
      });
      const node = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      node.setAttribute('d', path); node.setAttribute('fill', 'none');
      node.setAttribute('stroke', ['#60a5fa', '#4ade80', '#9ca3af'][k]); node.setAttribute('stroke-width', '2');
      svg.appendChild(node);
    });
  }
  document.getElementById('v4-cost').addEventListener('change', draw); draw();
})();
