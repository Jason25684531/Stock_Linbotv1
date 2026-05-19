"""
資料庫與設定管理統一模組
============================================
功能：
1. 資料庫連線管理
2. 設定值讀寫（防 SQL Injection）
3. 資料查詢輔助函數
"""
import json
from datetime import datetime, timedelta
import pandas as pd
import re
import time
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from config import Config


# 🔥 Singleton 引擎 + 連線池（避免 Too many connections）
_engine_instance = None
_table_columns_cache: dict[str, set[str]] = {}

# 允許的資料表名稱（防止 SQL Injection）
_ALLOWED_TABLES = {
    'daily_market_data', 'user_settings', 'user_simulation_trades',
    'monthly_revenue', 'financial_statements', 'daily_recommendations',
    'backtest_trades', 'backtest_equity_curve', 'daily_news_sentiment',
    'dashboard_aggregation_cache', 'pipeline_runs',
}

MIN_VALID_MARKET_ROWS = 100
RECOMMENDATION_HEARTBEAT_STOCK_ID = 'NONE'
COMMON_STOCK_ID_PATTERN = re.compile(r'^\d{4}$')
COMMON_STOCK_EXCLUDED_PREFIXES = ('03', '08')
DASHBOARD_AGGREGATION_CACHE_TABLE = 'dashboard_aggregation_cache'
DASHBOARD_AGGREGATION_CACHE_VERSION = 'v1'
PIPELINE_RUNS_TABLE = 'pipeline_runs'


def _utc_now_naive() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _parse_datetime_value(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value[:19].replace('T', ' '))
    except ValueError:
        return None


def ensure_pipeline_run_state_schema(engine=None):
    """Ensure minimal persisted run-state storage for scheduled pipeline steps."""
    if engine is None:
        engine = get_db_engine()

    dialect_name = getattr(engine.dialect, 'name', '')
    if dialect_name == 'sqlite':
        create_sql = f"""
            CREATE TABLE IF NOT EXISTS {PIPELINE_RUNS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_name TEXT NOT NULL,
                step_name TEXT NOT NULL,
                run_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                source_date TEXT,
                trade_date TEXT,
                rows_inserted INTEGER,
                rows_updated INTEGER,
                error_summary TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (pipeline_name, step_name, run_date)
            )
        """
        index_sql = [
            f"CREATE INDEX IF NOT EXISTS idx_pipeline_runs_run_date ON {PIPELINE_RUNS_TABLE} (run_date)",
            f"CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline_step ON {PIPELINE_RUNS_TABLE} (pipeline_name, step_name)",
        ]
    else:
        create_sql = f"""
            CREATE TABLE IF NOT EXISTS {PIPELINE_RUNS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                pipeline_name VARCHAR(64) NOT NULL,
                step_name VARCHAR(128) NOT NULL,
                run_date DATE NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'running',
                source_date DATE NULL,
                trade_date DATE NULL,
                rows_inserted INT NULL,
                rows_updated INT NULL,
                error_summary VARCHAR(1000) NULL,
                started_at DATETIME NULL,
                finished_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE KEY uq_pipeline_runs_step (pipeline_name, step_name, run_date),
                KEY idx_pipeline_runs_run_date (run_date),
                KEY idx_pipeline_runs_pipeline_step (pipeline_name, step_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        index_sql = []

    try:
        with engine.connect() as conn:
            conn.execute(text(create_sql))
            for sql in index_sql:
                conn.execute(text(sql))
            conn.commit()
    except Exception as e:
        print(f"⚠️ pipeline run-state schema ensure failed: {e}")


def _normalize_pipeline_status(status: str | None) -> str:
    normalized = str(status or '').strip().lower()
    return normalized or 'running'


def _truncate_error_summary(error_summary, max_length: int = 1000) -> str | None:
    if error_summary is None:
        return None
    normalized = str(error_summary).strip()
    if not normalized:
        return None
    return normalized[:max_length]


def record_pipeline_step_start(
    *,
    pipeline_name: str,
    step_name: str,
    run_date: str | None = None,
    engine=None,
) -> bool:
    """Persist start state for a pipeline step."""
    if engine is None:
        engine = get_db_engine()
    ensure_pipeline_run_state_schema(engine)

    normalized_pipeline_name = str(pipeline_name or '').strip() or 'manual'
    normalized_step_name = str(step_name or '').strip()
    normalized_run_date = normalize_date_str(run_date) or normalize_date_str(_utc_now_naive())
    if not normalized_step_name or not normalized_run_date:
        return False

    now = _utc_now_naive()
    params = {
        'pipeline_name': normalized_pipeline_name,
        'step_name': normalized_step_name,
        'run_date': normalized_run_date,
        'status': 'running',
        'started_at': now,
        'finished_at': None,
        'rows_inserted': None,
        'rows_updated': None,
        'error_summary': None,
        'updated_at': now,
        'created_at': now,
    }

    try:
        with engine.connect() as conn:
            existing = conn.execute(
                text(
                    f"""
                    SELECT id, started_at, created_at
                    FROM {PIPELINE_RUNS_TABLE}
                    WHERE pipeline_name = :pipeline_name
                      AND step_name = :step_name
                      AND run_date = :run_date
                    """
                ),
                {
                    'pipeline_name': normalized_pipeline_name,
                    'step_name': normalized_step_name,
                    'run_date': normalized_run_date,
                },
            ).mappings().fetchone()
            if existing:
                params['started_at'] = _parse_datetime_value(existing.get('started_at')) or now
                params['created_at'] = _parse_datetime_value(existing.get('created_at')) or now
                conn.execute(
                    text(
                        f"""
                        UPDATE {PIPELINE_RUNS_TABLE}
                        SET status = :status,
                            started_at = :started_at,
                            finished_at = :finished_at,
                            rows_inserted = :rows_inserted,
                            rows_updated = :rows_updated,
                            error_summary = :error_summary,
                            updated_at = :updated_at
                        WHERE pipeline_name = :pipeline_name
                          AND step_name = :step_name
                          AND run_date = :run_date
                        """
                    ),
                    params,
                )
            else:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {PIPELINE_RUNS_TABLE}
                            (pipeline_name, step_name, run_date, status, source_date, trade_date,
                             rows_inserted, rows_updated, error_summary, started_at, finished_at,
                             created_at, updated_at)
                        VALUES
                            (:pipeline_name, :step_name, :run_date, :status, NULL, NULL,
                             :rows_inserted, :rows_updated, :error_summary, :started_at, :finished_at,
                             :created_at, :updated_at)
                        """
                    ),
                    params,
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ pipeline step start record failed ({normalized_pipeline_name}/{normalized_step_name}): {e}")
        return False


def record_pipeline_step_finish(
    *,
    pipeline_name: str,
    step_name: str,
    run_date: str | None = None,
    status: str,
    source_date: str | None = None,
    trade_date: str | None = None,
    rows_inserted: int | None = None,
    rows_updated: int | None = None,
    error_summary: str | None = None,
    engine=None,
) -> bool:
    """Persist finish state for a pipeline step."""
    if engine is None:
        engine = get_db_engine()
    ensure_pipeline_run_state_schema(engine)

    normalized_pipeline_name = str(pipeline_name or '').strip() or 'manual'
    normalized_step_name = str(step_name or '').strip()
    normalized_run_date = normalize_date_str(run_date) or normalize_date_str(_utc_now_naive())
    normalized_status = _normalize_pipeline_status(status)
    normalized_source_date = normalize_date_str(source_date)
    normalized_trade_date = normalize_date_str(trade_date)
    normalized_error_summary = _truncate_error_summary(error_summary)
    if not normalized_step_name or not normalized_run_date:
        return False

    now = _utc_now_naive()
    params = {
        'pipeline_name': normalized_pipeline_name,
        'step_name': normalized_step_name,
        'run_date': normalized_run_date,
        'status': normalized_status,
        'source_date': normalized_source_date,
        'trade_date': normalized_trade_date,
        'rows_inserted': int(rows_inserted) if rows_inserted is not None else None,
        'rows_updated': int(rows_updated) if rows_updated is not None else None,
        'error_summary': normalized_error_summary,
        'started_at': now,
        'finished_at': now,
        'created_at': now,
        'updated_at': now,
    }

    try:
        with engine.connect() as conn:
            existing = conn.execute(
                text(
                    f"""
                    SELECT id, started_at, created_at
                    FROM {PIPELINE_RUNS_TABLE}
                    WHERE pipeline_name = :pipeline_name
                      AND step_name = :step_name
                      AND run_date = :run_date
                    """
                ),
                {
                    'pipeline_name': normalized_pipeline_name,
                    'step_name': normalized_step_name,
                    'run_date': normalized_run_date,
                },
            ).mappings().fetchone()
            if existing:
                params['started_at'] = _parse_datetime_value(existing.get('started_at')) or now
                params['created_at'] = _parse_datetime_value(existing.get('created_at')) or now
                conn.execute(
                    text(
                        f"""
                        UPDATE {PIPELINE_RUNS_TABLE}
                        SET status = :status,
                            source_date = :source_date,
                            trade_date = :trade_date,
                            rows_inserted = :rows_inserted,
                            rows_updated = :rows_updated,
                            error_summary = :error_summary,
                            started_at = :started_at,
                            finished_at = :finished_at,
                            updated_at = :updated_at
                        WHERE pipeline_name = :pipeline_name
                          AND step_name = :step_name
                          AND run_date = :run_date
                        """
                    ),
                    params,
                )
            else:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {PIPELINE_RUNS_TABLE}
                            (pipeline_name, step_name, run_date, status, source_date, trade_date,
                             rows_inserted, rows_updated, error_summary, started_at, finished_at,
                             created_at, updated_at)
                        VALUES
                            (:pipeline_name, :step_name, :run_date, :status, :source_date, :trade_date,
                             :rows_inserted, :rows_updated, :error_summary, :started_at, :finished_at,
                             :created_at, :updated_at)
                        """
                    ),
                    params,
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ pipeline step finish record failed ({normalized_pipeline_name}/{normalized_step_name}): {e}")
        return False


def get_pipeline_step_record(
    *,
    pipeline_name: str,
    step_name: str,
    run_date: str | None = None,
    engine=None,
) -> dict | None:
    """Load persisted state for a specific pipeline step."""
    if engine is None:
        engine = get_db_engine()
    ensure_pipeline_run_state_schema(engine)

    normalized_pipeline_name = str(pipeline_name or '').strip() or 'manual'
    normalized_step_name = str(step_name or '').strip()
    normalized_run_date = normalize_date_str(run_date)
    if not normalized_step_name or not normalized_run_date:
        return None

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f"""
                    SELECT *
                    FROM {PIPELINE_RUNS_TABLE}
                    WHERE pipeline_name = :pipeline_name
                      AND step_name = :step_name
                      AND run_date = :run_date
                    """
                ),
                {
                    'pipeline_name': normalized_pipeline_name,
                    'step_name': normalized_step_name,
                    'run_date': normalized_run_date,
                },
            ).mappings().fetchone()
        if not row:
            return None

        return {
            'pipeline_name': row.get('pipeline_name'),
            'step_name': row.get('step_name'),
            'run_date': normalize_date_str(row.get('run_date')),
            'status': row.get('status'),
            'source_date': normalize_date_str(row.get('source_date')),
            'trade_date': normalize_date_str(row.get('trade_date')),
            'rows_inserted': int(row.get('rows_inserted')) if row.get('rows_inserted') is not None else None,
            'rows_updated': int(row.get('rows_updated')) if row.get('rows_updated') is not None else None,
            'error_summary': row.get('error_summary'),
            'started_at': _parse_datetime_value(row.get('started_at')),
            'finished_at': _parse_datetime_value(row.get('finished_at')),
            'created_at': _parse_datetime_value(row.get('created_at')),
            'updated_at': _parse_datetime_value(row.get('updated_at')),
        }
    except Exception as e:
        print(f"⚠️ pipeline step lookup failed ({normalized_pipeline_name}/{normalized_step_name}): {e}")
        return None


def did_pipeline_step_run_on_date(
    *,
    pipeline_name: str,
    step_name: str,
    run_date: str | None = None,
    engine=None,
) -> bool:
    """Answer whether a specific pipeline step recorded observable state on a date."""
    record = get_pipeline_step_record(
        pipeline_name=pipeline_name,
        step_name=step_name,
        run_date=run_date,
        engine=engine,
    )
    return bool(record and str(record.get('status') or '').strip())


def build_dashboard_aggregation_cache_key(
    intent_name: str,
    *,
    stock_id: str | None = None,
    market: str = 'ALL',
    requested_date: str | None = None,
) -> str:
    normalized_intent = str(intent_name or '').strip().lower()
    if not normalized_intent:
        raise ValueError('intent_name is required')
    normalized_stock_id = normalize_stock_id_value(stock_id) or '*'
    normalized_market = str(market or 'ALL').strip().upper() or 'ALL'
    normalized_requested_date = normalize_date_str(requested_date) or '*'
    return f'{normalized_intent}:{normalized_market}:{normalized_requested_date}:{normalized_stock_id}'


def ensure_dashboard_aggregation_cache_schema(engine=None):
    """確保 dashboard 聚合快取資料表存在。"""
    if engine is None:
        engine = get_db_engine()

    dialect_name = getattr(engine.dialect, 'name', '')
    if dialect_name == 'sqlite':
        create_sql = f"""
            CREATE TABLE IF NOT EXISTS {DASHBOARD_AGGREGATION_CACHE_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT NOT NULL UNIQUE,
                intent_name TEXT NOT NULL,
                stock_id TEXT,
                market TEXT NOT NULL DEFAULT 'ALL',
                requested_date TEXT,
                as_of_date TEXT,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                fallback_used INTEGER NOT NULL DEFAULT 0,
                cache_status TEXT NOT NULL DEFAULT 'fresh',
                payload_version TEXT NOT NULL DEFAULT 'v1',
                fetched_at TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """
        index_sql = [
            f"CREATE INDEX IF NOT EXISTS idx_dashboard_cache_intent_date ON {DASHBOARD_AGGREGATION_CACHE_TABLE} (intent_name, requested_date)",
            f"CREATE INDEX IF NOT EXISTS idx_dashboard_cache_stock_date ON {DASHBOARD_AGGREGATION_CACHE_TABLE} (stock_id, requested_date)",
        ]
    else:
        create_sql = f"""
            CREATE TABLE IF NOT EXISTS {DASHBOARD_AGGREGATION_CACHE_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cache_key VARCHAR(255) NOT NULL,
                intent_name VARCHAR(64) NOT NULL,
                stock_id VARCHAR(16) NULL,
                market VARCHAR(16) NOT NULL DEFAULT 'ALL',
                requested_date DATE NULL,
                as_of_date DATE NULL,
                payload_json LONGTEXT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ok',
                fallback_used TINYINT(1) NOT NULL DEFAULT 0,
                cache_status VARCHAR(16) NOT NULL DEFAULT 'fresh',
                payload_version VARCHAR(32) NOT NULL DEFAULT 'v1',
                fetched_at DATETIME NOT NULL,
                expires_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE KEY uq_dashboard_cache_key (cache_key),
                KEY idx_dashboard_cache_intent_date (intent_name, requested_date),
                KEY idx_dashboard_cache_stock_date (stock_id, requested_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        index_sql = []

    try:
        with engine.connect() as conn:
            conn.execute(text(create_sql))
            for sql in index_sql:
                conn.execute(text(sql))
            conn.commit()
    except Exception as e:
        print(f"⚠️ dashboard aggregation cache schema 確認失敗: {e}")


def save_dashboard_aggregation_cache(
    intent_name: str,
    payload: dict,
    *,
    stock_id: str | None = None,
    market: str = 'ALL',
    requested_date: str | None = None,
    ttl_seconds: int = 300,
    cache_status: str = 'fresh',
    payload_version: str = DASHBOARD_AGGREGATION_CACHE_VERSION,
    engine=None,
) -> bool:
    """寫入 dashboard 聚合快取。"""
    if not isinstance(payload, dict) or not payload:
        return False
    if engine is None:
        engine = get_db_engine()
    ensure_dashboard_aggregation_cache_schema(engine)

    now = _utc_now_naive()
    ttl_value = int(ttl_seconds)
    expires_at = now - timedelta(seconds=1) if ttl_value <= 0 else now + timedelta(seconds=ttl_value)
    normalized_requested_date = normalize_date_str(requested_date or payload.get('requested_date'))
    normalized_as_of_date = normalize_date_str(payload.get('as_of_date'))
    normalized_market = str(market or payload.get('market') or 'ALL').strip().upper() or 'ALL'
    normalized_stock_id = normalize_stock_id_value(stock_id or payload.get('stock_id')) or None
    normalized_intent = str(intent_name or '').strip().lower()
    cache_key = build_dashboard_aggregation_cache_key(
        normalized_intent,
        stock_id=normalized_stock_id,
        market=normalized_market,
        requested_date=normalized_requested_date,
    )
    payload_json = json.dumps(payload, ensure_ascii=False)
    params = {
        'cache_key': cache_key,
        'intent_name': normalized_intent,
        'stock_id': normalized_stock_id,
        'market': normalized_market,
        'requested_date': normalized_requested_date,
        'as_of_date': normalized_as_of_date,
        'payload_json': payload_json,
        'status': str(payload.get('status') or 'ok'),
        'fallback_used': 1 if bool(payload.get('fallback_used')) else 0,
        'cache_status': str(cache_status or 'fresh'),
        'payload_version': str(payload_version or DASHBOARD_AGGREGATION_CACHE_VERSION),
        'fetched_at': now,
        'expires_at': expires_at,
        'created_at': now,
        'updated_at': now,
    }

    try:
        with engine.connect() as conn:
            existing_id = conn.execute(
                text(f"SELECT id FROM {DASHBOARD_AGGREGATION_CACHE_TABLE} WHERE cache_key = :cache_key"),
                {'cache_key': cache_key},
            ).scalar()
            if existing_id:
                conn.execute(
                    text(f"""
                        UPDATE {DASHBOARD_AGGREGATION_CACHE_TABLE}
                        SET intent_name = :intent_name,
                            stock_id = :stock_id,
                            market = :market,
                            requested_date = :requested_date,
                            as_of_date = :as_of_date,
                            payload_json = :payload_json,
                            status = :status,
                            fallback_used = :fallback_used,
                            cache_status = :cache_status,
                            payload_version = :payload_version,
                            fetched_at = :fetched_at,
                            expires_at = :expires_at,
                            updated_at = :updated_at
                        WHERE cache_key = :cache_key
                    """),
                    params,
                )
            else:
                conn.execute(
                    text(f"""
                        INSERT INTO {DASHBOARD_AGGREGATION_CACHE_TABLE}
                            (cache_key, intent_name, stock_id, market, requested_date,
                             as_of_date, payload_json, status, fallback_used, cache_status,
                             payload_version, fetched_at, expires_at, created_at, updated_at)
                        VALUES
                            (:cache_key, :intent_name, :stock_id, :market, :requested_date,
                             :as_of_date, :payload_json, :status, :fallback_used, :cache_status,
                             :payload_version, :fetched_at, :expires_at, :created_at, :updated_at)
                    """),
                    params,
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ dashboard aggregation cache 寫入失敗 ({cache_key}): {e}")
        return False


def get_dashboard_aggregation_cache(
    intent_name: str,
    *,
    stock_id: str | None = None,
    market: str = 'ALL',
    requested_date: str | None = None,
    allow_stale: bool = True,
    engine=None,
) -> dict | None:
    """讀取 dashboard 聚合快取並回傳 freshness metadata。"""
    if engine is None:
        engine = get_db_engine()
    ensure_dashboard_aggregation_cache_schema(engine)

    normalized_intent = str(intent_name or '').strip().lower()
    cache_key = build_dashboard_aggregation_cache_key(
        normalized_intent,
        stock_id=stock_id,
        market=market,
        requested_date=requested_date,
    )

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {DASHBOARD_AGGREGATION_CACHE_TABLE} WHERE cache_key = :cache_key"),
                {'cache_key': cache_key},
            ).mappings().fetchone()
        if not row:
            return None

        fetched_at = _parse_datetime_value(row.get('fetched_at'))
        expires_at = _parse_datetime_value(row.get('expires_at'))
        now = _utc_now_naive()
        computed_cache_status = 'stale' if expires_at and expires_at <= now else 'fresh'
        if computed_cache_status == 'stale' and not allow_stale:
            return None

        try:
            payload = json.loads(row.get('payload_json') or '{}')
        except Exception:
            payload = {}

        return {
            'cache_key': row.get('cache_key'),
            'intent_name': row.get('intent_name'),
            'stock_id': row.get('stock_id'),
            'market': row.get('market'),
            'requested_date': normalize_date_str(row.get('requested_date')),
            'as_of_date': normalize_date_str(row.get('as_of_date')),
            'status': row.get('status') or payload.get('status') or 'ok',
            'fallback_used': bool(row.get('fallback_used')),
            'cache_status': computed_cache_status,
            'payload_version': row.get('payload_version') or DASHBOARD_AGGREGATION_CACHE_VERSION,
            'fetched_at': normalize_date_str(fetched_at) if fetched_at else None,
            'expires_at': normalize_date_str(expires_at) if expires_at else None,
            'payload': payload,
        }
    except Exception as e:
        print(f"⚠️ dashboard aggregation cache 讀取失敗 ({cache_key}): {e}")
        return None


def resolve_dashboard_aggregation_cache(
    intent_name: str,
    *,
    refresh_fn,
    stock_id: str | None = None,
    market: str = 'ALL',
    requested_date: str | None = None,
    ttl_seconds: int = 300,
    allow_stale: bool = True,
    payload_version: str = DASHBOARD_AGGREGATION_CACHE_VERSION,
    engine=None,
) -> dict | None:
    """優先使用 fresh cache，必要時 refresh，失敗時可回退 stale cache。"""
    if engine is None:
        engine = get_db_engine()

    fresh_cache = get_dashboard_aggregation_cache(
        intent_name,
        stock_id=stock_id,
        market=market,
        requested_date=requested_date,
        allow_stale=False,
        engine=engine,
    )
    if fresh_cache:
        payload = dict(fresh_cache['payload'])
        payload['cache_status'] = 'fresh'
        payload['payload_version'] = fresh_cache['payload_version']
        return payload

    try:
        fresh_payload = refresh_fn()
        if fresh_payload:
            save_dashboard_aggregation_cache(
                intent_name,
                fresh_payload,
                stock_id=stock_id,
                market=market,
                requested_date=requested_date,
                ttl_seconds=ttl_seconds,
                cache_status='fresh',
                payload_version=payload_version,
                engine=engine,
            )
            resolved = dict(fresh_payload)
            resolved['cache_status'] = 'fresh'
            resolved['payload_version'] = payload_version
            return resolved
    except Exception as e:
        stale_cache = get_dashboard_aggregation_cache(
            intent_name,
            stock_id=stock_id,
            market=market,
            requested_date=requested_date,
            allow_stale=allow_stale,
            engine=engine,
        )
        if stale_cache:
            payload = dict(stale_cache['payload'])
            warnings = payload.get('warnings') or []
            warnings = [str(item) for item in warnings]
            warnings.append(f'cache refresh failed: {e}')
            payload['warnings'] = warnings
            payload['cache_status'] = 'stale'
            payload['payload_version'] = stale_cache['payload_version']
            payload['fallback_used'] = True
            return payload
        return None

    return None


def normalize_stock_id_value(stock_id) -> str:
    """將 stock_id 正規化為可比對字串。"""
    normalized = str(stock_id or '').strip()
    if normalized.endswith('.0'):
        normalized = normalized[:-2]
    return normalized


def get_table_columns(table_name: str, engine=None, refresh: bool = False) -> set[str]:
    """取得資料表欄位集合，並做簡單快取。"""
    normalized_table_name = str(table_name or '').strip()
    if not normalized_table_name:
        return set()

    cached = _table_columns_cache.get(normalized_table_name)
    if cached is not None and not refresh:
        return set(cached)

    if engine is None:
        engine = get_db_engine()

    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SHOW COLUMNS FROM {normalized_table_name}"))
            columns = {str(row[0]).strip() for row in result if row and row[0]}
            _table_columns_cache[normalized_table_name] = set(columns)
            return columns
    except Exception as e:
        print(f"⚠️ 讀取資料表欄位失敗 ({normalized_table_name}): {e}")
        return set(cached or set())


def is_common_stock_id(stock_id) -> bool:
    """判斷是否為一般上市櫃股票代號。"""
    normalized = normalize_stock_id_value(stock_id)
    return bool(COMMON_STOCK_ID_PATTERN.fullmatch(normalized)) and not normalized.startswith(COMMON_STOCK_EXCLUDED_PREFIXES)


def filter_common_stock_universe(df: pd.DataFrame) -> pd.DataFrame:
    """過濾出一般上市櫃股票，排除權證、ETF 與其他非普通股代號。"""
    if df is None:
        return pd.DataFrame()
    if df.empty or 'stock_id' not in df.columns:
        return df

    normalized_stock_ids = df['stock_id'].map(normalize_stock_id_value)
    mask = normalized_stock_ids.map(is_common_stock_id)
    filtered = df.loc[mask].copy()
    filtered['stock_id'] = normalized_stock_ids.loc[mask]
    return filtered


def get_db_engine(max_retries: int = 3):
    """獲取資料庫引擎（Singleton + 連線池 + 重試）
    
    使用全域唯一引擎，透過 SQLAlchemy 連線池管理連線數量，
    避免每次呼叫都建立新引擎導致 MySQL 1040 Too many connections。
    
    Args:
        max_retries: 連線失敗時的重試次數 (預設 3)
    """
    global _engine_instance
    if _engine_instance is None:
        last_err = None
        for attempt in range(max_retries):
            try:
                _engine_instance = create_engine(
                    Config.SQLALCHEMY_DATABASE_URI,
                    pool_size=5,          # 常駐連線數
                    max_overflow=10,      # 超額連線上限
                    pool_recycle=1800,    # 連線回收（30分鐘）
                    pool_pre_ping=True,   # 自動偵測斷線
                )
                # 驗證連線可用
                with _engine_instance.connect() as conn:
                    conn.execute(text("SELECT 1"))
                break
            except Exception as e:
                last_err = e
                _engine_instance = None
                if attempt < max_retries - 1:
                    wait = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
                    print(f"⚠️ DB 連線失敗 (attempt {attempt+1}/{max_retries}): {e}，{wait}秒後重試...")
                    time.sleep(wait)
        if _engine_instance is None:
            raise ConnectionError(f"❌ 無法連線資料庫（已重試 {max_retries} 次）: {last_err}")
    return _engine_instance


def get_setting(key, default_value=None):
    """
    從資料庫讀取設定值（防 SQL Injection）
    
    Args:
        key: 設定鍵值
        default_value: 預設值
    
    Returns:
        設定值字串
    """
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT setting_value FROM user_settings WHERE setting_key = :key"),
                {'key': key}
            ).scalar()
        return result if result is not None else default_value
    except Exception as e:
        print(f"⚠️ 讀取設定失敗 ({key}): {e}")
        return default_value


def update_setting(key, value):
    """
    更新資料庫設定值（防 SQL Injection）
    
    Args:
        key: 設定鍵值
        value: 新設定值
    
    Returns:
        是否成功
    """
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO user_settings (setting_key, setting_value) 
                    VALUES (:key, :value)
                    ON DUPLICATE KEY UPDATE 
                        setting_value = :value,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {'key': key, 'value': value}
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ 更新設定失敗 ({key}={value}): {e}")
        return False


def get_latest_trade_date():
    """
    獲取資料庫中的最新交易日期
    
    Returns:
        datetime.date or None
    """
    try:
        valid_dates = get_valid_market_dates()
        if valid_dates:
            return valid_dates[-1]
    except Exception as e:
        print(f"⚠️ 獲取最新日期失敗: {e}")
    return None


def normalize_date_str(date_value) -> str | None:
    """統一將日期值轉為 YYYY-MM-DD 字串。"""
    if date_value is None:
        return None
    if hasattr(date_value, 'strftime'):
        return date_value.strftime('%Y-%m-%d')
    text_value = str(date_value).strip()
    if not text_value:
        return None
    return text_value[:10]


def _is_business_weekday(date_value) -> bool:
    """判斷日期是否為週一到週五。"""
    normalized = normalize_date_str(date_value)
    if not normalized:
        return False
    try:
        return pd.Timestamp(normalized).weekday() < 5
    except Exception:
        return False


def is_recommendation_heartbeat(stock_id) -> bool:
    """判斷推薦資料列是否為零候選心跳。"""
    return str(stock_id or '').strip().upper() == RECOMMENDATION_HEARTBEAT_STOCK_ID


def get_valid_market_dates(
    start_date: str | None = None,
    end_date: str | None = None,
    min_row_count: int = MIN_VALID_MARKET_ROWS,
) -> list[str]:
    """取得有效市場交易日清單。"""
    engine = get_db_engine()
    filters = []
    params = {
        'min_rows': int(min_row_count),
        'market_symbol': Config.MARKET_SYMBOL,
    }

    if start_date:
        params['start_date'] = normalize_date_str(start_date)
        filters.append('trade_date >= :start_date')
    if end_date:
        params['end_date'] = normalize_date_str(end_date)
        filters.append('trade_date <= :end_date')

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ''
    sql = f"""
        SELECT trade_date,
               COUNT(*) AS row_count,
               SUM(CASE WHEN stock_id = :market_symbol THEN 1 ELSE 0 END) AS market_symbol_rows
        FROM daily_market_data
        {where_clause}
        GROUP BY trade_date
        HAVING COUNT(*) > :min_rows
        ORDER BY trade_date
    """
    dates_df = pd.read_sql(text(sql), engine, params=params)
    if dates_df.empty:
        return []

    valid_dates: list[str] = []
    for _, row in dates_df.iterrows():
        normalized = normalize_date_str(row.get('trade_date'))
        if not normalized or not _is_business_weekday(normalized):
            continue

        market_symbol_rows = int(row.get('market_symbol_rows', 0) or 0)
        if market_symbol_rows <= 0:
            continue

        valid_dates.append(normalized)

    return valid_dates


def get_recommendation_dates(
    start_date: str | None = None,
    end_date: str | None = None,
    include_heartbeats: bool = True,
) -> list[str]:
    """取得推薦資料中存在的日期清單。"""
    engine = get_db_engine()
    filters = []
    params = {}

    if start_date:
        params['start_date'] = normalize_date_str(start_date)
        filters.append('trade_date >= :start_date')
    if end_date:
        params['end_date'] = normalize_date_str(end_date)
        filters.append('trade_date <= :end_date')
    if not include_heartbeats:
        params['heartbeat_stock_id'] = RECOMMENDATION_HEARTBEAT_STOCK_ID
        filters.append('stock_id <> :heartbeat_stock_id')

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ''
    sql = f"""
        SELECT DISTINCT trade_date
        FROM daily_recommendations
        {where_clause}
        ORDER BY trade_date
    """
    dates_df = pd.read_sql(text(sql), engine, params=params)
    if dates_df.empty:
        return []

    return [
        normalized
        for value in dates_df['trade_date'].tolist()
        for normalized in [normalize_date_str(value)]
        if normalized and _is_business_weekday(normalized)
    ]


def get_completed_recommendation_strategy_days(
    start_date: str | None = None,
    end_date: str | None = None,
    strategies: list[str] | None = None,
) -> set[tuple[str, str]]:
    """取得已完成落庫的推薦日期/策略組合（含 heartbeat）。"""
    engine = get_db_engine()
    filters = []
    params: dict[str, object] = {}

    if start_date:
        params['start_date'] = normalize_date_str(start_date)
        filters.append('trade_date >= :start_date')
    if end_date:
        params['end_date'] = normalize_date_str(end_date)
        filters.append('trade_date <= :end_date')
    if strategies:
        strategy_filters = []
        for index, strategy_name in enumerate(strategies):
            param_name = f'strategy_{index}'
            params[param_name] = strategy_name
            strategy_filters.append(f'strategy = :{param_name}')
        if strategy_filters:
            filters.append(f"({' OR '.join(strategy_filters)})")

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ''
    sql = f"""
        SELECT DISTINCT trade_date, strategy
        FROM daily_recommendations
        {where_clause}
        ORDER BY trade_date, strategy
    """
    strategy_df = pd.read_sql(text(sql), engine, params=params)
    if strategy_df.empty:
        return set()

    completed: set[tuple[str, str]] = set()
    for _, row in strategy_df.iterrows():
        date_str = normalize_date_str(row.get('trade_date'))
        strategy_name = str(row.get('strategy') or '').strip()
        if not date_str or not strategy_name:
            continue
        completed.add((date_str, strategy_name))
    return completed


def get_actual_latest_date(min_row_count: int = MIN_VALID_MARKET_ROWS):
    """取得市場資料與推薦資料交集中的最新有效日期。"""
    try:
        market_dates = set(get_valid_market_dates(min_row_count=min_row_count))
        if not market_dates:
            return None

        recommendation_dates = set(get_recommendation_dates(include_heartbeats=True))
        shared_dates = sorted(market_dates & recommendation_dates)
        if shared_dates:
            return shared_dates[-1]
    except Exception as e:
        print(f"⚠️ 獲取實際最新日期失敗: {e}")
    return None


def get_stock_data(stock_id=None, date_str=None):
    """
    從資料庫撈取股票資料（安全版 - 參數化查詢）
    
    Args:
        stock_id: 股票代號（None 代表全市場）
        date_str: 日期字串（None 代表最新）
    
    Returns:
        (DataFrame, 日期字串)
    """
    engine = get_db_engine()
    
    try:
        # 如果沒指定日期，抓最新的一天
        if not date_str:
            latest = get_latest_trade_date()
            if not latest:
                return pd.DataFrame(), None
            date_str = normalize_date_str(latest)

        # 參數化查詢（防 SQL Injection）
        if stock_id:
            sql = text("""
                SELECT * FROM daily_market_data 
                WHERE trade_date = :date AND stock_id = :sid
            """)
            df = pd.read_sql(sql, engine, params={'date': date_str, 'sid': stock_id})
        else:
            sql = text("""
                SELECT * FROM daily_market_data 
                WHERE trade_date = :date 
                AND stock_id NOT IN (:bond, :market, :inverse)
            """)
            df = pd.read_sql(
                sql, 
                engine, 
                params={
                    'date': date_str, 
                    'bond': Config.BOND_SYMBOL, 
                    'market': Config.MARKET_SYMBOL, 
                    'inverse': '00632R'
                }
            )
            df = filter_common_stock_universe(df)
            
        return df, date_str
        
    except Exception as e:
        print(f"❌ 資料庫查詢失敗: {e}")
        return pd.DataFrame(), None


def get_stock_history(stock_id, limit: int = 120, end_date: str | None = None):
    """讀取單一股票歷史行情與指標序列。"""
    normalized_stock_id = normalize_stock_id_value(stock_id)
    if not normalized_stock_id:
        return pd.DataFrame()

    safe_limit = max(1, min(int(limit), 520))
    engine = get_db_engine()
    available_columns = get_table_columns('daily_market_data', engine=engine)
    required_columns = ['trade_date', 'stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']
    optional_columns = [
        'ma5',
        'ma20',
        'ma60',
        'rsi',
        'bias',
        'chip_score',
        'foreign_buy',
        'trust_buy',
        'dealer_buy',
    ]
    selected_columns = [column for column in required_columns if column in available_columns]
    selected_columns.extend(column for column in optional_columns if column in available_columns)
    if 'trade_date' not in selected_columns or 'stock_id' not in selected_columns or 'close_price' not in selected_columns:
        print(f"⚠️ daily_market_data 缺少必要欄位，無法查詢歷史股價: {sorted(available_columns)}")
        return pd.DataFrame()

    filters = ['stock_id = :sid']
    params: dict[str, object] = {'sid': normalized_stock_id}

    if end_date:
        params['end_date'] = normalize_date_str(end_date)
        filters.append('trade_date <= :end_date')

    where_clause = ' AND '.join(filters)
    query_limit = safe_limit * 3
    sql = f"""
        SELECT {', '.join(selected_columns)}
        FROM daily_market_data
        WHERE {where_clause}
        ORDER BY trade_date DESC
        LIMIT {query_limit}
    """

    try:
        history_df = pd.read_sql(text(sql), engine, params=params)
        if history_df.empty:
            return history_df

        for column in optional_columns:
            if column not in history_df.columns:
                history_df[column] = None
        for column in required_columns:
            if column not in history_df.columns:
                history_df[column] = None

        history_df['stock_id'] = history_df['stock_id'].map(normalize_stock_id_value)
        history_df['trade_date'] = history_df['trade_date'].map(normalize_date_str)
        history_df['volume'] = pd.to_numeric(history_df['volume'], errors='coerce').fillna(0)
        history_df = (
            history_df.sort_values(['trade_date', 'volume'], ascending=[False, False])
            .drop_duplicates(subset=['trade_date', 'stock_id'], keep='first')
            .sort_values('trade_date')
            .tail(safe_limit)
            .reset_index(drop=True)
        )
        return history_df
    except Exception as e:
        print(f"❌ 歷史股價查詢失敗 ({normalized_stock_id}): {e}")
        return pd.DataFrame()


def validate_setting(key, value):
    """
    驗證參數合法性
    
    Args:
        key: 設定鍵值
        value: 待驗證的值
    
    Returns:
        (是否合法, 錯誤訊息)
    """
    validators = {
        'ai_threshold': lambda v: (0 <= float(v) <= 1, "AI信心需在 0-1 之間"),
        'stop_loss': lambda v: (0 < float(v) < 1, "停損需在 0-1 之間"),
        'take_profit': lambda v: (0 <= float(v) < 1, "停利需在 0-1 之間（0表示不停利）"),
        'mode': lambda v: (v in ['conservative', 'aggressive'], "模式只能是 conservative 或 aggressive"),
        'ai_top_n': lambda v: (1 <= int(v) <= 10, "推薦數量需在 1-10 之間"),
        'max_hold_days': lambda v: (1 <= int(v) <= 60, "持有天數需在 1-60 之間"),
    }
    
    if key in validators:
        try:
            is_valid, err_msg = validators[key](value)
            return is_valid, err_msg
        except:
            return False, "參數格式錯誤"
    return True, ""


def upsert_stock_data(df, table_name='daily_market_data'):
    """
    原子性更新股票資料（INSERT ... ON DUPLICATE KEY UPDATE）
    
    避免「刪除-插入」模式造成的數據丟失風險
    
    Args:
        df: DataFrame，包含股票資料
        table_name: 目標資料表名稱 (必須在允許清單中)
    
    Returns:
        成功插入/更新的筆數
    """
    if df is None or df.empty:
        return 0
    
    # 資料表名稱安全檢查
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"❌ 不允許的資料表名稱: {table_name}（允許: {_ALLOWED_TABLES}）")

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            # 查詢 DB 實際欄位，過濾 DataFrame 中不存在於表的欄位
            result = conn.execute(text(f"SHOW COLUMNS FROM {table_name}"))
            db_columns = {row[0] for row in result}
            valid_cols = [col for col in df.columns if col in db_columns]
            df = df[valid_cols]

            # 方案：使用 REPLACE INTO（MySQL 特有）
            # REPLACE = DELETE + INSERT，但是原子性操作
            for _, row in df.iterrows():
                columns = ', '.join(row.index)
                placeholders = ', '.join([f':{col}' for col in row.index])

                sql = text(f"""
                    REPLACE INTO {table_name} ({columns})
                    VALUES ({placeholders})
                """)

                conn.execute(sql, row.to_dict())

            conn.commit()

        return len(df)

    except Exception as e:
        print(f"❌ upsert_stock_data 失敗: {e}")
        return 0


def get_market_trend(date_str):
    """
    判斷大盤趨勢（V33 Phase 2: 簡化為二元狀態）
    
    🔥 嚴格邏輯：只在收盤 > MA60 時返回 BULL，否則一律 BEAR
    目的：避免在下跌趨勢中買入，降低 MDD
    
    若 MA60 未計算（為 NULL / 0），則使用 60 日歷史收盤價
    自行計算簡易 MA60，避免因指標缺失造成永久 BEAR 的誤判。
    
    Args:
        date_str: 日期字串
    
    Returns:
        'BULL' | 'BEAR' (簡化為二元狀態，提高安全性)
    """
    try:
        df, _ = get_stock_data(Config.MARKET_SYMBOL, date_str)
        if df.empty:
            return 'BEAR'  # 🔥 預設為 BEAR（保守策略）
        
        data = df.iloc[0]
        close = data.get('close_price')
        ma60 = data.get('ma60')
        
        # 🔥 安全檢查：close 為 None 時預設為 BEAR
        if close is None:
            return 'BEAR'
        
        # 若 MA60 有效（非 None / 非 0），比對（含 3% 容忍區間）
        # 放寬條件：close > MA60 * 0.97 即視為多頭，避免小幅回檔誤觸熔斷
        if ma60 is not None and float(ma60) > 0:
            tolerance = float(ma60) * 0.95
            if float(close) > tolerance:
                return 'BULL'
            else:
                return 'BEAR'
        
        # ---- Fallback: MA60 未計算，從歷史資料自行推算 ----
        try:
            engine = get_db_engine()
            query = text("""
                SELECT close_price FROM daily_market_data
                WHERE stock_id = :sid
                  AND trade_date <= :date
                ORDER BY trade_date DESC
                LIMIT 60
            """)
            with engine.connect() as conn:
                result = conn.execute(query, {'sid': Config.MARKET_SYMBOL, 'date': date_str})
                rows = result.fetchall()
            
            if len(rows) < 20:
                # 歷史資料不足，保守回傳 BEAR
                return 'BEAR'
            
            prices = [float(r[0]) for r in rows if r[0] is not None]
            if not prices:
                return 'BEAR'
            
            computed_ma60 = sum(prices) / len(prices)
            current_close = prices[0]  # 最新收盤

            if current_close > computed_ma60 * 0.95:
                return 'BULL'
            else:
                return 'BEAR'
        except Exception:
            return 'BEAR'
        
    except Exception as e:
        print(f"⚠️ 市場趨勢判斷失敗: {e}")
        return 'BEAR'  # 🔥 錯誤時預設為 BEAR（保守策略）


# ==========================================
# 🎮 V33 Phase 3: PK System 資料庫初始化
# ==========================================
def create_user_simulation_trade(user_id, stock_id, buy_price, buy_date, status='HOLDING'):
    """
    新增使用者模擬交易紀錄

    Returns:
        bool: 是否成功
    """
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO user_simulation_trades 
                (user_id, stock_id, buy_price, buy_date, status)
                VALUES (:user_id, :stock_id, :buy_price, :buy_date, :status)
            """), {
                'user_id': user_id,
                'stock_id': stock_id,
                'buy_price': float(buy_price),
                'buy_date': buy_date,
                'status': status
            })
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ 新增模擬交易失敗: {e}")
        return False


def ensure_indicator_columns(columns: list, table: str = 'daily_market_data'):
    """確保 daily_market_data 表有所需的指標欄位，不存在則自動新增。
    
    使用 ALTER TABLE ADD COLUMN IF NOT EXISTS 語法（MySQL 8.0+）。
    對於不支援的版本，先查詢現有欄位再決定是否新增。
    
    Args:
        columns: 需要確保存在的欄位名稱列表
        table: 資料表名稱（預設 'daily_market_data'）
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"❌ 不允許的資料表名稱: {table}")
    
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            # 查詢現有欄位
            result = conn.execute(text(f"SHOW COLUMNS FROM {table}"))
            existing = {row[0] for row in result.fetchall()}
            
            missing = [c for c in columns if c not in existing]
            if missing:
                for col in missing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} FLOAT DEFAULT 0"))
                        print(f"   ✅ 新增欄位: {table}.{col}")
                    except Exception as e:
                        if 'Duplicate column' not in str(e):
                            print(f"   ⚠️ 新增欄位 {col} 失敗: {e}")
                conn.commit()
            else:
                print(f"   ✅ 所有指標欄位已存在")
    except Exception as e:
        print(f"⚠️ ensure_indicator_columns 失敗: {e}")


def supplement_financial_data(df):
    """
    為 DataFrame 補充 revenue_yoy / op_profit_margin / eps 等財務欄位。
    
    用途：LineBot / Dashboard 即時推薦時，daily_market_data 不含財務資料，
    V34/V35 策略需要 revenue_yoy / op_profit_margin 才能正確篩選。
    
    Args:
        df: 包含 stock_id 欄位的 DataFrame（來自 daily_market_data）
    
    Returns:
        補充財務欄位後的 DataFrame
    """
    if df.empty:
        return df
    
    engine = get_db_engine()
    
    try:
        with engine.connect() as conn:
            # 補充 revenue_yoy（月營收年增率）
            needs_revenue = (
                'revenue_yoy' not in df.columns 
                or df['revenue_yoy'].isna().all() 
                or (df['revenue_yoy'] == 0).all()
            )
            if needs_revenue:
                rev_query = text("""
                    SELECT mr1.stock_id, mr1.revenue_yoy
                    FROM monthly_revenue mr1
                    INNER JOIN (
                        SELECT stock_id, MAX(year * 100 + month) as max_period
                        FROM monthly_revenue
                        GROUP BY stock_id
                    ) mr2 ON mr1.stock_id = mr2.stock_id 
                         AND (mr1.year * 100 + mr1.month) = mr2.max_period
                """)
                rev_df = pd.read_sql(rev_query, conn)
                if not rev_df.empty:
                    rev_df['revenue_yoy'] = rev_df['revenue_yoy'].clip(-100, 500)
                    rev_map = rev_df.set_index('stock_id')['revenue_yoy'].to_dict()
                    df['revenue_yoy'] = df['stock_id'].map(rev_map).fillna(0)
                else:
                    df['revenue_yoy'] = 0
            
            # 補充 op_profit_margin / eps（季度財報）
            needs_financial = (
                'op_profit_margin' not in df.columns 
                or df['op_profit_margin'].isna().all() 
                or (df['op_profit_margin'] == 0).all()
            )
            if needs_financial:
                fin_query = text("""
                    SELECT fs1.stock_id, 
                           fs1.operating_margin / 100 as op_profit_margin,
                           fs1.eps
                    FROM financial_statements fs1
                    INNER JOIN (
                        SELECT stock_id, MAX(year * 10 + quarter) as max_period
                        FROM financial_statements
                        GROUP BY stock_id
                    ) fs2 ON fs1.stock_id = fs2.stock_id 
                         AND (fs1.year * 10 + fs1.quarter) = fs2.max_period
                """)
                fin_df = pd.read_sql(fin_query, conn)
                if not fin_df.empty:
                    op_map = fin_df.set_index('stock_id')['op_profit_margin'].to_dict()
                    eps_map = fin_df.set_index('stock_id')['eps'].to_dict()
                    df['op_profit_margin'] = df['stock_id'].map(op_map).fillna(0)
                    if 'eps' not in df.columns or df['eps'].isna().all():
                        df['eps'] = df['stock_id'].map(eps_map).fillna(0)
                else:
                    df['op_profit_margin'] = 0
                    if 'eps' not in df.columns:
                        df['eps'] = 0
    except Exception as e:
        print(f"⚠️ supplement_financial_data 失敗: {e}")
        if 'revenue_yoy' not in df.columns:
            df['revenue_yoy'] = 0
        if 'op_profit_margin' not in df.columns:
            df['op_profit_margin'] = 0
    
    return df


def get_open_holdings(limit: int = 10):
    """查詢目前持有中的 AI 推薦持股

    從 daily_recommendations 取得最近 20 天、狀態為 OPEN 的推薦，
    並 LEFT JOIN 最新收盤價計算未實現損益。

    Args:
        limit: 最大回傳筆數（預設 10）

    Returns:
        list[dict]: 持股資料列表，每筆含 stock_id, strategy, entry_price,
                    trade_date, current_price
    """
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT r.stock_id, r.strategy, r.close_price AS entry_price,
                   r.trade_date, d.close_price AS current_price
            FROM daily_recommendations r
            LEFT JOIN daily_market_data d 
                ON r.stock_id = d.stock_id AND d.trade_date = (
                    SELECT MAX(trade_date) FROM daily_market_data
                )
            WHERE r.trade_date >= DATE_SUB(CURDATE(), INTERVAL 20 DAY)
              AND r.status = 'OPEN'
            ORDER BY r.trade_date DESC
            LIMIT :lim
        """), {'lim': limit})
        rows = result.fetchall()
    return rows


def safe_float(value):
    """將可能為 NaN / None 的值轉為 float 或 None（API 回傳安全值）"""
    if value is None:
        return None
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(value):
    """將可能為 NaN / None 的值轉為 int 或 None（API 回傳安全值）"""
    if value is None:
        return None
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def ensure_backtest_tables() -> bool:
    """確保回測結果資料表存在。"""
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS backtest_trades (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    strategy VARCHAR(64) NOT NULL,
                    stock_id VARCHAR(20) NOT NULL,
                    buy_date DATE NULL,
                    sell_date DATE NULL,
                    buy_price DECIMAL(12, 4) NULL,
                    sell_price DECIMAL(12, 4) NULL,
                    profit_pct DECIMAL(12, 4) NULL,
                    reason VARCHAR(100) NULL,
                    days INT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_strategy_sell (strategy, sell_date),
                    INDEX idx_sell_date (sell_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS backtest_equity_curve (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    date DATE NOT NULL,
                    asset_value DECIMAL(18, 4) NOT NULL,
                    roi DECIMAL(12, 6) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_date (date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ ensure_backtest_tables 失敗: {e}")
        return False


def save_backtest_results(trades_df: pd.DataFrame = None, equity_df: pd.DataFrame = None) -> bool:
    """將最新回測結果覆寫保存至資料庫。"""
    if trades_df is None and equity_df is None:
        return True

    if not ensure_backtest_tables():
        return False

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            if trades_df is not None:
                if trades_df.empty:
                    # 無資料時才清空全表
                    conn.execute(text("DELETE FROM backtest_trades"))
                else:
                    # 僅清除本次回測涉及的策略舊資料，保留其他策略歷史
                    strategies_in_batch = trades_df['strategy'].dropna().unique().tolist()
                    if strategies_in_batch:
                        placeholders = ', '.join(f':s{i}' for i in range(len(strategies_in_batch)))
                        params = {f's{i}': s for i, s in enumerate(strategies_in_batch)}
                        conn.execute(
                            text(f"DELETE FROM backtest_trades WHERE strategy IN ({placeholders})"),
                            params
                        )
                if not trades_df.empty:
                    insert_sql = text("""
                        INSERT INTO backtest_trades
                        (strategy, stock_id, buy_date, sell_date, buy_price, sell_price, profit_pct, reason, days)
                        VALUES
                        (:strategy, :stock_id, :buy_date, :sell_date, :buy_price, :sell_price, :profit_pct, :reason, :days)
                    """)

                    records = []
                    for _, row in trades_df.iterrows():
                        records.append({
                            'strategy': str(row.get('strategy') or ''),
                            'stock_id': str(row.get('stock_id') or ''),
                            'buy_date': row.get('buy_date'),
                            'sell_date': row.get('sell_date'),
                            'buy_price': safe_float(row.get('buy_price')),
                            'sell_price': safe_float(row.get('sell_price')),
                            'profit_pct': safe_float(row.get('profit_pct')),
                            'reason': str(row.get('reason') or ''),
                            'days': safe_int(row.get('days')),
                        })
                    conn.execute(insert_sql, records)

            if equity_df is not None:
                conn.execute(text("DELETE FROM backtest_equity_curve"))
                if not equity_df.empty:
                    insert_sql = text("""
                        INSERT INTO backtest_equity_curve (date, asset_value, roi)
                        VALUES (:date, :asset_value, :roi)
                    """)
                    records = []
                    for _, row in equity_df.iterrows():
                        records.append({
                            'date': row.get('date'),
                            'asset_value': safe_float(row.get('asset_value')) or 0.0,
                            'roi': safe_float(row.get('roi')) or 0.0,
                        })
                    conn.execute(insert_sql, records)

            conn.commit()
        return True
    except Exception as e:
        print(f"❌ save_backtest_results 失敗: {e}")
        return False


def get_backtest_trades(strategy: str = None, limit: int = None):
    """取得回測交易紀錄，可選擇依策略過濾。"""
    try:
        ensure_backtest_tables()
        engine = get_db_engine()
        params = {}
        sql = """
            SELECT strategy, stock_id, buy_date, sell_date,
                   buy_price, sell_price, profit_pct, reason, days
            FROM backtest_trades
        """
        if strategy:
            sql += " WHERE strategy = :strategy"
            params['strategy'] = str(strategy).strip()
        sql += " ORDER BY sell_date DESC, id DESC"
        if limit is not None:
            params['lim'] = max(1, min(int(limit), 5000))
            sql += " LIMIT :lim"

        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            rows = result.fetchall()

        return [
            {
                'strategy': row[0],
                'stock_id': row[1],
                'buy_date': row[2].strftime('%Y-%m-%d') if row[2] else None,
                'sell_date': row[3].strftime('%Y-%m-%d') if row[3] else None,
                'buy_price': safe_float(row[4]),
                'sell_price': safe_float(row[5]),
                'profit_pct': safe_float(row[6]),
                'reason': row[7],
                'days': safe_int(row[8]),
            }
            for row in rows
        ]
    except Exception as e:
        print(f"❌ get_backtest_trades 失敗: {e}")
        return []


def get_recent_backtest_trades(limit: int = 50):
    """取得最近回測交易紀錄（依賣出日新到舊）。"""
    try:
        return get_backtest_trades(limit=limit)
    except Exception as e:
        print(f"❌ get_recent_backtest_trades 失敗: {e}")
        return []


def get_backtest_equity_curve():
    """取得回測權益曲線（依日期遞增）。"""
    try:
        ensure_backtest_tables()
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT date, asset_value, roi
                FROM backtest_equity_curve
                ORDER BY date ASC, id ASC
            """))
            rows = result.fetchall()

        return {
            'dates': [row[0].strftime('%Y-%m-%d') if row[0] else None for row in rows],
            'equity': [safe_float(row[1]) for row in rows],
            'roi': [safe_float(row[2]) for row in rows],
        }
    except Exception as e:
        print(f"❌ get_backtest_equity_curve 失敗: {e}")
        return {'dates': [], 'equity': [], 'roi': []}


def get_backtest_summary_from_db():
    """從資料庫計算回測摘要（供 API fallback 使用）。"""
    try:
        ensure_backtest_tables()
        engine = get_db_engine()
        with engine.connect() as conn:
            equity_result = conn.execute(text("""
                SELECT date, asset_value
                FROM backtest_equity_curve
                ORDER BY date ASC, id ASC
            """))
            equity_rows = equity_result.fetchall()

            trade_result = conn.execute(text("""
                SELECT profit_pct, days
                FROM backtest_trades
            """))
            trade_rows = trade_result.fetchall()

        if not equity_rows:
            return None

        equity_values = [safe_float(row[1]) or 0.0 for row in equity_rows]
        first_asset = equity_values[0] if equity_values else 0.0
        last_asset = equity_values[-1] if equity_values else 0.0
        total_roi = ((last_asset / first_asset) - 1) * 100 if first_asset > 0 else 0.0

        peak = float('-inf')
        max_drawdown = 0.0
        for value in equity_values:
            if value > peak:
                peak = value
            if peak > 0:
                drawdown = ((value - peak) / peak) * 100
                if drawdown < max_drawdown:
                    max_drawdown = drawdown

        trade_count = len(trade_rows)
        if trade_count > 0:
            profit_values = [safe_float(row[0]) for row in trade_rows]
            win_count = len([p for p in profit_values if p is not None and p > 0])
            win_rate = (win_count / trade_count) * 100

            day_values = [safe_float(row[1]) for row in trade_rows if safe_float(row[1]) is not None]
            avg_hold_days = sum(day_values) / len(day_values) if day_values else 0.0
        else:
            win_rate = 0.0
            avg_hold_days = 0.0

        return {
            'total_roi': round(total_roi, 2),
            'max_drawdown': round(max_drawdown, 2),
            'sharpe_ratio': 0.0,
            'win_rate': round(win_rate, 2),
            'trade_count': trade_count,
            'avg_hold_days': round(avg_hold_days, 1),
        }
    except Exception as e:
        print(f"❌ get_backtest_summary_from_db 失敗: {e}")
        return None


# ============================================
# 📊 財報共用 DB 操作
# ============================================

def ensure_financial_columns(conn):
    """自動檢查並新增 financial_statements 表的選填欄位

    Args:
        conn: SQLAlchemy 連線 (已在 transaction 內)
    """
    optional_columns = {
        'operating_margin': "FLOAT NULL COMMENT '營業利益率(%)'",
    }
    for col_name, col_def in optional_columns.items():
        check_query = text("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'financial_statements'
              AND COLUMN_NAME = :col_name
        """)
        exists = conn.execute(check_query, {"col_name": col_name}).scalar() > 0
        if not exists:
            conn.execute(text(
                f"ALTER TABLE financial_statements ADD COLUMN {col_name} {col_def}"
            ))
            print(f"   ✅ financial_statements.{col_name} 欄位建立完成")


def upsert_financial_statements(
    conn: Connection,
    df: pd.DataFrame,
    west_year: int,
    quarter: int,
) -> int:
    """批量 UPSERT 財報資料至 financial_statements 表。

    This helper is the persistence boundary for MCP-backed quarterly financial
    payloads. Callers must provide rows already normalized to the current DB
    storage unit and include the required columns `stock_id`, `revenue`,
    `operating_expense`, and `operating_profit`. The function rewrites the
    target quarter atomically and preserves the effective idempotent contract
    on `(stock_id, year, quarter)` when the same quarter is replayed.

    Args:
        conn: SQLAlchemy 連線 (已在 transaction 內)
        df: 財報 DataFrame，欄位需符合 MCP financial mapping 後的儲存契約
        west_year: 西元年
        quarter: 季度 (1-4)

    Returns:
        int: 寫入筆數
    """
    # 先清除該季舊資料
    result = conn.execute(text(
        "DELETE FROM financial_statements WHERE year = :year AND quarter = :quarter"
    ), {"year": west_year, "quarter": quarter})
    deleted = result.rowcount
    if deleted > 0:
        print(f"   🗑️ 清除舊資料: {deleted} 筆")

    insert_query = text("""
        INSERT INTO financial_statements
        (stock_id, year, quarter, revenue, rd_expense, operating_expense,
         operating_profit, eps, operating_margin)
        VALUES (:stock_id, :year, :quarter, :revenue, :rd_expense,
                :operating_expense, :operating_profit, :eps, :operating_margin)
        ON DUPLICATE KEY UPDATE
            revenue = VALUES(revenue),
            rd_expense = VALUES(rd_expense),
            operating_expense = VALUES(operating_expense),
            operating_profit = VALUES(operating_profit),
            eps = VALUES(eps),
            operating_margin = VALUES(operating_margin)
    """)

    count = 0
    for _, row in df.iterrows():
        clean_stock_id = str(row['stock_id']).replace('.0', '')
        revenue = int(row['revenue'])
        operating_profit = int(row['operating_profit'])
        op_margin = round((operating_profit / revenue) * 100, 2) if revenue > 0 else 0.0

        conn.execute(insert_query, {
            "stock_id": clean_stock_id,
            "year": int(west_year),
            "quarter": int(quarter),
            "revenue": revenue,
            "rd_expense": int(row.get('rd_expense', 0)),
            "operating_expense": int(row['operating_expense']),
            "operating_profit": operating_profit,
            "eps": float(row.get('eps', 0.0)),
            "operating_margin": op_margin,
        })
        count += 1

    return count


# ==========================================
# 📊 產業分類對照
# ==========================================

_sector_cache: dict = {}


def _load_sector_map() -> dict:
    """載入 stock_sector_map.json（含快取）"""
    global _sector_cache
    if _sector_cache:
        return _sector_cache
    try:
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), 'stock_sector_map.json')
        with open(path, 'r', encoding='utf-8') as f:
            _sector_cache = json.load(f)
    except Exception:
        _sector_cache = {}
    return _sector_cache


def get_stock_sector(stock_id: str) -> str:
    """查詢個股所屬產業

    Args:
        stock_id: 股票代號（如 '2330'）

    Returns:
        產業名稱（如 '半導體'），查無則回傳 '其他'
    """
    m = _load_sector_map()
    info = m.get(str(stock_id).strip(), {})
    return info.get('sector', '其他')


# ==========================================
# 📰 消息面情緒持久化
# ==========================================

def ensure_news_schema(engine=None):
    """確保消息面相關 DB schema 存在（懶初始化）

    1. 建立 daily_news_sentiment 資料表（若不存在）
    2. 為 daily_recommendations 新增 news_boost_reason 欄位（若不存在）
    """
    if engine is None:
        engine = get_db_engine()
    try:
        with engine.connect() as conn:
            # 建立 daily_news_sentiment 資料表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS daily_news_sentiment (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    trade_date  DATE NOT NULL,
                    sentiment   VARCHAR(10) NOT NULL DEFAULT '中性',
                    bull_sectors TEXT,
                    bear_sectors TEXT,
                    bull_reasons TEXT,
                    bear_reasons TEXT,
                    bull_theme_map TEXT,
                    bear_theme_map TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_date (trade_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            for column_name in ['bull_reasons', 'bear_reasons', 'bull_theme_map', 'bear_theme_map']:
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'daily_news_sentiment'
                      AND COLUMN_NAME = :column_name
                """), {"column_name": column_name})
                if result.scalar() == 0:
                    conn.execute(text(f"""
                        ALTER TABLE daily_news_sentiment
                        ADD COLUMN {column_name} TEXT NULL
                    """))
            # 為 daily_recommendations 新增 news_boost_reason 欄位（若尚不存在）
            result = conn.execute(text("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'daily_recommendations'
                  AND COLUMN_NAME = 'news_boost_reason'
            """))
            if result.scalar() == 0:
                conn.execute(text("""
                    ALTER TABLE daily_recommendations
                    ADD COLUMN news_boost_reason VARCHAR(100) NULL
                """))
                print("  ✅ DB 遷移：daily_recommendations.news_boost_reason 欄位已新增")
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ DB schema 確認失敗（不影響執行）: {e}")


def save_news_sentiment(date_str: str, sentiment: str,
                        bull_sectors: list, bear_sectors: list,
                        bull_reasons: list | None = None,
                        bear_reasons: list | None = None,
                        bull_theme_map: dict | None = None,
                        bear_theme_map: dict | None = None) -> None:
    """儲存每日消息面情緒結果到 daily_news_sentiment 資料表

    使用 INSERT … ON DUPLICATE KEY UPDATE 以支援同日重複執行。

    Args:
        date_str: 日期字串（'YYYY-MM-DD'）
        sentiment: '偏多' | '偏空' | '中性'
        bull_sectors: 利多產業列表
        bear_sectors: 利空產業列表
        bull_reasons: 利多重點條列
        bear_reasons: 利空重點條列
        bull_theme_map: 利多族群對應主題
        bear_theme_map: 利空族群對應主題
    """
    import json
    engine = get_db_engine()
    ensure_news_schema(engine)
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO daily_news_sentiment
                    (trade_date, sentiment, bull_sectors, bear_sectors,
                     bull_reasons, bear_reasons, bull_theme_map, bear_theme_map)
                VALUES
                    (:date, :sentiment, :bull, :bear, :bull_reasons, :bear_reasons,
                     :bull_theme_map, :bear_theme_map)
                ON DUPLICATE KEY UPDATE
                    sentiment    = VALUES(sentiment),
                    bull_sectors = VALUES(bull_sectors),
                    bear_sectors = VALUES(bear_sectors),
                    bull_reasons = VALUES(bull_reasons),
                    bear_reasons = VALUES(bear_reasons),
                    bull_theme_map = VALUES(bull_theme_map),
                    bear_theme_map = VALUES(bear_theme_map),
                    created_at   = CURRENT_TIMESTAMP
            """), {
                "date": date_str,
                "sentiment": sentiment,
                "bull": json.dumps(bull_sectors, ensure_ascii=False),
                "bear": json.dumps(bear_sectors, ensure_ascii=False),
                "bull_reasons": json.dumps(bull_reasons or [], ensure_ascii=False),
                "bear_reasons": json.dumps(bear_reasons or [], ensure_ascii=False),
                "bull_theme_map": json.dumps(bull_theme_map or {}, ensure_ascii=False),
                "bear_theme_map": json.dumps(bear_theme_map or {}, ensure_ascii=False),
            })
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ 消息面情緒儲存失敗: {e}")


def get_news_sentiment(date_str: str = None) -> dict:
    """取得指定日期的消息面情緒資料

    Args:
        date_str: 日期字串，None 表示取最新一筆

    Returns:
        dict: {"trade_date": "...", "sentiment": "偏多",
               "bull_sectors": [...], "bear_sectors": [...]}
              查無資料時回傳預設中性值
    """
    import json
    default = {
        "trade_date": date_str or '',
        "sentiment": "中性",
        "bull_sectors": [],
        "bear_sectors": [],
        "bull_reasons": [],
        "bear_reasons": [],
        "bull_theme_map": {},
        "bear_theme_map": {},
    }
    engine = get_db_engine()
    ensure_news_schema(engine)
    try:
        with engine.connect() as conn:
            if date_str:
                row = conn.execute(text("""
                    SELECT trade_date, sentiment, bull_sectors, bear_sectors,
                           bull_reasons, bear_reasons, bull_theme_map, bear_theme_map
                    FROM daily_news_sentiment
                    WHERE trade_date = :date
                    LIMIT 1
                """), {"date": date_str}).fetchone()
            else:
                row = conn.execute(text("""
                    SELECT trade_date, sentiment, bull_sectors, bear_sectors,
                           bull_reasons, bear_reasons, bull_theme_map, bear_theme_map
                    FROM daily_news_sentiment
                    ORDER BY trade_date DESC
                    LIMIT 1
                """)).fetchone()

        if not row:
            return default

        td = row[0]
        return {
            "trade_date": td.strftime('%Y-%m-%d') if hasattr(td, 'strftime') else str(td),
            "sentiment": row[1] or '中性',
            "bull_sectors": json.loads(row[2]) if row[2] else [],
            "bear_sectors": json.loads(row[3]) if row[3] else [],
            "bull_reasons": json.loads(row[4]) if row[4] else [],
            "bear_reasons": json.loads(row[5]) if row[5] else [],
            "bull_theme_map": json.loads(row[6]) if row[6] else {},
            "bear_theme_map": json.loads(row[7]) if row[7] else {},
        }
    except Exception as e:
        print(f"  ⚠️ 消息面情緒讀取失敗: {e}")
        return default


def get_daily_recommendations(date_str: str = None, strategy: str = None,
                              limit: int | None = None,
                              include_heartbeats: bool = False) -> pd.DataFrame:
    """讀取每日推薦結果（供 Dashboard / Line Bot 共用）

    Args:
        date_str: 交易日期，None 代表最新交易日
        strategy: 策略代號（如 'v36_chip_momentum'），None 代表不限制
        limit: 限制筆數，None 代表不限制

    Returns:
        DataFrame: 包含 daily_recommendations 主要欄位
    """
    engine = get_db_engine()
    try:
        if not date_str:
            latest = get_actual_latest_date() or get_latest_trade_date()
            if not latest:
                return pd.DataFrame()
            date_str = normalize_date_str(latest)

        sql = """
            SELECT stock_id, trade_date, strategy, close_price, ai_score, rsi, volume,
                   news_boost_reason
            FROM daily_recommendations
            WHERE trade_date = :date
        """
        params = {"date": date_str}

        if strategy:
            sql += " AND strategy = :strategy"
            params["strategy"] = strategy

        sql += " ORDER BY ai_score DESC"

        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)

        result_df = pd.read_sql(text(sql), engine, params=params)
        if include_heartbeats or result_df.empty or 'stock_id' not in result_df.columns:
            return result_df

        filtered_df = result_df[
            result_df['stock_id'].astype(str).str.strip().str.upper() != RECOMMENDATION_HEARTBEAT_STOCK_ID
        ].copy()
        return filtered_df.reset_index(drop=True)
    except Exception as e:
        print(f"  ⚠️ 讀取 daily_recommendations 失敗: {e}")
        return pd.DataFrame()


def merge_recommendations_with_market_data(
    recommendations: pd.DataFrame,
    market_df: pd.DataFrame,
) -> pd.DataFrame:
    """將落庫推薦結果與當日市場欄位合併，避免多處重複 merge 邏輯。"""
    if recommendations is None or recommendations.empty:
        return pd.DataFrame()

    merged = recommendations.copy()
    merged['stock_id'] = merged['stock_id'].astype(str)
    if 'trade_date' in merged.columns and 'recommendation_trade_date' not in merged.columns:
        merged['recommendation_trade_date'] = merged['trade_date'].map(normalize_date_str)
    if 'close_price' in merged.columns and 'recommendation_close_price' not in merged.columns:
        merged['recommendation_close_price'] = merged['close_price']
    if 'rsi' in merged.columns and 'recommendation_rsi' not in merged.columns:
        merged['recommendation_rsi'] = merged['rsi']
    if 'volume' in merged.columns and 'recommendation_volume' not in merged.columns:
        merged['recommendation_volume'] = merged['volume']
    merged['recommendation_price_basis'] = 'raw_close'
    merged['recommendation_data_source'] = 'daily_recommendations'

    if market_df is None or market_df.empty:
        merged['market_trade_date'] = None
        merged['market_close_price'] = None
        merged['price_trade_date'] = merged.get('recommendation_trade_date')
        merged['price_basis'] = 'raw_close'
        merged['price_data_source'] = 'daily_recommendations'
        return merged

    market_copy = market_df.copy()
    market_copy['stock_id'] = market_copy['stock_id'].astype(str)
    if 'trade_date' in market_copy.columns:
        market_copy['trade_date'] = market_copy['trade_date'].map(normalize_date_str)

    merged = merged.merge(
        market_copy,
        on='stock_id',
        how='left',
        suffixes=('', '_market')
    )

    merged['market_trade_date'] = (
        merged['trade_date_market'].map(normalize_date_str)
        if 'trade_date_market' in merged.columns
        else None
    )
    merged['market_close_price'] = (
        merged['close_price_market']
        if 'close_price_market' in merged.columns
        else None
    )

    for field in ['rsi', 'volume']:
        market_field = f'{field}_market'
        if market_field in merged.columns:
            merged[field] = merged[market_field].fillna(merged[field])

    if 'close_price_market' in merged.columns:
        has_market_close = merged['close_price_market'].notna()
        merged['close_price'] = merged['close_price_market'].fillna(merged['recommendation_close_price'])
        merged['price_trade_date'] = merged['market_trade_date'].where(
            has_market_close,
            merged.get('recommendation_trade_date'),
        )
        merged['price_basis'] = has_market_close.map(
            lambda flag: 'latest_actual_close' if bool(flag) else 'raw_close'
        )
        merged['price_data_source'] = has_market_close.map(
            lambda flag: 'daily_market_data' if bool(flag) else 'daily_recommendations'
        )
    else:
        merged['price_trade_date'] = merged.get('recommendation_trade_date')
        merged['price_basis'] = 'raw_close'
        merged['price_data_source'] = 'daily_recommendations'

    return merged


def _get_prior_recommendation_dates(date_str: str, strategy: str = None) -> list[str]:
    """取得指定日期前、由近到遠的推薦日期清單。"""
    engine = get_db_engine()
    sql = """
        SELECT DISTINCT trade_date
        FROM daily_recommendations
        WHERE trade_date < :date
    """
    params = {'date': date_str}

    if strategy:
        sql += " AND strategy = :strategy"
        params['strategy'] = strategy

    sql += " ORDER BY trade_date DESC"

    dates_df = pd.read_sql(text(sql), engine, params=params)
    if dates_df.empty:
        return []

    return [normalize_date_str(value) for value in dates_df['trade_date'].tolist() if normalize_date_str(value)]


def _calc_date_diff_days(older_date: str, newer_date: str) -> int | None:
    """計算兩個日期字串相差天數。"""
    if not older_date or not newer_date:
        return None
    try:
        return int((pd.to_datetime(newer_date) - pd.to_datetime(older_date)).days)
    except Exception:
        return None


def _resolve_requested_date(date_str: str | None = None) -> str | None:
    """解析使用者請求日期，未指定時使用今日日期。"""
    requested_date = normalize_date_str(date_str)
    if requested_date:
        return requested_date
    return normalize_date_str(pd.Timestamp.now())


def _resolve_market_anchor_date(requested_date: str | None) -> str | None:
    """取得指定日期當日或之前最近一個有效市場交易日。"""
    normalized_requested = normalize_date_str(requested_date)
    if not normalized_requested:
        return None

    valid_dates = get_valid_market_dates(end_date=normalized_requested)
    if valid_dates:
        return normalize_date_str(valid_dates[-1])

    latest = get_latest_trade_date()
    latest_str = normalize_date_str(latest)
    if latest_str and latest_str <= normalized_requested:
        return latest_str
    return None


def _build_recommendation_resolution_meta(
    requested_date: str | None,
    market_anchor_date: str | None,
    recommendation_date: str | None,
    *,
    resolution_source: str,
    fallback_used: bool,
    has_persisted_snapshot: bool,
    market_circuit_breaker_active: bool,
    current_day_recommendations_used: bool,
    fallback_too_old: bool,
    fallback_age_days: int | None,
    last_available_recommendation_date: str | None,
) -> dict:
    return {
        'requested_date': requested_date,
        'market_anchor_date': market_anchor_date,
        'recommendation_date': recommendation_date,
        'resolution_source': resolution_source,
        'fallback_used': fallback_used,
        'has_persisted_snapshot': has_persisted_snapshot,
        'market_circuit_breaker_active': market_circuit_breaker_active,
        'current_day_recommendations_used': current_day_recommendations_used,
        'fallback_too_old': fallback_too_old,
        'fallback_age_days': fallback_age_days,
        'last_available_recommendation_date': last_available_recommendation_date,
    }


def _classify_recommendation_snapshot(snapshot_rows: pd.DataFrame) -> tuple[pd.DataFrame, bool, bool]:
    """拆分推薦快照中的候選股與 heartbeat 狀態。"""
    if snapshot_rows is None or snapshot_rows.empty or 'stock_id' not in snapshot_rows.columns:
        return pd.DataFrame(), False, False

    prepared = snapshot_rows.copy()
    stock_ids = prepared['stock_id'].astype(str).str.strip().str.upper()
    heartbeat_mask = stock_ids == RECOMMENDATION_HEARTBEAT_STOCK_ID
    has_heartbeat = bool(heartbeat_mask.any())
    candidates = prepared.loc[~heartbeat_mask].copy().reset_index(drop=True)
    has_candidates = not candidates.empty
    return candidates, has_heartbeat, has_candidates


def get_recommendations_with_market_fallback(
    date_str: str = None,
    strategy: str = None,
    limit: int | None = None,
    max_fallback_age_days: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """取得指定策略的持久化推薦快照，必要時回推同策略最後一筆資料。"""
    requested_date = _resolve_requested_date(date_str)
    market_anchor_date = _resolve_market_anchor_date(requested_date)
    if not requested_date or not market_anchor_date:
        return pd.DataFrame(), _build_recommendation_resolution_meta(
            requested_date=requested_date,
            market_anchor_date=market_anchor_date,
            recommendation_date=market_anchor_date,
            resolution_source='missing',
            fallback_used=False,
            has_persisted_snapshot=False,
            market_circuit_breaker_active=False,
            current_day_recommendations_used=False,
            fallback_too_old=False,
            fallback_age_days=None,
            last_available_recommendation_date=None,
        )

    if max_fallback_age_days is None:
        max_fallback_age_days = Config.RECOMMENDATION_FALLBACK_MAX_AGE_DAYS

    circuit_breaker_active = get_market_trend(market_anchor_date) != 'BULL'
    current_rows = get_daily_recommendations(
        date_str=market_anchor_date,
        strategy=strategy,
        limit=limit,
        include_heartbeats=True,
    )
    current_candidates, current_has_heartbeat, current_has_candidates = _classify_recommendation_snapshot(current_rows)

    if current_has_heartbeat:
        return pd.DataFrame(), _build_recommendation_resolution_meta(
            requested_date=requested_date,
            market_anchor_date=market_anchor_date,
            recommendation_date=market_anchor_date,
            resolution_source='heartbeat',
            fallback_used=False,
            has_persisted_snapshot=True,
            market_circuit_breaker_active=circuit_breaker_active,
            current_day_recommendations_used=False,
            fallback_too_old=False,
            fallback_age_days=0,
            last_available_recommendation_date=None,
        )

    if current_has_candidates:
        return current_candidates, _build_recommendation_resolution_meta(
            requested_date=requested_date,
            market_anchor_date=market_anchor_date,
            recommendation_date=market_anchor_date,
            resolution_source='persisted',
            fallback_used=False,
            has_persisted_snapshot=True,
            market_circuit_breaker_active=circuit_breaker_active,
            current_day_recommendations_used=True,
            fallback_too_old=False,
            fallback_age_days=0,
            last_available_recommendation_date=None,
        )

    for candidate_date in _get_prior_recommendation_dates(market_anchor_date, strategy=strategy):
        last_available_recommendation_date = candidate_date

        fallback_age_days = _calc_date_diff_days(candidate_date, market_anchor_date)
        if (
            max_fallback_age_days is not None
            and fallback_age_days is not None
            and fallback_age_days > max_fallback_age_days
        ):
            return pd.DataFrame(), _build_recommendation_resolution_meta(
                requested_date=requested_date,
                market_anchor_date=market_anchor_date,
                recommendation_date=market_anchor_date,
                resolution_source='missing',
                fallback_used=False,
                has_persisted_snapshot=False,
                market_circuit_breaker_active=circuit_breaker_active,
                current_day_recommendations_used=False,
                fallback_too_old=True,
                fallback_age_days=fallback_age_days,
                last_available_recommendation_date=last_available_recommendation_date,
            )

        fallback_rows = get_daily_recommendations(
            date_str=candidate_date,
            strategy=strategy,
            limit=limit,
            include_heartbeats=True,
        )
        fallback_candidates, fallback_has_heartbeat, fallback_has_candidates = _classify_recommendation_snapshot(fallback_rows)
        if not fallback_has_heartbeat and not fallback_has_candidates:
            continue

        if fallback_has_heartbeat:
            return pd.DataFrame(), _build_recommendation_resolution_meta(
                requested_date=requested_date,
                market_anchor_date=market_anchor_date,
                recommendation_date=candidate_date,
                resolution_source='heartbeat',
                fallback_used=True,
                has_persisted_snapshot=True,
                market_circuit_breaker_active=circuit_breaker_active,
                current_day_recommendations_used=False,
                fallback_too_old=False,
                fallback_age_days=fallback_age_days,
                last_available_recommendation_date=last_available_recommendation_date,
            )

        return fallback_candidates, _build_recommendation_resolution_meta(
            requested_date=requested_date,
            market_anchor_date=market_anchor_date,
            recommendation_date=candidate_date,
            resolution_source='strategy_fallback',
            fallback_used=True,
            has_persisted_snapshot=True,
            market_circuit_breaker_active=circuit_breaker_active,
            current_day_recommendations_used=False,
            fallback_too_old=False,
            fallback_age_days=fallback_age_days,
            last_available_recommendation_date=last_available_recommendation_date,
        )

    return pd.DataFrame(), _build_recommendation_resolution_meta(
        requested_date=requested_date,
        market_anchor_date=market_anchor_date,
        recommendation_date=market_anchor_date,
        resolution_source='missing',
        fallback_used=False,
        has_persisted_snapshot=False,
        market_circuit_breaker_active=circuit_breaker_active,
        current_day_recommendations_used=False,
        fallback_too_old=False,
        fallback_age_days=None,
        last_available_recommendation_date=None,
    )


def format_market_fallback_notice(meta: dict, strategy_display: str) -> str:
    """統一格式化推薦解析後的提示文字。"""
    if not meta:
        return ''

    requested_date = meta.get('requested_date') or ''
    recommendation_date = meta.get('recommendation_date') or requested_date
    market_anchor_date = meta.get('market_anchor_date') or recommendation_date
    resolution_source = meta.get('resolution_source') or 'missing'

    if resolution_source == 'heartbeat' and not meta.get('fallback_used'):
        return (
            f"ℹ️ {market_anchor_date} 的{strategy_display}已完成選股，當日為零候選，無符合條件標的。"
            f"以下顯示空結果，未回推舊名單。"
        )

    if resolution_source == 'heartbeat' and meta.get('fallback_used'):
        return (
            f"ℹ️ {requested_date} 對應市場基準日 {market_anchor_date} 缺少{strategy_display}快照，"
            f"已回推至 {recommendation_date} 最近一次完成紀錄；該日結果為零候選。"
        )

    if meta.get('fallback_used'):
        return (
            f"⚠️ {requested_date} 對應市場基準日 {market_anchor_date} 缺少當日{strategy_display}快照。"
            f"大盤 MA60 風險判斷僅供參考；以下改顯示 {recommendation_date} 最近一次已落庫推薦，非當日新訊號。"
        )

    if resolution_source == 'missing':
        if meta.get('fallback_too_old'):
            last_date = meta.get('last_available_recommendation_date') or '未知日期'
            age_days = meta.get('fallback_age_days')
            age_text = f"距今 {age_days} 天" if age_days is not None else '時間過久'
            return (
                f"⚠️ 最近可用的{strategy_display}落庫日期為 {last_date}，{age_text}，"
                f"已超過可接受範圍，因此不顯示舊名單。"
            )
        return f"⚠️ {requested_date} 尚無可用的{strategy_display}落庫推薦紀錄。"

    if meta.get('current_day_recommendations_used'):
        return (
            f"ℹ️ 以下顯示 {market_anchor_date} 的當日{strategy_display}落庫推薦。"
        )

    return ''
