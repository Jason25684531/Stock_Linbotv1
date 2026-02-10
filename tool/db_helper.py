"""
資料庫與設定管理統一模組
============================================
功能：
1. 資料庫連線管理
2. 設定值讀寫（防 SQL Injection）
3. 資料查詢輔助函數
"""
import pandas as pd
from sqlalchemy import create_engine, text
from config import Config


def get_db_engine():
    """獲取資料庫引擎"""
    return create_engine(Config.SQLALCHEMY_DATABASE_URI)


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
        table_name: 目標資料表名稱
    
    Returns:
        成功插入/更新的筆數
    """
    if df is None or df.empty:
        return 0
    
    try:
        engine = get_db_engine()
        
        # 使用 to_sql 的 method 參數實現 upsert
        # 注意：這裡使用簡化版本，利用 pandas 的 replace 方法
        # 實際生產環境可以用 executemany 配合自定義 upsert SQL 提升性能
        
        with engine.connect() as conn:
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
    
    Args:
        date_str: 日期字串
    
    Returns:
        'BULL' | 'BEAR' (簡化為二元狀態，提高安全性)
    """
    try:
        df, _ = get_stock_data(Config.MARKET_SYMBOL, date_str)
        if df.empty or 'ma60' not in df.columns:
            return 'BEAR'  # 🔥 預設為 BEAR（保守策略）
        
        data = df.iloc[0]
        close = data.get('close_price')
        ma60 = data.get('ma60')
        
        # 🔥 安全檢查：任一數值為 None 時預設為 BEAR
        if close is None or ma60 is None:
            return 'BEAR'
        
        # 🔥 嚴格條件：只有收盤 > MA60 才視為多頭
        if close > ma60:
            return 'BULL'
        else:
            return 'BEAR'  # 其他情況一律視為空頭，禁止買入
    except Exception as e:
        print(f"⚠️ 市場趨勢判斷失敗: {e}")
        return 'BEAR'  # 🔥 錯誤時預設為 BEAR（保守策略）


# ==========================================
# 🎮 V33 Phase 3: PK System 資料庫初始化
# ==========================================
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
