"""每日選股執行腳本 (V33 Strategy Factory - Multi-Strategy Support)"""
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 修復 Windows 終端機 UTF-8 編碼問題
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from sqlalchemy import text
from datetime import datetime

from core.db_helper import get_db_engine, get_stock_data
from core.strategy_manager import StrategyManager
from core.calc_indicators import (
    calculate_ratio_features,
    calculate_rsi, calculate_macd, calculate_kd,
    calculate_bb_width, calculate_atr, calculate_natr,
    calculate_std_20, calculate_bias,
    calculate_consec_days, calculate_margin_change_pct,
    calculate_chip_score,
)
from core.model_utils import load_model
from config import Config

# 新聞族群加分快取（每日只呼叫一次 Gemini）
_news_boost_cache: dict = {
    "bull_sectors": [], "bear_sectors": [], "sentiment": "中性"
}
# 個股層級新聞快取（key: stock_id, value: {"score": int, "reason": str}）
_stock_news_cache: dict = {}

# 模型存放目錄（與 3_train_model.py 一致）


def compute_indicators_from_history(date_str: str, engine) -> pd.DataFrame:
    """
    從歷史資料計算技術指標，並回傳最新一天含指標的完整 DataFrame。

    因為 daily_market_data 中只有原始行情（MA/RSI/NATR 等為 NULL），
    需要載入足夠長度的歷史資料才能正確計算滾動窗口指標。

    流程：
    1. 從 DB 載入最近 150 天全市場資料
    2. 逐股票計算 MA5/MA20/MA60、RSI、MACD、KD、BB、ATR、NATR、STD_20
    3. 取最新交易日的橫截面資料
    4. 同步回寫指標到 daily_market_data（供 get_market_trend 使用）

    Args:
        date_str: 目標日期字串 (YYYY-MM-DD)
        engine: SQLAlchemy engine

    Returns:
        DataFrame: 最新日含完整技術指標的全市場資料
    """
    print("📊 計算技術指標（載入歷史資料中）...")

    sql = text("""
        SELECT * FROM daily_market_data
        WHERE trade_date >= DATE_SUB(:date, INTERVAL 150 DAY)
        ORDER BY stock_id, trade_date
    """)
    df = pd.read_sql(sql, engine, params={'date': date_str})
    print(f"  ✓ 載入 {len(df)} 筆歷史資料（約 150 天）")

    if df.empty:
        return pd.DataFrame()

    # 數值清洗
    num_cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna(subset=['close_price'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['stock_id', 'trade_date'])

    # ---- 計算滾動指標（groupby stock_id）----
    print("  ✓ 計算 MA5 / MA20 / MA60...")
    df['ma5'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(5, min_periods=3).mean())
    df['ma20'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df['ma60'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(60, min_periods=30).mean())

    print("  ✓ 計算 Bias / RSI / MACD...")
    df['bias'] = (df['close_price'] - df['ma20']) / df['ma20'].replace(0, np.nan) * 100
    df['bias'] = df['bias'].fillna(0)
    df['rsi'] = df.groupby('stock_id')['close_price'].transform(calculate_rsi)
    df['macd_hist'] = df.groupby('stock_id')['close_price'].transform(calculate_macd)

    print("  ✓ 計算 KD / BB / ATR / NATR / STD_20...")
    df['kd_k'] = df.groupby('stock_id').apply(
        lambda g: calculate_kd(g), include_groups=False
    ).reset_index(level=0, drop=True)

    df['bb_width'] = df.groupby('stock_id')['close_price'].transform(calculate_bb_width)

    df['atr'] = df.groupby('stock_id').apply(
        lambda g: calculate_atr(g), include_groups=False
    ).reset_index(level=0, drop=True)

    df['natr'] = df.groupby('stock_id').apply(
        lambda g: calculate_natr(g), include_groups=False
    ).reset_index(level=0, drop=True)

    df['std_20'] = df.groupby('stock_id')['close_price'].transform(calculate_std_20)

    # 🆕 Phase 3: 籌碼面指標（V36 Chip Momentum 所需）
    print("  ✓ 計算籌碼面指標 (chip_score, consec_days, margin_change)...")
    vol_safe = df['volume'].replace(0, 1)

    if 'dealer_buy' in df.columns:
        df['dealer_ratio'] = (df['dealer_buy'] / vol_safe).clip(-0.5, 0.5)
    else:
        df['dealer_ratio'] = 0

    if 'foreign_buy' in df.columns:
        df['foreign_ratio'] = (df['foreign_buy'] / vol_safe).clip(-0.5, 0.5)
    else:
        df['foreign_ratio'] = 0

    if 'trust_buy' in df.columns:
        df['trust_ratio'] = (df['trust_buy'] / vol_safe).clip(-0.5, 0.5)
    else:
        df['trust_ratio'] = 0

    if 'foreign_buy' in df.columns:
        df['foreign_consec_days'] = df.groupby('stock_id')['foreign_buy'].transform(calculate_consec_days)
    else:
        df['foreign_consec_days'] = 0

    if 'trust_buy' in df.columns:
        df['trust_consec_days'] = df.groupby('stock_id')['trust_buy'].transform(calculate_consec_days)
    else:
        df['trust_consec_days'] = 0

    if 'margin_balance' in df.columns:
        df['margin_change_pct'] = df.groupby('stock_id')['margin_balance'].transform(calculate_margin_change_pct)
    else:
        df['margin_change_pct'] = 0

    df['chip_score'] = calculate_chip_score(df)

    # 填補 NaN
    indicator_cols = ['ma5', 'ma20', 'ma60', 'bias', 'rsi', 'macd_hist',
                      'kd_k', 'bb_width', 'atr', 'natr', 'std_20',
                      'dealer_ratio', 'foreign_ratio', 'trust_ratio',
                      'foreign_consec_days', 'trust_consec_days',
                      'margin_change_pct', 'chip_score']
    for col in indicator_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # ---- 取最新交易日橫截面 ----
    latest_date = df['trade_date'].max()
    latest_df = df[df['trade_date'] == latest_date].copy()

    has_ma60 = (latest_df['ma60'] > 0).sum()
    has_rsi = (latest_df['rsi'] > 0).sum()
    has_natr = (latest_df['natr'] > 0).sum()
    print(f"  ✓ 最新日 {latest_date.strftime('%Y-%m-%d')}: {len(latest_df)} 檔")
    print(f"    MA60: {has_ma60} 檔 | RSI: {has_rsi} 檔 | NATR: {has_natr} 檔")

    # ---- 回寫指標到 DB（供 get_market_trend 等使用）----
    _write_indicators_to_db(latest_df, engine)

    return latest_df


def _write_indicators_to_db(df: pd.DataFrame, engine):
    """
    批次回寫技術指標到 daily_market_data（僅更新最新日的指標欄位）

    使用臨時表 + JOIN UPDATE 的高效批量寫入方式，
    避免逐筆 UPDATE 造成的鎖超時問題。
    
    🔥 V35 更新: 新增 revenue_yoy 欄位回寫（支援 V34/V35 策略）
    """
    if df.empty:
        return

    # 🔥 V35+V36: 更新欄位列表（含 revenue_yoy + 籌碼面指標）
    indicator_cols = ['ma5', 'ma20', 'ma60', 'bias', 'rsi', 'macd_hist',
                      'kd_k', 'bb_width', 'atr', 'natr', 'std_20', 'revenue_yoy',
                      'dealer_ratio', 'foreign_ratio', 'trust_ratio',
                      'foreign_consec_days', 'trust_consec_days',
                      'margin_change_pct', 'chip_score']

    try:
        with engine.connect() as conn:
            result = conn.execute(text("DESCRIBE daily_market_data"))
            existing_cols = {row[0] for row in result.fetchall()}

        cols_to_update = [c for c in indicator_cols if c in existing_cols and c in df.columns]
        if not cols_to_update:
            return

        date_val = df['trade_date'].iloc[0]
        date_str = date_val.strftime('%Y-%m-%d') if hasattr(date_val, 'strftime') else str(date_val)

        # 準備臨時表資料
        tmp_df = df[['stock_id'] + cols_to_update].copy()
        for c in cols_to_update:
            tmp_df[c] = pd.to_numeric(tmp_df[c], errors='coerce').fillna(0)

        with engine.connect() as conn:
            # 設定較長的鎖等待時間
            conn.execute(text("SET innodb_lock_wait_timeout = 600"))

            # 1. 建立臨時表
            col_defs = ', '.join([f"{c} DOUBLE" for c in cols_to_update])
            conn.execute(text("DROP TABLE IF EXISTS _tmp_indicators"))
            conn.execute(text(f"""
                CREATE TABLE _tmp_indicators (
                    stock_id VARCHAR(20) PRIMARY KEY,
                    {col_defs}
                )
            """))
            conn.commit()

            # 2. 寫入臨時表（pandas to_sql 很快）
            tmp_df.to_sql('_tmp_indicators', conn, if_exists='append', index=False, chunksize=5000)

            # 3. 分批 UPDATE（每批 2000 筆避免鎖超時）
            set_clause = ', '.join([f"d.{c} = t.{c}" for c in cols_to_update])
            stock_ids = tmp_df['stock_id'].tolist()
            batch_size = 2000
            updated = 0
            for i in range(0, len(stock_ids), batch_size):
                batch = stock_ids[i:i + batch_size]
                placeholders = ','.join([f"'{sid}'" for sid in batch])
                conn.execute(text(f"""
                    UPDATE daily_market_data d
                    INNER JOIN _tmp_indicators t ON d.stock_id = t.stock_id
                    SET {set_clause}
                    WHERE d.trade_date = :trade_date
                    AND d.stock_id IN ({placeholders})
                """), {'trade_date': date_str})
                conn.commit()
                updated += len(batch)
                print(f"    寫回進度: {updated}/{len(stock_ids)}")

            # 4. 清理臨時表
            conn.execute(text("DROP TABLE IF EXISTS _tmp_indicators"))
            conn.commit()

        print(f"  ✓ 已回寫 {len(tmp_df)} 檔指標到 daily_market_data")
    except Exception as e:
        print(f"  ⚠️ 回寫指標失敗（不影響選股）: {e}")


def merge_financial_data(df: pd.DataFrame, engine) -> pd.DataFrame:
    """
    合併季度財報數據到日線數據 [V35 升級: 新增營業利益率]
    
    財報是季度數據（稀疏），日線是每日數據（密集）
    使用 forward fill 方法：每檔股票使用最新的季報數據
    
    Args:
        df: 日線資料 DataFrame
        engine: 資料庫引擎
    
    Returns:
        合併後的 DataFrame (新增 rd_ratio, eps, op_profit_margin 欄位)
    """
    try:
        # 從資料庫讀取最新的財報數據（每檔股票取最新一季）
        # [V35 升級] 新增查詢 operating_expense, operating_profit
        # [V35 升級] 直接在 SQL 計算營業利益率 (op_profit_margin)
        query = text("""
            SELECT 
                fs1.stock_id,
                fs1.year,
                fs1.quarter,
                fs1.revenue,
                fs1.rd_expense,
                fs1.operating_expense,
                fs1.operating_profit,
                fs1.eps,
                CASE 
                    WHEN fs1.revenue > 0 THEN fs1.rd_expense / fs1.revenue 
                    ELSE 0 
                END as rd_ratio,
                CASE 
                    WHEN fs1.revenue > 0 THEN fs1.operating_profit / fs1.revenue 
                    ELSE 0 
                END as op_profit_margin
            FROM financial_statements fs1
            INNER JOIN (
                SELECT stock_id, MAX(year * 10 + quarter) as max_period
                FROM financial_statements
                WHERE year >= 1911
                GROUP BY stock_id
            ) fs2 ON fs1.stock_id = fs2.stock_id 
                 AND (fs1.year * 10 + fs1.quarter) = fs2.max_period
            WHERE fs1.year >= 1911
        """)
        
        with engine.connect() as conn:
            financial_df = pd.read_sql(query, conn)
        
        if financial_df.empty:
            print("  ⚠️ 資料庫中無財報數據，使用預設值")
            df['rd_ratio'] = 0.0
            df['op_profit_margin'] = 0.0
            df['eps'] = 0.0
            return df
        
        print(f"  ✓ 從資料庫載入 {len(financial_df)} 檔股票的財報數據")
        
        # 合併數據（left join，保留所有日線數據）
        # [V35 升級] 新增 op_profit_margin 和 operating_profit 欄位
        df = df.merge(
            financial_df[['stock_id', 'rd_ratio', 'op_profit_margin', 'operating_profit', 'eps', 'year', 'quarter']],
            on='stock_id',
            how='left'
        )
        
        # 填補缺失值（無財報數據的股票）
        df['rd_ratio'] = df['rd_ratio'].fillna(0.0)
        df['op_profit_margin'] = df['op_profit_margin'].fillna(0.0)
        df['operating_profit'] = df['operating_profit'].fillna(0)
        df['eps'] = df['eps'].fillna(0.0)
        
        # 統計
        has_rd = (df['rd_ratio'] > 0).sum()
        has_op = (df['operating_profit'] != 0).sum()
        has_eps = (df['eps'] != 0).sum()
        
        print(f"  ✓ 有研發費用：{has_rd} 檔 ({has_rd/len(df)*100:.1f}%)")
        print(f"  ✓ 有營業利益：{has_op} 檔 ({has_op/len(df)*100:.1f}%) [V35 新功能]")
        print(f"  ✓ 有 EPS 數據：{has_eps} 檔 ({has_eps/len(df)*100:.1f}%)")
        
        return df
        
    except Exception as e:
        print(f"  ❌ 合併財報數據失敗: {e}")
        # 失敗時填補預設值
        df['rd_ratio'] = 0.0
        df['op_profit_margin'] = 0.0
        df['eps'] = 0.0
        return df


def merge_revenue_data(df: pd.DataFrame, engine) -> pd.DataFrame:
    """
    合併月營收資料到日線數據 [V35: 供 V34/V35 策略使用]
    
    從 monthly_revenue 表讀取每檔股票最新月營收的 YoY 增長率，
    合併至日線 DataFrame 以供 V34 Turbo (revenue_yoy > 30%) 等策略篩選。
    
    Args:
        df: 日線資料 DataFrame（需含 stock_id 欄位）
        engine: 資料庫引擎
    
    Returns:
        合併後的 DataFrame（新增 revenue_yoy 欄位）
    """
    try:
        # 讀取每檔股票最新的月營收 YoY
        query = text("""
            SELECT mr1.stock_id, mr1.revenue_yoy, mr1.revenue, mr1.year, mr1.month
            FROM monthly_revenue mr1
            INNER JOIN (
                SELECT stock_id, MAX(year * 100 + month) as max_period
                FROM monthly_revenue
                GROUP BY stock_id
            ) mr2 ON mr1.stock_id = mr2.stock_id 
                 AND (mr1.year * 100 + mr1.month) = mr2.max_period
        """)
        
        with engine.connect() as conn:
            revenue_df = pd.read_sql(query, conn)
        
        if revenue_df.empty:
            print("  ⚠️ 月營收資料表為空，revenue_yoy 填為 0")
            df['revenue_yoy'] = 0.0
            return df
        
        print(f"  ✓ 從 monthly_revenue 載入 {len(revenue_df)} 檔股票的月營收")
        
        # 移除舊的 revenue_yoy 欄位（如果存在，避免 merge 衝突）
        if 'revenue_yoy' in df.columns:
            df = df.drop(columns=['revenue_yoy'])
            print(f"  ℹ️  已移除舊的 revenue_yoy 欄位")
        
        # 合併月營收 YoY
        df = df.merge(
            revenue_df[['stock_id', 'revenue_yoy']],
            on='stock_id',
            how='left'
        )
        
        # 填補缺失值
        df['revenue_yoy'] = df['revenue_yoy'].fillna(0.0)
        
        has_yoy = (df['revenue_yoy'] != 0).sum()
        print(f"  ✓ 有營收 YoY：{has_yoy} 檔 ({has_yoy/len(df)*100:.1f}%)")
        
        # 🔥 V35: 將 revenue_yoy 寫回 daily_market_data
        _write_revenue_yoy_to_db(df, engine)
        
        return df
        
    except Exception as e:
        print(f"  ⚠️ 合併月營收失敗（可能表不存在）: {e}")
        df['revenue_yoy'] = 0.0
        return df


def _write_revenue_yoy_to_db(df: pd.DataFrame, engine):
    """
    將 revenue_yoy 寫回 daily_market_data 表
    
    🔥 V35 新增: 讓 V34/V35 回測能正確讀取營收 YoY 數據
    """
    if df.empty or 'revenue_yoy' not in df.columns:
        return
    
    try:
        # 只取最新日期的資料
        date_val = df['trade_date'].iloc[0]
        date_str = date_val.strftime('%Y-%m-%d') if hasattr(date_val, 'strftime') else str(date_val)
        
        # 準備更新資料
        update_df = df[['stock_id', 'revenue_yoy']].copy()
        update_df['revenue_yoy'] = pd.to_numeric(update_df['revenue_yoy'], errors='coerce').fillna(0)
        
        # 過濾掉 YoY=0 的資料（減少更新量）
        update_df = update_df[update_df['revenue_yoy'] != 0]
        
        if update_df.empty:
            print(f"  ⚠️ 無有效 revenue_yoy 需寫入")
            return
        
        with engine.connect() as conn:
            # 批次更新
            updated = 0
            batch_size = 500
            stock_list = update_df.to_dict('records')
            
            for i in range(0, len(stock_list), batch_size):
                batch = stock_list[i:i + batch_size]
                for row in batch:
                    conn.execute(text("""
                        UPDATE daily_market_data 
                        SET revenue_yoy = :yoy
                        WHERE stock_id = :sid AND trade_date = :dt
                    """), {'yoy': row['revenue_yoy'], 'sid': row['stock_id'], 'dt': date_str})
                conn.commit()
                updated += len(batch)
            
            print(f"  ✓ 已回寫 {updated} 檔 revenue_yoy 到 daily_market_data")
    
    except Exception as e:
        print(f"  ⚠️ 回寫 revenue_yoy 失敗: {e}")


def load_strategy_model(strategy_name: str):
    """Load strategy model with fallback."""
    model, features, model_path, used_fallback = load_model(
        strategy_name=strategy_name,
        allow_fallback=True,
        require_predict_proba=True,
    )
    if model:
        tag = "fallback" if used_fallback else "primary"
        print(f"[AI] model ({tag}) {strategy_name}: {os.path.basename(model_path)}")
        return model, features

    print(f"[AI] model not found for {strategy_name}")
    return None, None

def run_strategy(strategy, df, date_str, engine):
    """執行單一策略的選股流程（自動載入策略專屬模型）"""
    print(f"\n{'='*40}")
    print(f"📊 執行策略: {strategy.display_name} ({strategy.name})")
    print(f"🎯 目標報酬: {strategy.target_return}%")
    print(f"⏰ 持有天數: {strategy.look_ahead_days}天")
    print(f"{'='*40}")
    
    # 策略篩選
    candidates = strategy.filter_candidates(df.copy())
    print(f"✅ 篩選出 {len(candidates)} 檔候選股票")
    
    if candidates.empty:
        print(f"💤 {strategy.name}: 今日無符合條件的股票")
        return candidates
    
    # 載入此策略專屬的 AI 模型
    model, model_features = load_strategy_model(strategy.name)
    
    # AI 預測
    if model:
        # 使用模型記錄的特徵（優先）或策略定義的特徵
        features = model_features if model_features else strategy.features
        for f in features:
            if f not in candidates.columns:
                candidates[f] = 0
        
        X = candidates[features].fillna(0)
        candidates['ai_score'] = model.predict_proba(X)[:, 1]
        candidates = candidates.sort_values('ai_score', ascending=False)
        print(f"✅ AI 評分完成（特徵數: {len(features)}）")

    # 新聞情緒雙向加減分
    if Config.NEWS_BOOST_ENABLED and 'ai_score' in candidates.columns:
        try:
            from core.db_helper import get_stock_sector
            bull_sectors = _news_boost_cache.get('bull_sectors', [])
            bear_sectors = _news_boost_cache.get('bear_sectors', [])
            bull_theme_map = _news_boost_cache.get('bull_theme_map', {})
            bear_theme_map = _news_boost_cache.get('bear_theme_map', {})
            sentiment    = _news_boost_cache.get('sentiment', '中性')

            if bull_sectors or bear_sectors:
                bull_factor   = min(Config.NEWS_BOOST_FACTOR, Config.NEWS_BOOST_MAX)
                bear_factor   = Config.NEWS_PENALTY_FACTOR
                boosted = penalized = 0
                candidates['ai_score']         = candidates['ai_score'].astype(float)
                candidates['news_boost_reason'] = ''

                for idx, row in candidates.iterrows():
                    sector = get_stock_sector(row['stock_id'])
                    reason_parts = []

                    if sector in bull_sectors:
                        candidates.at[idx, 'ai_score'] *= (1 + bull_factor)
                        topic = bull_theme_map.get(sector, '消息偏多')
                        reason_parts.append(f"{sector}受惠: {topic}")
                        boosted += 1

                    # 個股層級新聞加分（Phase 2）
                    stock_news = _stock_news_cache.get(str(row['stock_id']))
                    if stock_news and stock_news.get('score', 0) > 0:
                        extra = min(bull_factor, Config.NEWS_BOOST_MAX - bull_factor)
                        candidates.at[idx, 'ai_score'] *= (1 + extra)
                        reason_parts.append(f"個股: {stock_news['reason']}")
                    elif stock_news and stock_news.get('score', 0) < 0:
                        candidates.at[idx, 'ai_score'] *= (1 - bear_factor)
                        reason_parts.append(f"個股利空: {stock_news['reason']}")

                    if sector in bear_sectors:
                        candidates.at[idx, 'ai_score'] *= (1 - bear_factor)
                        topic = bear_theme_map.get(sector, '消息偏空')
                        reason_parts.append(f"{sector}承壓: {topic}")
                        penalized += 1

                    if reason_parts:
                        candidates.at[idx, 'news_boost_reason'] = '\uff5c'.join(reason_parts)[:100]

                candidates = candidates.sort_values('ai_score', ascending=False)
                if boosted > 0:
                    print(f"  📈 新聞加分: {boosted} 檔屬於 {bull_sectors}（+{bull_factor:.0%}）")
                if penalized > 0:
                    print(f"  📉 新聞折減: {penalized} 檔屬於 {bear_sectors}（-{bear_factor:.0%}）")
        except Exception as e:
            print(f"  ⚠️ 新聞加減分失敗（不影響選股）: {e}")
        finally:
            if 'news_boost_reason' not in candidates.columns:
                candidates['news_boost_reason'] = ''
    else:
        candidates['news_boost_reason'] = ''

    # 存入資料庫
    try:
        with engine.connect() as conn:
            # 清除此策略的舊資料
            conn.execute(text("""
                DELETE FROM daily_recommendations 
                WHERE trade_date = :date AND strategy = :strategy
            """), {"date": date_str, "strategy": strategy.name})
            conn.commit()
            
            # 插入新資料（含 news_boost_reason）
            for _, row in candidates.head(10).iterrows():
                ai_score = row.get('ai_score', None)
                reason   = row.get('news_boost_reason', '') or None
                conn.execute(text("""
                    INSERT INTO daily_recommendations 
                    (stock_id, trade_date, strategy, close_price, ai_score, rsi, volume, news_boost_reason)
                    VALUES (:stock_id, :date, :strategy, :price, :score, :rsi, :volume, :reason)
                """), {
                    "stock_id": row['stock_id'],
                    "date":     date_str,
                    "strategy": strategy.name,
                    "price":    row['close_price'],
                    "score":    float(ai_score) if ai_score is not None else None,
                    "rsi":      row.get('rsi', None),
                    "volume":   row.get('volume', None),
                    "reason":   reason,
                })
            conn.commit()
            print(f"✅ {strategy.name}: 資料已儲存")
    except Exception as e:
        print(f"⚠️ {strategy.name}: 資料庫寫入失敗: {e}")
    
    # 顯示結果
    print(f"\n🎯 {strategy.display_name} 推薦 (Top 5):")
    for i, (_, row) in enumerate(candidates.head(5).iterrows(), 1):
        score_str = f"AI: {row['ai_score']:.2%}" if 'ai_score' in row else "N/A"
        # [V35 升級] 顯示營業利益率 (OpMg)
        op_margin_str = f"OpMg: {row.get('op_profit_margin', 0):.1%}"
        print(f"  {i}. {row['stock_id']} (${row['close_price']:.2f}) - {score_str} - {op_margin_str}")
    
    return candidates


def main():
    print("\n" + "="*50)
    print("🤖 Stock AI 每日選股執行中...")
    print("="*50 + "\n")
    
    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategies = manager.get_active_strategies()
    strategy_names = manager.get_active_strategy_names()
    
    print(f"📊 啟用策略數量: {len(strategies)}")
    print(f"📋 策略列表: {', '.join(strategy_names)}\n")
    
    # 2. 載入資料
    print("📂 載入股市資料...")
    engine = get_db_engine()

    # 取得最新交易日期
    from core.db_helper import get_latest_trade_date
    latest_date = get_latest_trade_date()
    if not latest_date:
        print("❌ 無資料可用")
        return
    date_str = latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') else str(latest_date)
    print(f"✅ 資料日期: {date_str}")

    # 3. 從歷史資料計算完整技術指標（核心步驟）
    df = compute_indicators_from_history(date_str, engine)
    if df.empty:
        print("❌ 計算指標後無資料")
        return
    print(f"✅ 總計 {len(df)} 檔股票（含完整技術指標）\n")
    
    # 4. 計算比率特徵
    print("🔧 計算比率特徵...")
    df = calculate_ratio_features(df)
    print("✅ 特徵計算完成\n")
    
    # 5. [V35 升級] 合併財報數據（含營業利益率）
    print("🧪 合併財報數據（含營業利益）...")
    df = merge_financial_data(df, engine)
    print("✅ 財報數據合併完成\n")
    
    # 6. [V35 升級] 合併月營收 YoY（供 V34/V35 策略使用）
    print("💰 合併月營收資料...")
    df = merge_revenue_data(df, engine)
    print("✅ 月營收合併完成\n")
    
    # 7. 新聞族群分析（全策略共用，只呼叫一次 Gemini）
    global _news_boost_cache, _stock_news_cache
    _news_boost_cache = {
        "bull_sectors": [], "bear_sectors": [],
        "bull_reasons": [], "bear_reasons": [],
        "bull_theme_map": {}, "bear_theme_map": {},
        "sentiment": "中性"
    }
    _stock_news_cache = {}
    if Config.NEWS_BOOST_ENABLED:
        try:
            from core.news_agent import get_news_sector_boost
            from core.db_helper import ensure_news_schema, save_news_sentiment
            # 確保 DB schema 就緒
            ensure_news_schema(engine)
            _news_boost_cache = get_news_sector_boost()
            bull = _news_boost_cache.get('bull_sectors', [])
            bear = _news_boost_cache.get('bear_sectors', [])
            bull_reasons = _news_boost_cache.get('bull_reasons', [])
            bear_reasons = _news_boost_cache.get('bear_reasons', [])
            bull_theme_map = _news_boost_cache.get('bull_theme_map', {})
            bear_theme_map = _news_boost_cache.get('bear_theme_map', {})
            sent = _news_boost_cache.get('sentiment', '中性')
            if bull:
                print(
                    f"📈 利多族群: {bull} | 📉 利空族群: {bear} | "
                    f"利多主題: {bull_theme_map} | 利空主題: {bear_theme_map} | "
                    f"利多重點: {bull_reasons} | 利空重點: {bear_reasons} | 情緒: {sent}"
                )
            else:
                print("📰 今日無明顯利多族群")
            # 儲存情緒到 DB
            save_news_sentiment(
                date_str, sent, bull, bear,
                bull_reasons, bear_reasons,
                bull_theme_map, bear_theme_map,
            )
        except Exception as e:
            print(f"⚠️ 新聞分析失敗（不影響選股）: {e}")

    # 7.5 個股層級新聞偵測（Phase 2：Yahoo 奇摩個股新聞）
    if Config.NEWS_BOOST_ENABLED:
        try:
            from core.news_agent import get_stock_news_mentions
            # 取得所有策略候選股的合集（最多 20 支，控制 API 成本）
            pre_ids = []
            for strat in strategies:
                try:
                    cands = strat.filter_candidates(df.copy())
                    if not cands.empty:
                        pre_ids.extend(cands['stock_id'].head(5).tolist())
                except Exception:
                    pass
            unique_ids = list(dict.fromkeys(pre_ids))[:20]
            if unique_ids:
                _stock_news_cache = get_stock_news_mentions(unique_ids)
        except Exception as e:
            print(f"⚠️ 個股新聞偵測失敗（不影響選股）: {e}")

    # 8. 遍歷所有策略執行選股（每策略動態載入專屬模型）
    all_results = {}
    for strategy in strategies:
        candidates = run_strategy(strategy, df, date_str, engine)
        all_results[strategy.name] = candidates
    
    # 9. 總結
    print("\n" + "="*50)
    print("📊 選股結果總覽:")
    print("="*50)
    for name, candidates in all_results.items():
        count = len(candidates) if not candidates.empty else 0
        print(f"  • {name}: {count} 檔候選股票")
    
    print("\n" + "="*50)
    print("✅ 今日選股作業完成！")
    print("="*50 + "\n")

if __name__ == "__main__":
    raise SystemExit(main())
