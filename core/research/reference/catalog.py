"""The 21 web strategies' capabilities (design D6), derived from the existing registries; no new hand-written list.

Natives keep the governed RUNNABLE / BLOCKED decision of scripts/legacy_baseline_run.py; TQuant ports run on the same pinned research bars;
the Fundamental shadow and H4 stay with their governed research processes.
"""
from __future__ import annotations

from dataclasses import dataclass

NATIVE_NEEDS = ('official_bars', 't86_flows', 'mi_margn', 'financial_statements', 'monthly_revenue', 'publication_dates', 'corporate_actions')
TQUANT_NEEDS = ('official_bars:close_price,high_price,low_price,volume',)


@dataclass(frozen=True)
class Capability:
    strategy_id: str
    family: str  # native | tquant | fundamental | research
    engine: str | None  # LEGACY_V2 | TQUANT_SNAPSHOT | None
    governance: str | None  # governed elsewhere: never run here
    static_block: str | None  # a governed data blocker
    data_needs: tuple


def capabilities() -> list[Capability]:
    from core.research import h4_signal
    from core.runtime import fundamental_production
    from core.strategy_manager import StrategyManager
    from scripts import legacy_baseline_run as legacy
    rows = []
    for sid, meta in StrategyManager.STRATEGY_METADATA.items():
        if meta.source == 'tquant':
            rows.append(Capability(sid, 'tquant', 'TQUANT_SNAPSHOT', None, None, TQUANT_NEEDS))
        else:
            rows.append(Capability(sid, 'native', 'LEGACY_V2', None, legacy.BLOCKED.get(sid), NATIVE_NEEDS))
    rows.append(Capability(fundamental_production.STRATEGY_ID, 'fundamental', None, 'FUNDAMENTAL_PIT_SHADOW', None,
                           ('financial_statements', 'publication_dates')))
    rows.append(Capability(h4_signal.STRATEGY_ID, 'research', None, 'H4_SEALED_RESEARCH', None, ('outputs/research_mode/prepared_v6',)))
    return rows
