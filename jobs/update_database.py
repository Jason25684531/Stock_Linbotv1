"""每日資料庫統一更新入口。

目前 covered dataset 全數經由 MCP transport boundary 取得：
- 市場快照 / 外資買賣超：`core.mcp_client.TWSEMCPClient`
- 季度財報：`core.update_financials_mops.update_quarter`

保留在本程序中的本地 enrichers：
- `core.update_monthly_revenue` 月營收
- `core.crawlers.chip_data_scraper` 融資融券

使用方式：`python jobs/update_database.py`
"""
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
from sqlalchemy import text
import time
import random
from datetime import datetime, timedelta
from config import Config
from core.db_helper import (
    MIN_VALID_MARKET_ROWS,
    _is_business_weekday,
    get_latest_trade_date,
    normalize_date_str,
    record_pipeline_step_finish,
    record_pipeline_step_start,
    save_dashboard_aggregation_cache,
)
from core.mcp_client import (
    ForeignInvestorFlowRequest,
    MCPFetchJob,
    MCPRequestContext,
    StockBasicSnapshotRequest,
    TWSEMCPClient as MCPClient,
)


# ============================================
# ⚙️ 設定區 (統一使用 Config)
# ============================================

DEFAULT_DASHBOARD_PREWARM_STOCKS = [
    stock_id.strip()
    for stock_id in os.getenv('DASHBOARD_PREWARM_STOCKS', '2330,2317,2454').split(',')
    if stock_id.strip()
]


def _resolve_pipeline_run_context(step_name: str) -> tuple[str, str, str]:
    pipeline_name = str(os.getenv('STOCK_PIPELINE_NAME') or '').strip() or 'manual'
    run_date = normalize_date_str(os.getenv('STOCK_PIPELINE_RUN_DATE')) or datetime.now().strftime('%Y-%m-%d')
    return pipeline_name, step_name, run_date

# ============================================
# 🛠️ 核心功能：抓取全市場資料
# ============================================




def get_earliest_incomplete_date(engine, lookback_days: int = 7):
    """近期缺法人或融資融券（覆蓋不到一半）的最早交易日，供回補。"""
    # ponytail: fixed lookback window; older gaps need jobs/backfill_pipeline.py.
    since = datetime.now().date() - timedelta(days=lookback_days)
    try:
        with engine.connect() as conn:
            earliest = conn.execute(text("""
                SELECT MIN(trade_date) FROM (
                    SELECT trade_date FROM daily_market_data
                    WHERE trade_date >= :since
                    GROUP BY trade_date
                    HAVING COUNT(foreign_buy) = 0 OR COUNT(margin_balance) * 2 < COUNT(*)
                ) gaps
            """), {'since': since}).scalar()
        return pd.Timestamp(earliest).date() if earliest else None
    except Exception:
        return None


def clean_number(x):
    """清洗數字格式 (去除逗號, 處理 --)"""
    if pd.isna(x):
        return 0
    s = str(x).replace(',', '').replace('---', '0').replace('--', '0')
    try:
        return float(s)
    except Exception:
        return 0


def fetch_market_data_with_mcp(
    mcp_client: MCPClient,
    date_str: str,
) -> tuple[pd.DataFrame, list[str]]:
    """透過 MCP 取得市場快照與外資買賣超。"""
    correlation_id = (
        f"market-{date_str.replace('-', '')}-{int(time.time())}"
    )
    request_context = MCPRequestContext(
        market=Config.MCP_DEFAULT_MARKET,
        correlation_id=correlation_id,
        include_etfs=True,
    )
    jobs = [
        MCPFetchJob(
            name='snapshot',
            dataset='stock_basic_snapshot',
            request=StockBasicSnapshotRequest(
                trade_date=date_str,
                context=request_context,
            ),
        ),
        MCPFetchJob(
            name='flow',
            dataset='foreign_investor_flow',
            request=ForeignInvestorFlowRequest(
                trade_date=date_str,
                context=request_context,
            ),
        ),
    ]
    results = mcp_client.fetch_many_sync(
        jobs,
        return_exceptions=True,
        correlation_id=correlation_id,
    )

    warnings: list[str] = []
    snapshot_df = pd.DataFrame()
    flow_df = pd.DataFrame()

    snapshot_result = results.get('snapshot')
    if isinstance(snapshot_result, Exception):
        warnings.append(f"市場快照失敗: {snapshot_result}")
    elif isinstance(snapshot_result, dict):
        snapshot_df = MCPClient.stock_basic_snapshot_to_frame(
            snapshot_result
        )
    else:
        warnings.append('市場快照未回傳有效資料')

    flow_result = results.get('flow')
    if isinstance(flow_result, Exception):
        warnings.append(f"外資買賣超失敗: {flow_result}")
    elif isinstance(flow_result, dict):
        flow_df = MCPClient.foreign_investor_flow_to_frame(
            flow_result
        )
    else:
        warnings.append('外資買賣超未回傳有效資料')

    if not snapshot_df.empty and flow_df.empty:
        # 法人缺漏時仍寫入行情（欄位保留 NULL）。
        warnings.append(f'法人資料尚未取得 ({date_str})，先寫入行情')
    final_df = merge_market_data(snapshot_df, flow_df, date_str)
    return final_df, warnings


def merge_market_data(
    snapshot_df: pd.DataFrame,
    flow_df: pd.DataFrame,
    date_str: str,
) -> pd.DataFrame:
    """將 MCP 市場快照與外資資料合併為 daily_market_data 契約。"""
    if snapshot_df is None or snapshot_df.empty:
        return pd.DataFrame()

    merged_snapshot = snapshot_df.copy()
    merged_snapshot['stock_id'] = (
        merged_snapshot['stock_id'].astype(str).str.strip()
    )
    if 'trade_date' not in merged_snapshot.columns:
        return pd.DataFrame()
    source_dates = merged_snapshot['trade_date'].map(normalize_date_str)
    if source_dates.isna().any() or source_dates.ne(normalize_date_str(date_str)).any():
        raise ValueError(f'Market snapshot source date does not match requested date {date_str}')
    merged_snapshot['trade_date'] = source_dates
    merged_snapshot = merged_snapshot.drop_duplicates(
        subset=['stock_id', 'trade_date']
    )

    if flow_df is not None and not flow_df.empty:
        merged_flow = flow_df.copy()
        merged_flow['stock_id'] = (
            merged_flow['stock_id'].astype(str).str.strip()
        )
        if 'trade_date' not in merged_flow.columns:
            return pd.DataFrame()
        flow_dates = merged_flow['trade_date'].map(normalize_date_str)
        if flow_dates.isna().any() or flow_dates.ne(normalize_date_str(date_str)).any():
            raise ValueError(f'Market flow source date does not match requested date {date_str}')
        merged_flow['trade_date'] = flow_dates
        for column in ['foreign_buy', 'trust_buy', 'dealer_buy']:
            if column not in merged_flow.columns:
                merged_flow[column] = None
        merged_flow = merged_flow[
            [
                'stock_id',
                'trade_date',
                'foreign_buy',
                'trust_buy',
                'dealer_buy',
            ]
        ].drop_duplicates(subset=['stock_id', 'trade_date'])
        final_df = pd.merge(
            merged_snapshot,
            merged_flow,
            on=['stock_id', 'trade_date'],
            how='left',
        )
    else:
        final_df = merged_snapshot

    for column in ['foreign_buy', 'trust_buy', 'dealer_buy']:
        if column not in final_df.columns:
            final_df[column] = None

    return final_df


def enrich_with_margin_balance(
    df: pd.DataFrame,
    date_str: str,
) -> tuple[pd.DataFrame, str | None]:
    """保留融資融券 enrichment，但不覆蓋 MCP covered dataset。"""
    if df is None or df.empty:
        return pd.DataFrame(), None

    try:
        from core.crawlers.chip_data_scraper import fetch_margin_balance

        margin_df = fetch_margin_balance(date_str)
        if margin_df.empty:
            return df, '融資融券無資料，保留 MCP 主資料'

        enriched_df = df.copy()
        enriched_df['stock_id'] = enriched_df['stock_id'].astype(
            str).str.strip()
        margin_df['stock_id'] = margin_df['stock_id'].astype(str).str.strip()
        enriched_df = pd.merge(
            enriched_df,
            margin_df,
            on='stock_id',
            how='left')
        return enriched_df, None
    except Exception as exc:
        return df, f'融資融券合併失敗（不中斷）: {exc}'


MARKET_OPTIONAL_COLUMNS = ('foreign_buy', 'trust_buy', 'dealer_buy', 'margin_balance', 'short_balance')


def process_and_save(df, date_str, engine):
    """
    清洗股票行情資料並寫入資料庫

    將抓回的原始 DataFrame 進行數值清洗（去除逗號、處理無效值），
    然後透過 db_helper.upsert_stock_data 寫入 daily_market_data 表。

    Args:
        df: 原始行情 DataFrame（含 stock_id, open_price 等欄位）
        date_str: 交易日期字串 (YYYY-MM-DD)
        engine: SQLAlchemy 資料庫引擎

    Returns:
        int: 成功寫入的筆數
    """
    if df is None or df.empty:
        return 0

    df = clean_market_frame(df, date_str)

    from core.db_helper import get_table_columns, upsert_stock_data
    if engine is not None:
        missing_columns = set(MARKET_OPTIONAL_COLUMNS) - get_table_columns('daily_market_data', engine=engine)
        if missing_columns:
            raise RuntimeError(f'Market schema missing nullable columns: {sorted(missing_columns)}')
    return upsert_stock_data(df, 'daily_market_data')


def clean_market_frame(df, date_str):
    """Pure cleaning of one fetched market frame (no database access); process_and_save writes its result.
    services/market_data_update.py stages this output before any commit."""
    requested_date = normalize_date_str(date_str)
    if not requested_date or not _is_business_weekday(requested_date):
        raise ValueError(f'Market snapshot date is not a valid business weekday: {date_str}')

    cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio']
    for col in cols:
        if col in df.columns:
            df[col] = df[col].apply(clean_number)
        else:
            df[col] = 0

    optional_cols = list(MARKET_OPTIONAL_COLUMNS)
    for col in optional_cols:
        if col not in df.columns:
            df[col] = None
    unseparated = df[optional_cols].apply(lambda s: s.map(lambda v: v.replace(',', '') if isinstance(v, str) else v))  # T86: -21,603,811
    df[optional_cols] = unseparated.apply(pd.to_numeric, errors='coerce').astype(object).where(
        lambda values: values.notna(), None
    )

    if 'trade_date' not in df.columns:
        raise ValueError('Market data is missing its authoritative source trade_date')
    source_dates = df['trade_date'].map(normalize_date_str)
    if source_dates.isna().any() or source_dates.ne(requested_date).any():
        raise ValueError(f'Market data source date does not match requested date {date_str}')
    df['trade_date'] = source_dates
    df = df[df['close_price'] > 0].copy()
    if df.empty:
        raise ValueError('Market data contains no rows with a positive close price')

    # 過濾權證（保留個股 + ETF + 債券ETF，只排除權證等衍生品）
    # 個股: 4碼 1xxx-9xxx | ETF/債券: 00 開頭 (0050, 006208, 00679B, 00631L...)
    before_filter = len(df)
    df['stock_id'] = df['stock_id'].astype(str).str.strip()
    df = df[df['stock_id'].str.fullmatch(r'(?:[1-9]\d{3}|00\d{2,4}[A-Z]?)')].copy()
    if df.empty:
        raise ValueError('Market data contains no supported stock identifiers')
    filtered_out = before_filter - len(df)
    if filtered_out > 0:
        print(
            '  📊 過濾權證: '
            f'{before_filter} → {len(df)} 筆'
            f'（排除 {filtered_out} 筆權證/衍生品）'
        )
    return df

# ============================================
# 🚀 主程式 - V35 統一更新入口
# ============================================




def update_market_date(
    engine,
    date_str: str,
    mcp_client: MCPClient | None = None,
    min_row_count: int = MIN_VALID_MARKET_ROWS,
) -> int:
    """更新單一交易日市場資料，供每日更新與 backfill 共用。"""
    print(f"\n📅 正在處理: {date_str}")
    requested_date = normalize_date_str(date_str)
    if not requested_date or not _is_business_weekday(requested_date):
        raise ValueError(f'Market snapshot date is not a valid business weekday: {date_str}')
    client = mcp_client or MCPClient()

    final_df, warnings = fetch_market_data_with_mcp(client, date_str)
    if warnings:
        print(f"  ⚠️ MCP 部分成功: {'；'.join(warnings)}")

    if final_df.empty:
        if warnings:
            raise RuntimeError(f'Market data fetch failed for {requested_date}: {"; ".join(warnings)}')
        # ponytail: an empty successful response is treated as a holiday; confirm with an authoritative Taiwan calendar when available.
        print("  ⚠️ 缺少市場快照，略過當日寫入")
        return 0

    if len(final_df) < int(min_row_count):
        print(
            f"  ⚠️ MCP 回傳筆數過少 ({len(final_df)} < {int(min_row_count)})，"
            "略過當日市場資料更新"
        )
        raise RuntimeError(
            f'Market snapshot is incomplete for {requested_date}: '
            f'{len(final_df)} rows, expected at least {int(min_row_count)}'
        )

    final_df, margin_warning = enrich_with_margin_balance(final_df, date_str)
    if margin_warning:
        print(f"  ⚠️ {margin_warning}")

    return process_and_save(final_df, date_str, engine)


def prewarm_dashboard_aggregation_cache(
    trade_date: str | None = None,
    tracked_stock_ids: list[str] | None = None,
    mcp_client: MCPClient | None = None,
) -> dict:
    """預熱 dashboard MCP 聚合快取。"""
    resolved_trade_date = normalize_date_str(trade_date) or normalize_date_str(get_latest_trade_date())
    stock_ids = [str(stock_id).strip() for stock_id in (tracked_stock_ids or DEFAULT_DASHBOARD_PREWARM_STOCKS) if str(stock_id).strip()]
    summary = {
        'trade_date': resolved_trade_date,
        'market_hotspot_cached': False,
        'tracked_stock_ids': stock_ids,
        'stock_trend_cached': [],
        'investment_screening_cached': [],
    }
    if not resolved_trade_date:
        return summary

    client = mcp_client or MCPClient()

    try:
        hotspot_payload = client.get_market_hotspot_sync(resolved_trade_date)
        if hotspot_payload:
            summary['market_hotspot_cached'] = save_dashboard_aggregation_cache(
                'market_hotspot',
                hotspot_payload,
                market='ALL',
                requested_date=resolved_trade_date,
                ttl_seconds=300,
            )
    except Exception as exc:
        print(f"⚠️ 預熱 market_hotspot 失敗 ({resolved_trade_date}): {exc}")

    for stock_id in stock_ids:
        try:
            trend_payload = client.get_twse_stock_trend_sync(stock_id, trade_date=resolved_trade_date)
            if trend_payload:
                saved = save_dashboard_aggregation_cache(
                    'twse_stock_trend',
                    trend_payload,
                    stock_id=stock_id,
                    market='ALL',
                    requested_date=resolved_trade_date,
                    ttl_seconds=300,
                )
                summary['stock_trend_cached'].append({'stock_id': stock_id, 'cached': saved})
        except Exception as exc:
            print(f"⚠️ 預熱 twse_stock_trend 失敗 ({stock_id}, {resolved_trade_date}): {exc}")
            summary['stock_trend_cached'].append({'stock_id': stock_id, 'cached': False})

        try:
            screening_payload = client.get_investment_screening_sync(stock_id, trade_date=resolved_trade_date)
            if screening_payload:
                saved = save_dashboard_aggregation_cache(
                    'investment_screening',
                    screening_payload,
                    stock_id=stock_id,
                    market='ALL',
                    requested_date=resolved_trade_date,
                    ttl_seconds=300,
                )
                summary['investment_screening_cached'].append({'stock_id': stock_id, 'cached': saved})
        except Exception as exc:
            print(f"⚠️ 預熱 investment_screening 失敗 ({stock_id}, {resolved_trade_date}): {exc}")
            summary['investment_screening_cached'].append({'stock_id': stock_id, 'cached': False})

    return summary


def run_monthly_revenue_update():
    """
    步驟二：更新月營收資料

    呼叫 core/update_monthly_revenue.py 中的爬蟲邏輯，
    爬取 MOPS 靜態 HTML 以取得最新月營收與 YoY 資料，
    寫入 monthly_revenue 資料表。

    Returns:
        bool: 是否成功
    """
    print(f"\n{'='*60}")
    print("💰 步驟 2/3：更新月營收資料")
    print(f"{'='*60}")

    try:
        from core.update_monthly_revenue import (
            fetch_mops_static_revenue,
            process_and_save as save_revenue,
        )

        now = datetime.now()
        # 營收通常在次月 10 日後公布，抓取上個月的
        if now.day < 12:
            target_year = now.year if now.month > 1 else now.year - 1
            target_month = now.month - 1 if now.month > 1 else 12
        else:
            target_year = now.year
            target_month = now.month

        # 嘗試最新月份 + 上個月（確保補齊）
        months_to_try = []
        for offset in range(2):
            m = target_month - offset
            y = target_year
            if m <= 0:
                m += 12
                y -= 1
            months_to_try.append((y, m))

        success = False
        for y, m in months_to_try:
            print(f"\n📅 嘗試更新 {y}年{m}月 月營收...")
            res_df = fetch_mops_static_revenue(y, m)
            if res_df is not None and not res_df.empty:
                save_revenue(res_df)
                success = True
            time.sleep(random.uniform(2, 4))

        return success

    except Exception as e:
        print(f"⚠️ 月營收更新失敗（不中斷流程）: {e}")
        return False


def run_financial_update():
    """
    步驟三：更新最新一季財報

    呼叫 core/update_financials_mops.py 中的季報爬蟲邏輯，
    從 mopsov 備援站抓取最新一季的綜合損益表，
    寫入 financial_statements 資料表（含營業利益率計算）。

    Returns:
        bool: 是否成功
    """
    print(f"\n{'='*60}")
    print("📊 步驟 3/3：更新季度財報")
    print(f"{'='*60}")

    try:
        from core.update_financials_mops import update_quarter

        now = datetime.now()
        roc_year = now.year - 1911

        # 推算最新可取得的季度
        # Q1 (5月後可取得), Q2 (8月後), Q3 (11月後), Q4 (次年3月後)
        if now.month >= 11:
            target_year, target_quarter = roc_year, 3
        elif now.month >= 8:
            target_year, target_quarter = roc_year, 2
        elif now.month >= 5:
            target_year, target_quarter = roc_year, 1
        elif now.month >= 3:
            target_year, target_quarter = roc_year - 1, 4
        else:
            target_year, target_quarter = roc_year - 1, 3

        print(f"📅 目標季度: 民國 {target_year} Q{target_quarter}")
        result = update_quarter(target_year, target_quarter)
        return result

    except Exception as e:
        print(f"⚠️ 季度財報更新失敗（不中斷流程）: {e}")
        return False




def main() -> int:
    """Step 1 of the evening pipeline (design D4): the market data service is the single writer of the market tables. It catches up every
    missing TWSE session under the trusted ingestion grant; this job keeps the update_database pipeline_runs step and its exit code:
    0 only when prices are stored for the trading-calendar expected session, so --stop-on-error never runs picks or LINE on old data."""
    import os
    from services.market_data_update import DEFAULT_ROOT, MarketDataUpdateService
    pipeline_name, step_name, run_date = _resolve_pipeline_run_context('update_database')
    record_pipeline_step_start(pipeline_name=pipeline_name, step_name=step_name, run_date=run_date)
    service = MarketDataUpdateService(root=os.environ.get('STOCK_LINBOT_UPDATE_ROOT') or DEFAULT_ROOT)
    for _ in range(LOCK_WAIT_ATTEMPTS):  # a manual update holding the lock is waited for, as before
        record = service.run('daily')
        if record['status'] != 'UPDATE_IN_PROGRESS':
            break
        time.sleep(LOCK_WAIT_SECONDS)
    expected = service.expected(datetime.now().date())[0]
    latest = normalize_date_str(get_latest_trade_date())
    current = latest == expected
    if current:
        print(f"Dashboard aggregation prewarm summary: {prewarm_dashboard_aggregation_cache(latest)}")
    problems = [f"{r['dataset']}|{r['date']}: {r['status']}" for r in record.get('results', []) if r['status'] not in ('SUCCESS', 'PIT_UNVERIFIED', 'SKIPPED_ALREADY_SUCCESS', 'CLOSED_OBSERVED')]
    summary = f"service {record['status']} {record.get('reason') or ''}; latest {latest}, expected {expected}; " + '; '.join(problems)
    record_pipeline_step_finish(pipeline_name=pipeline_name, step_name=step_name, run_date=run_date, status='success' if current else 'failed',
                                source_date=latest, trade_date=latest, error_summary=None if current else summary[:1000])
    print(summary, flush=True)
    return 0 if current else 1


LOCK_WAIT_SECONDS = 30
LOCK_WAIT_ATTEMPTS = 240  # ponytail: 2 hours, the stale-lock age; a held lock past that is reported, not broken; revisit when the lock policy changes.




if __name__ == "__main__":
    raise SystemExit(main())
