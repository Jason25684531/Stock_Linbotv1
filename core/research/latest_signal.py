"""Latest-data H4 signals (h4-web-integration-v1 design D6): provider contract, PIT and health checks, readiness.

Disabled today: the only latest data is 2026, which is sealed, and strategy_signal.check_window is not relaxed here.
No provider is configured anywhere; tests use a synthetic one inside the development window."""
from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

from core.research import h4_signal, strategy_signal as ss
from core.strategy.contracts import StrategyContext

MAX_SESSION_LAG_DAYS = 5  # calendar days between the provider's latest session and today before the data counts as stale
MISSING = (
    'Owner forward-observation grant in the trusted external grant file (scripts/task_controller.require_grant)',
    '2026 daily price source and PIT fundamental source with first-publication dates',
    'Owner decision to lift strategy_signal.check_window for an isolated output directory',
    'separate scheduler job writing only to that directory; no recommendation table, LINE or broker path',
)


class LatestDataProvider(Protocol):
    # ponytail: contract only; no implementation exists until an Owner-approved 2026 source does
    def latest_session(self) -> str: ...
    def members(self, day: str) -> list[str]: ...
    def growth(self, day: str) -> dict[str, pd.DataFrame]: ...  # metric -> frame indexed by ticker with value, available_date
    def momentum(self, day: str) -> pd.Series | None: ...
    def digests(self) -> dict[str, str]: ...


class LatestSignalError(ValueError):
    """A provider output that cannot become a signal: PIT, stale, future or unhashed data."""


def build_latest_context(provider: LatestDataProvider, today: str | None = None) -> tuple[StrategyContext, dict]:
    today = today or date.today().isoformat()
    day = str(provider.latest_session())[:10]
    if day > today:
        raise LatestSignalError('FUTURE_SESSION')
    if (pd.Timestamp(today) - pd.Timestamp(day)).days > MAX_SESSION_LAG_DAYS:
        raise LatestSignalError('STALE_SESSION')
    ss.check_window(day)
    growth = provider.growth(day)
    available = pd.concat([frame['available_date'] for frame in growth.values()]) if growth else pd.Series(dtype=str)
    if (pd.to_datetime(available) > pd.Timestamp(day)).any():
        raise LatestSignalError('PIT_VIOLATION')
    digests = provider.digests()
    if not digests or not all(digests.values()):
        raise LatestSignalError('INPUT_DIGEST_MISSING')
    members = sorted(map(str, provider.members(day)))
    context = StrategyContext(asof_date=day, universe=tuple(members), data={
        'members': members, 'non_members': [], 'ready': list(growth), 'growth_day': {m: f['value'] for m, f in growth.items()},
        'momentum_day': provider.momentum(day), 'data_version': 'latest-provider', 'digests': digests,
        'input_dates': {'bars': day, 'fundamentals_latest_available': str(pd.to_datetime(available).max().date()) if len(available) else None,
                        'universe': day}})
    return context, {'signal_date': day, 'input_digests': digests, 'source_hash': ss.source_hash(digests)}


def latest_signals(provider: LatestDataProvider, today: str | None = None) -> dict:
    """Signals are withheld when data health reports an anomaly (missing inputs, coverage drop, input after signal date)."""
    context, manifest = build_latest_context(provider, today)
    signals = h4_signal.H4Executor().execute(context, h4_signal.SPEC).metadata['signals']
    health = ss.data_health(signals, context.data['input_dates'], context.asof_date)
    return {'status': health['status'], 'health': health, 'manifest': manifest, 'signals': signals if health['status'] == 'OK' else None}


def readiness(provider: LatestDataProvider | None = None) -> dict:
    if provider is None:
        return {'status': 'LATEST_SIGNAL_DISABLED', 'reason': 'NO_AUTHORIZED_LATEST_DATA_PROVIDER', 'missing': list(MISSING)}
    day = str(provider.latest_session())[:10]
    if day > ss.DEV_END:
        return {'status': 'LATEST_SIGNAL_DISABLED', 'reason': 'REQUIRES_SEALED_2026_DATA', 'latest_session': day, 'missing': list(MISSING)}
    return {'status': 'READY', 'latest_session': day, 'missing': []}
