import pandas as pd
import numpy as np
from sqlalchemy import text
from config import Config
import time
from datetime import datetime
from tool.db_helper import get_db_engine

# 引入您剛寫好的爬蟲與指標計算
try:
    from tool.calc_indicators import calculate_natr, calculate_std
    # 這裡假設您的爬蟲函式放在 1_update_database.py 或類似位置，如果沒有，請確保引入路徑正確
    # 為了方便，這裡直接定義一個簡單的爬蟲 wrapper，或者您需要確認 fetch_revenue_v4_smart 所在位置
    import sys
    sys.path.append('.')
    from one_update_database import fetch_revenue_v4_smart # 假設檔名是 1_update_database.py (Python變數不能數字開頭，需確認import方式)
except ImportError:
    # 如果找不到，這裡提供一個 placeholder 避免腳本崩潰
    print("⚠️ 警告：找不到 fetch_revenue_v4_smart，將跳過爬蟲測試")
    def fetch_revenue_v4_smart(y, m): return pd.DataFrame()

def fix_schema():
    print("\n======================================================================")
    print("                        資料庫結構修復工具 - V33 Phase 1 (MySQL修正版)")
    print("======================================================================")
    
    engine = get_db_engine()
    conn = engine.connect()
    
    # 1. 檢查並新增欄位
    print("\n步驟 1: 檢查資料庫結構")
    existing_cols = pd.read_sql("SELECT * FROM daily_market_data LIMIT 1", engine).columns.tolist()
    
    new_cols = {
        'revenue_yoy': 'FLOAT',
        'natr': 'FLOAT',
        'std_20': 'FLOAT'
    }
    
    for col, dtype in new_cols.items():
        if col not in existing_cols:
            print(f"  ✅ 新增欄位: {col}")
            try:
                conn.execute(text(f"ALTER TABLE daily_market_data ADD COLUMN {col} {dtype}"))
            except Exception as e:
                print(f"  ⚠️ 無法新增 (可能已存在): {e}")
        else:
            print(f"  ℹ️ 欄位已存在: {col}")

    # 2. 回填營收資料 (針對 MySQL 優化 SQL)
    print("\n步驟 2: 回填營收資料 (最近 3 個月)")
    current_date = datetime.now()
    targets = [
        (current_date.year, current_date.month),
        (current_date.year, current_date.month - 1) if current_date.month > 1 else (current_date.year - 1, 12),
        (current_date.year, current_date.month - 2) if current_date.month > 2 else (current_date.year - 1, 12 + current_date.month - 2)
    ]
    
    # 修正：針對 1_update_database.py 的引用
    # 嘗試動態 import
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("module.name", "1_update_database.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fetch_func = mod.fetch_revenue_v4_smart
    except:
        print("⚠️ 無法載入 1_update_database.py，跳過爬蟲部分")
        fetch_func = None

    if fetch_func:
        for year, month in targets:
            print(f"\n📅 處理 {year}年{month}月...")
            try:
                df_rev = fetch_func(year, month)
                # 🔥 修復：檢查 None 以避免 'NoneType' object has no attribute 'empty'
                if df_rev is not None and not df_rev.empty:
                    print(f"  ✅ 抓到 {len(df_rev)} 筆營收資料，開始寫入...")
                    
                    # 轉換為 SQL 參數列表
                    params = []
                    for _, row in df_rev.iterrows():
                        params.append({
                            'yoy': row['revenue_yoy'],
                            'stock_id': str(row['stock_id']),
                            'year_month': f"{year}-{month:02d}"
                        })
                    
                    # 🔥 MySQL 專用 SQL 語法 (使用 DATE_FORMAT) 🔥
                    update_sql = text("""
                        UPDATE daily_market_data
                        SET revenue_yoy = :yoy
                        WHERE stock_id = :stock_id
                        AND DATE_FORMAT(trade_date, '%Y-%m') = :year_month
                    """)
                    
                    # 為了避免一次鎖死資料庫，分批執行
                    batch_size = 1000
                    for i in range(0, len(params), batch_size):
                        batch = params[i:i+batch_size]
                        conn.execute(update_sql, batch)
                        conn.commit()
                        print(f"    已更新 {min(i+batch_size, len(params))} / {len(params)} 筆")
                else:
                    print("  ⚠️ 無資料")
            except Exception as e:
                print(f"  ❌ 處理失敗: {e}")

    # 3. 重新計算技術指標 (解決 Duplicate Labels 問題)
    print("\n步驟 3: 重新計算技術指標")
    print("📖 讀取資料庫 (這可能需要一點時間)...")
    
    # 讀取必要欄位即可，節省記憶體
    df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    print(f"✅ 已載入 {len(df)} 筆記錄")
    
    # 🔥 關鍵修正：去除重複資料 🔥
    print("🧹 正在檢查並清除重複資料...")
    before_len = len(df)
    df = df.drop_duplicates(subset=['stock_id', 'trade_date'], keep='last')
    after_len = len(df)
    if before_len > after_len:
        print(f"⚠️ 發現重複資料！已移除 {before_len - after_len} 筆重複記錄。")
        # 這裡建議把清洗後的資料寫回資料庫，但為了安全起見，我們先只在記憶體中處理指標
        # 若要根治，需要執行 DELETE SQL 清除重複項
    
    # 確保格式正確
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['stock_id', 'trade_date'])
    
    # 轉數字
    for col in ['open_price', 'high_price', 'low_price', 'close_price']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    print("📊 計算 NATR 與 STD_20...")
    
    # 使用 GroupBy Apply 計算
    # 定義一個計算函數，避免 lambda 造成索引混亂
    def calc_new_indicators(g):
        # 引用 calc_indicators.py 裡的邏輯
        # 如果引用失敗，這裡手寫邏輯
        close = g['close_price']
        high = g['high_price']
        low = g['low_price']
        prev_close = close.shift(1)
        
        # ATR
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean()
        
        # NATR
        g['natr'] = (atr / close) * 100
        
        # STD_20
        g['std_20'] = close.rolling(20).std()
        
        return g

    # group_keys=False 避免多層索引
    df = df.groupby('stock_id', group_keys=False).apply(calc_new_indicators)
    
    # 填補空值
    df['natr'] = df['natr'].fillna(0)
    df['std_20'] = df['std_20'].fillna(0)
    
    # 🔥 修復：清理 inf 和 -inf 值（MySQL 無法處理）
    print("🧹 清理無效數值 (inf/-inf)...")
    
    # 檢查並報告 inf 數量
    natr_inf_count = np.isinf(df['natr']).sum()
    std_inf_count = np.isinf(df['std_20']).sum()
    
    if natr_inf_count > 0 or std_inf_count > 0:
        print(f"  ⚠️  發現無效數值: NATR 有 {natr_inf_count} 筆 inf, STD_20 有 {std_inf_count} 筆 inf")
        print(f"  ✅ 正在將 inf/-inf 替換為 0...")
    
    # 將 inf 和 -inf 替換為 0
    df['natr'] = df['natr'].replace([np.inf, -np.inf], 0)
    df['std_20'] = df['std_20'].replace([np.inf, -np.inf], 0)
    
    # 限制極端值 (額外安全措施)
    df['natr'] = df['natr'].clip(0, 100)  # NATR 不太可能超過 100%
    df['std_20'] = df['std_20'].clip(0, df['close_price'].max() * 0.5)  # STD 不應超過股價的 50%
    
    print("💾 正在寫回資料庫 (Update 模式)...")
    
    # 為了避免寫回時因為 Primary Key 衝突 (如果之前沒清乾淨)，我們使用 replace 模式寫入一個暫存表，然後用 SQL 更新
    # 這裡為了簡單且安全，我們採用「更新欄位」的方式
    # 但因為 pandas to_sql replace 會刪除整個表，這很危險。
    # 最穩健的方法是：用 sqlalchemy 逐筆更新 (太慢) 或 使用臨時表。
    
    # 這裡我們採用：將計算好指標的 DataFrame，針對這兩個欄位進行大量更新
    # 為了效能，我們只寫入最近 60 天的資料就好 (舊資料的波動率對策略影響較小)
    
    recent_date = df['trade_date'].max() - pd.Timedelta(days=90)
    df_update = df[df['trade_date'] >= recent_date][['stock_id', 'trade_date', 'natr', 'std_20']].copy()
    
    print(f"  👉 僅更新 {recent_date.date()} 之後的資料 (共 {len(df_update)} 筆) 以節省時間")
    
    # 最後確認：確保沒有 inf 值
    df_update['natr'] = df_update['natr'].replace([np.inf, -np.inf], 0)
    df_update['std_20'] = df_update['std_20'].replace([np.inf, -np.inf], 0)
    
    # 建立臨時表
    df_update.to_sql('temp_indicators', engine, if_exists='replace', index=False)
    
    # 執行 SQL Update (MySQL 語法)
    update_indicator_sql = text("""
        UPDATE daily_market_data d
        JOIN temp_indicators t ON d.stock_id = t.stock_id AND d.trade_date = t.trade_date
        SET d.natr = t.natr, d.std_20 = t.std_20
    """)
    
    with engine.begin() as trans:
        trans.execute(update_indicator_sql)
        trans.execute(text("DROP TABLE temp_indicators"))
    
    print("✅ 技術指標更新完成")

    # 4. 驗證
    print("\n步驟 4: 驗證資料完整性")
    final_check = pd.read_sql("SELECT count(*) as cnt FROM daily_market_data WHERE natr > 0", engine)
    print(f"📊 有效 NATR 筆數: {final_check.iloc[0]['cnt']}")
    
    print("\n🎉 修復作業結束！")

if __name__ == "__main__":
    fix_schema()