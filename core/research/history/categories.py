"""The one table of historical data categories and the one rule that turns a year's evidence into a status (design D2, D3).

Requirements, coverage, the trust matrix, gap recovery and the web all read CATEGORIES; no other module lists categories.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.research.native_frame import FRAME_INPUTS

YEARS = tuple(range(2015, 2026))
STATUSES = ('VERIFIED', 'PARTIAL', 'ACCESS_BLOCKED', 'SOURCE_UNAVAILABLE', 'FAILED_VALIDATION')  # ascending severity
NOT_REQUIRED, COMPARISON_ONLY = 'NOT_REQUIRED', 'COMPARISON_ONLY'
FAIL_BELOW, AGREE_MIN, MIN_INDEPENDENT_COVERAGE = 0.99, 0.999, 0.5


@dataclass(frozen=True)
class Check:
    """One independent reconciliation: of `population` primary rows, `compared` exist in the second source and `agreed` match."""
    against: str
    population: int
    compared: int
    agreed: int

    @property
    def rate(self):
        return self.agreed / self.compared if self.compared else None


@dataclass(frozen=True)
class Evidence:
    local: bool
    expected: int | None = None
    present: int | None = None
    checks: tuple[Check, ...] = ()
    integrity: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    route: str | None = None  # denial code of the legal route for absent data; None = no legal source known
    covered: bool = False  # the source is known to cover the whole year, so zero events means none happened
    metrics: dict = field(default_factory=dict, compare=False)


def decide(e: Evidence) -> tuple[str, tuple[str, ...]]:
    if e.integrity:
        return 'FAILED_VALIDATION', e.integrity
    if not e.local or (e.present == 0 and e.expected != 0):
        return ('ACCESS_BLOCKED', (e.route,)) if e.route else ('SOURCE_UNAVAILABLE', ('NO_LEGAL_SOURCE',))
    if e.expected == 0:
        # an empty year cannot be reconciled against anything, so it never certifies "nothing happened"
        return 'PARTIAL', ('ZERO_EVENTS_NOT_INDEPENDENTLY_CONFIRMED' if e.covered else 'ZERO_EVENTS_SOURCE_COVERAGE_UNPROVEN',)
    bad = [f'RECONCILE_MISMATCH:{k.against}:{k.rate:.6f}' for k in e.checks if k.compared and k.rate < FAIL_BELOW]
    if bad:
        return 'FAILED_VALIDATION', tuple(bad)
    partial = []
    if e.expected is not None and e.present is not None and e.present < e.expected:
        partial.append(f'INCOMPLETE:{e.present}/{e.expected}')
    if not any(k.compared for k in e.checks):
        partial.append('NO_INDEPENDENT_SOURCE')
    else:
        covered = max(k.compared / k.population for k in e.checks if k.population)
        if covered < MIN_INDEPENDENT_COVERAGE:
            partial.append('INDEPENDENT_COVERAGE:' + ','.join(f'{k.against}:{k.compared / k.population:.4f}' for k in e.checks if k.population))
        partial += [f'RECONCILE_RATE:{k.against}:{k.rate:.6f}' for k in e.checks if k.compared and k.rate < AGREE_MIN]
    partial += list(e.caveats)
    if partial:
        return 'PARTIAL', tuple(partial)
    return 'VERIFIED', ('COMPLETE_AND_RECONCILED:' + ','.join(k.against for k in e.checks if k.compared),)


def worst(statuses) -> str:
    return max(statuses, key=STATUSES.index, default='VERIFIED')


@dataclass(frozen=True)
class Route:
    """The legal source that would fill a gap and why it cannot be used now (research_budget job names are proposals for the Owner)."""
    source: str
    endpoint: str
    budget_job: str | None  # None: not a research-budget route (restricted or no legal source)
    requests_per_year: int | None  # estimate for the Owner's request plan; None = unknown
    note: str = ''


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    role: str  # SIGNAL: changes signals or results; COMPARISON: only the benchmark columns
    inputs: frozenset = frozenset()  # native_frame.FRAME_INPUTS names served by this category
    priced: bool = False  # needed by every strategy that trades on prices
    soft: bool = False  # caps a strategy's trust at PARTIAL instead of blocking (FAILED_VALIDATION still propagates)
    route: Route | None = None


TECHNICAL = frozenset({'ma5', 'ma20', 'ma60', 'bias', 'rsi', 'macd_hist', 'kd_k', 'bb_width', 'atr', 'natr', 'std_20', 'volume_ratio', 'high_60'})
T86 = frozenset({'foreign_buy', 'trust_buy', 'dealer_buy', 'foreign_ratio', 'trust_ratio', 'dealer_ratio', 'foreign_consec_days',
                 'trust_consec_days', 'dealer_consec_days', 'chip_score'})
MARGIN = frozenset({'margin_balance', 'margin_change_pct', 'chip_score'})
FINANCIAL = frozenset({'eps', 'op_profit_margin', 'rd_ratio'})
REVENUE = frozenset({'revenue_yoy'})
TWSE = 'https://www.twse.com.tw/rwd/zh'

CATEGORIES = (
    Category('calendar', '交易日曆', 'SIGNAL', priced=True,
             route=Route('TWSE', f'{TWSE}/afterTrading/MI_INDEX', 'hist_foundation_quotes', None, 'one request per missing session')),
    Category('prices', '原始行情 OHLCV', 'SIGNAL', TECHNICAL, priced=True,
             route=Route('TWSE', f'{TWSE}/afterTrading/MI_INDEX', 'hist_foundation_quotes', None, 'one request per missing session')),
    Category('universe', '歷史股票池（上市）', 'SIGNAL', priced=True),
    Category('delistings', '下市股票覆蓋', 'SIGNAL', priced=True, soft=True),
    Category('dividends', '除權息（TWT49U）', 'SIGNAL', priced=True,
             route=Route('TWSE', f'{TWSE}/exRight/TWT49U', 'hist_foundation_events', 1)),
    Category('capital_changes', '減資與面額變更（TWTAUU／TWTB8U）', 'SIGNAL', priced=True, soft=True,
             route=Route('TWSE', f'{TWSE}/reducation/TWTAUU + /change/TWTB8U', 'hist_foundation_events', 2)),
    Category('benchmark_price', '加權股價指數', 'COMPARISON'),
    Category('benchmark_total_return', '加權股價報酬指數', 'COMPARISON'),
    Category('t86_flows', '三大法人（T86）', 'SIGNAL', T86,
             route=Route('TWSE', f'{TWSE}/fund/T86', 'hist_foundation_chips', 245)),
    Category('margin', '融資融券（MI_MARGN）', 'SIGNAL', MARGIN,
             route=Route('TWSE', f'{TWSE}/marginTrading/MI_MARGN', 'hist_foundation_chips', 245)),
    Category('financial_statements', '財報數值（引擎輸入）', 'SIGNAL', FINANCIAL,
             route=Route('MOPS', 'https://mops.twse.com.tw/mops/web/t163sb04', 'hist_foundation_financials', 8,
                         'local alternative data/processed/fundamental_pit_v3 has FY2013-2025 eps/operating income without rd_expense; no engine adapter')),
    Category('financial_publication_time', '財報首次公開時間（PIT）', 'SIGNAL', FINANCIAL,
             route=Route('MOPS', 'https://mops.twse.com.tw/mops/web/t57sb01', 'hist_foundation_financials', 8,
                         'correction history of amended filings is not served (t56sb31_q1 outside the public allowlist)')),
    Category('monthly_revenue', '月營收數值', 'SIGNAL', REVENUE,
             route=Route('MOPS', 'https://mopsov.twse.com.tw/nas/t21/sii/t21sc03', 'hist_foundation_revenue', 12)),
    Category('revenue_announcement_time', '月營收首次公告時間（PIT）', 'SIGNAL', REVENUE,
             route=Route('MOPS', 'MOPS daily material-information announcements', 'hist_foundation_revenue_pit', None,
                         't21sc03 carries no announcement time; historical depth of the announcement source is unverified')),
)
BY_ID = {cat.id: cat for cat in CATEGORIES}
PRICED = tuple(cat.id for cat in CATEGORIES if cat.priced)
if set().union(*(cat.inputs for cat in CATEGORIES)) != set(FRAME_INPUTS):  # a new frame input without a category must fail loudly
    raise ImportError('FRAME_INPUTS_NOT_COVERED: ' + ','.join(sorted(set(FRAME_INPUTS) - set().union(*(cat.inputs for cat in CATEGORIES)))))
