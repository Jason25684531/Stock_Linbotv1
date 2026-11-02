"""Diagnostic-only charts for completed research-mode results."""
import os
from pathlib import Path
import re
import tempfile
import threading

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import pandas as pd

from core.research import factor_charts

_LOCK = threading.Lock()


def _validate(label, assumptions_sha):
    if label != 'DIAGNOSTIC_ONLY' or not re.fullmatch(r'[0-9a-f]{64}', str(assumptions_sha)):
        raise ValueError('DIAGNOSTIC_WATERMARK_REQUIRED')


def _watermark(figure, assumptions_sha):
    figure.text(.5, .5, 'DIAGNOSTIC_ONLY', ha='center', va='center', rotation=25, fontsize=24, alpha=.18)
    figure.text(.01, .01, f'DIAGNOSTIC_ONLY | assumptions SHA-256: {assumptions_sha}', fontsize=6)


def write_equity_chart(path, result, *, label, assumptions_sha):
    _validate(label, assumptions_sha)
    if result.get('status') != 'COMPUTABLE' or not {'base', 'stress'} <= result.get('tracks', {}).keys():
        raise ValueError('COMPLETE_COST_TRACKS_REQUIRED')
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    series = {}
    for name in ('base', 'stress'):
        daily = result['tracks'][name]['result']['daily']
        if not daily or any(row['date'] > '2025-12-31' for row in daily):
            raise ValueError('INVALID_DEVELOPMENT_SERIES')
        frame = pd.DataFrame(daily)
        frame['date'] = pd.to_datetime(frame['date'])
        if not pd.to_numeric(frame['nav'], errors='coerce').notna().all() or (frame['nav'] <= 0).any():
            raise ValueError('INVALID_NAV')
        series[name] = frame
    if not series['base']['date'].equals(series['stress']['date']):
        raise ValueError('COST_TRACK_DATE_MISMATCH')
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, (equity, drawdown) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    temporary = None
    try:
        for name, frame in series.items():
            equity.plot(frame['date'], frame['nav'], label=name)
            drawdown.plot(frame['date'], frame['nav'] / frame['nav'].cummax() - 1, label=name)
        equity.set_title('DIAGNOSTIC_ONLY — equity'); equity.legend()
        drawdown.set_title('Drawdown'); drawdown.grid(alpha=.25)
        _watermark(fig, assumptions_sha)
        fig.tight_layout(rect=(0, .035, 1, 1))
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.chart-', suffix='.png', delete=False) as stream:
            temporary = Path(stream.name)
        fig.savefig(temporary, dpi=120, metadata={'Description': f'DIAGNOSTIC_ONLY assumptions_sha256={assumptions_sha}'})
        os.link(temporary, path)
    finally:
        plt.close(fig)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def write_factor_charts(output, *, tables, scoreboard, label, assumptions_sha):
    _validate(label, assumptions_sha)
    output = Path(output)
    for frame in tables.values():
        if 'asof_date' in frame and (pd.to_datetime(frame['asof_date']) > pd.Timestamp('2025-12-31')).any():
            raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    output.mkdir(parents=True, exist_ok=False)
    original = Figure.savefig
    def stamped(figure, *args, **kwargs):
        _watermark(figure, assumptions_sha)
        kwargs['metadata'] = {'Description': f'DIAGNOSTIC_ONLY assumptions_sha256={assumptions_sha}'}
        return original(figure, *args, **kwargs)
    with _LOCK:
        Figure.savefig = stamped
        try:
            return factor_charts.write_charts(output, tables=tables, scoreboard=scoreboard,
                                              source_run_id='RAW_DEVELOPMENT', evaluation_run_id='DIAGNOSTIC_ONLY')
        finally:
            Figure.savefig = original
