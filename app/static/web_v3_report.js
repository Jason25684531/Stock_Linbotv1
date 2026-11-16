(() => {
    const node = document.getElementById('v3-report-data');
    if (!node) return;
    const data = JSON.parse(node.textContent), series = data.series;
    const select = document.getElementById('report-cost');
    const kpis = ['cagr', 'total', 'mdd', 'recovery', 'excess'].map(k => document.querySelector(`[data-testid="kpi-${k}"]`));
    const original = kpis.map(el => el?.innerHTML);
    const missing = '— DATA_MISSING（未保存此成本軌指標）';
    const switchCost = () => {
        const stress = select.value === 'stress';
        document.getElementById('cost-basis').textContent = `${stress ? '兩倍成本' : '基礎成本'} · 全期 PROVISIONAL`;
        kpis.forEach((el, i) => { if (el) el.innerHTML = stress ? missing : original[i]; });
        if (stress && kpis[0]) {
            const item = data.base.cagr_stress;
            kpis[0].textContent = item?.value == null ? missing : `${(item.value * 100).toFixed(1)}%`;
        }
        if (stress) ['total_return', 'max_drawdown'].forEach((key, i) => {
            const value = data.stress_metrics?.[key]?.value;
            if (kpis[i + 1]) kpis[i + 1].textContent = value == null ? missing : `${(value * 100).toFixed(1)}%`;
        });
        document.getElementById('nav-base').style.display = stress ? 'none' : '';
        document.getElementById('nav-stress').style.display = stress ? '' : 'none';
        document.getElementById('run-dd').style.display = stress ? 'none' : '';
        document.getElementById('stress-dd-missing').hidden = !stress;
    };
    if (select) { select.addEventListener('change', switchCost); switchCost(); }
    const chart = document.getElementById('run-equity');
    chart?.addEventListener('pointermove', event => {
        const box = chart.getBoundingClientRect();
        const i = Math.max(0, Math.min(series.date.length - 1, Math.round(((event.clientX - box.left) / box.width * 800 - 40) / 720 * (series.date.length - 1))));
        const track = select?.value === 'stress' ? 'nav_stress' : 'nav_base';
        document.getElementById('chart-inspect').textContent = `${series.date[i]} · ${track} ${series[track]?.[i] ?? 'DATA_MISSING'} · benchmark ${series.benchmark_total_return?.[i] ?? 'DATA_MISSING'}`;
    });
    document.querySelectorAll('table[data-v3-page]').forEach(table => {
        const section = table.closest('details'), rows = Array.from(table.tBodies[0].rows);
        let page = 0; const size = 25, pages = Math.max(1, Math.ceil(rows.length / size));
        const draw = () => {
            rows.forEach((row, i) => row.hidden = Math.floor(i / size) !== page);
            section.querySelector('[data-v3-page-label]').textContent = `${page + 1} / ${pages}`;
        };
        section.querySelector('[data-v3-prev]').onclick = () => { page = Math.max(0, page - 1); draw(); };
        section.querySelector('[data-v3-next]').onclick = () => { page = Math.min(pages - 1, page + 1); draw(); };
        draw();
    });
})();
