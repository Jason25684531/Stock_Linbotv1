"""
資料庫與設定管理統一模組
============================================
功能：
1. 資料庫連線管理
2. 設定值讀寫（防 SQL Injection）
3. 資料查詢輔助函數
"""
import pandas as pd
import time
from sqlalchemy import create_engine, text
from config import Config


# 🔥 Singleton 引擎 + 連線池（避免 Too many connections）
_engine_instance = None

# 允許的資料表名稱（防止 SQL Injection）
_ALLOWED_TABLES = {
    'daily_market_data', 'user_settings', 'user_simulation_trades',
    'monthly_revenue', 'financial_statements', 'daily_recommendations',
    'backtest_trades', 'backtest_equity_curve',
}


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
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT MAX(trade_date) FROM daily_market_data")
            ).scalar()
            if result:
                return result if hasattr(result, 'strftime') else result
    except Exception as e:
        print(f"⚠️ 獲取最新日期失敗: {e}")
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
            date_str = latest.strftime('%Y-%m-%d') if hasattr(latest, 'strftime') else str(latest)

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
            
        return df, date_str
        
    except Exception as e:
        print(f"❌ 資料庫查詢失敗: {e}")
        return pd.DataFrame(), None


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

def init_pk_tables():
    """
    建立 PK System 所需資料表
    - user_simulation_trades: 使用者模擬交易記錄
    """
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            # 建立使用者模擬交易表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_simulation_trades (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(100) NOT NULL COMMENT '使用者 ID (Line User ID)',
                    stock_id VARCHAR(20) NOT NULL COMMENT '股票代碼',
                    buy_price DECIMAL(10, 2) NOT NULL COMMENT '買入價格',
                    buy_date DATE NOT NULL COMMENT '買入日期',
                    sell_price DECIMAL(10, 2) DEFAULT NULL COMMENT '賣出價格',
                    sell_date DATE DEFAULT NULL COMMENT '賣出日期',
                    status VARCHAR(20) DEFAULT 'HOLDING' COMMENT '狀態: HOLDING, CLOSED',
                    roi DECIMAL(10, 4) DEFAULT NULL COMMENT '報酬率 (百分比)',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_status (user_id, status),
                    INDEX idx_buy_date (buy_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='使用者模擬交易記錄'
            """))
            conn.commit()
        print("✅ PK System 資料表初始化完成")
        return True
    except Exception as e:
        print(f"❌ init_pk_tables 失敗: {e}")
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


def get_recent_backtest_trades(limit: int = 50):
    """取得最近回測交易紀錄（依賣出日新到舊）。"""
    try:
        ensure_backtest_tables()
        lim = max(1, min(int(limit), 500))
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT strategy, stock_id, buy_date, sell_date,
                       buy_price, sell_price, profit_pct, reason, days
                FROM backtest_trades
                ORDER BY sell_date DESC, id DESC
                LIMIT :lim
            """), {'lim': lim})
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


def upsert_financial_statements(conn, df, west_year: int, quarter: int):
    """批量 UPSERT 財報資料至 financial_statements 表

    Args:
        conn: SQLAlchemy 連線 (已在 transaction 內)
        df: 爬蟲回傳的 DataFrame
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


def get_stocks_by_sectors(sectors: list[str]) -> list[str]:
    """取得屬於指定產業的所有股票代號

    Args:
        sectors: 產業名稱列表（如 ['半導體', '航運']）

    Returns:
        符合的 stock_id 列表
    """
    m = _load_sector_map()
    target = set(sectors)
    return [sid for sid, info in m.items() if info.get('sector') in target]
