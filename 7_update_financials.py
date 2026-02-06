import os
import pandas as pd
from sqlalchemy import bindparam, text
from tool.db_helper import get_db_engine

# 設定資料夾路徑
BASE_DIR = "Quarterly Financial Report"
FOLDERS = {
    "OTC listed": "上櫃",
    "Publicly listed": "上市"
}

def clean_bad_data(conn):
    print("🧹 [1/2] 清洗髒資料...")
    # 1. 刪除未來的幽靈資料 (2074)
    conn.execute(text("DELETE FROM financial_statements WHERE year > 2030"))
    # 2. 刪除忘記轉西元的資料 (113) - 假設正常的財報都是 2000 年以後
    conn.execute(text("DELETE FROM financial_statements WHERE year < 1911"))
    print("   ✅ 資料庫已淨身，準備重新匯入...")

def import_csv_safely(filepath, market_name):
    filename = os.path.basename(filepath)
    # print(f"   📄 處理檔案: {filename}") # 減少雜訊，只印重要訊息
    
    try:
        # 讀取 CSV
        try:
            df = pd.read_csv(filepath, encoding='cp950')
        except:
            df = pd.read_csv(filepath, encoding='utf-8')

        # 清理欄位
        df.columns = [c.strip() for c in df.columns]
        
        # === 核心修正：直接讀取 CSV 內容 ===
        if '年度' not in df.columns or '季別' not in df.columns:
            print(f"      ❌ 跳過 {filename}: 找不到 '年度' 或 '季別' 欄位")
            return

        # 取得年份與季度 (取第一列)
        first_row = df.iloc[0]
        roc_year = int(first_row['年度'])
        quarter = int(first_row['季別'])
        west_year = roc_year + 1911 # 轉西元
        
        print(f"      📅 識別成功: 民國{roc_year} -> 西元{west_year} Q{quarter} ({market_name})")

        # 欄位對應
        rename_map = {
            '公司代號': 'stock_id',
            '營業收入': 'revenue',
            '基本每股盈餘（元）': 'eps',
            '基本每股盈餘': 'eps',
            '研究發展費': 'rd_expense'
        }
        
        target_cols = [c for c in rename_map.keys() if c in df.columns]
        final_df = df[target_cols].rename(columns=rename_map)
        
        # 補缺值與轉型
        if 'rd_expense' not in final_df.columns:
            final_df['rd_expense'] = 0 # 簡表沒研發費，補 0
            
        for col in ['revenue', 'eps', 'rd_expense']:
            if col in final_df.columns:
                final_df[col] = pd.to_numeric(
                    final_df[col].astype(str).str.replace(',', '').replace('--', '0'), 
                    errors='coerce'
                ).fillna(0)

        # 單位轉換
        if 'revenue' in final_df.columns: final_df['revenue'] *= 1000
        if 'rd_expense' in final_df.columns: final_df['rd_expense'] *= 1000
        
        # 設定時間
        final_df['year'] = west_year
        final_df['quarter'] = quarter
        
        # 寫入資料庫
        engine = get_db_engine()
        with engine.begin() as conn:
            # 先刪除該季資料 (避免重複)
            delete_stmt = text(
                "DELETE FROM financial_statements WHERE year=:y AND quarter=:q AND stock_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            conn.execute(
                delete_stmt,
                {
                    'y': west_year,
                    'q': quarter,
                    'ids': final_df['stock_id'].astype(str).tolist()
                }
            )
            # 寫入
            final_df.to_sql('financial_statements', conn, if_exists='append', index=False, chunksize=2000)
            
        print(f"         ✅ 匯入 {len(final_df)} 筆")

    except Exception as e:
        print(f"      ❌ {filename} 處理失敗: {e}")

def main():
    # 先連線一次做清洗
    engine = get_db_engine()
    with engine.begin() as conn:
        clean_bad_data(conn)
        
    print("\n📂 [2/2] 開始重新匯入 (依據 CSV 內容判定年份)...")
    if not os.path.exists(BASE_DIR):
        print(f"❌ 找不到資料夾: {BASE_DIR}")
        return

    for folder_name, market_name in FOLDERS.items():
        folder_path = os.path.join(BASE_DIR, folder_name)
        if not os.path.exists(folder_path): continue
            
        files = [f for f in os.listdir(folder_path) if f.lower().endswith('.csv')]
        for file in files:
            import_csv_safely(os.path.join(folder_path, file), market_name)
            
    print("\n🎉 全部修正完成！請執行 check_db_status.py 確認結果。")

if __name__ == "__main__":
    main()
