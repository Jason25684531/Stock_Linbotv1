import requests
import pandas as pd
import time
from sqlalchemy import text
from tool.db_helper import get_db_engine

def update_from_api():
    # 上市公司-綜合損益表-一般業
    URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_X_ci"
    print(f"🚀 [API] 正在連線證交所: {URL}")
    
    try:
        # 1. 抓取資料
        res = requests.get(URL, timeout=30)
        res.raise_for_status()
        data = res.json()
        
        if not data:
            print("❌ API 回傳空列表 []，無資料可更新。")
            return

        print(f"   取得 {len(data)} 筆原始資料，開始驗證品質...")
        
        # 2. 轉為 DataFrame
        df = pd.DataFrame(data)
        
        # === 🛡️ 防呆機制：過濾無效資料 ===
        # 檢查 '年度' 或 '季別' 是否為空字串
        if '年度' not in df.columns or '季別' not in df.columns:
            print("❌ 錯誤：API 缺少關鍵欄位 (年度/季別)")
            return

        # 去除空白資料列 (年度為空的列)
        df = df[df['年度'] != '']
        df = df[df['季別'] != '']
        
        if df.empty:
            print("❌ 警告：API 回傳的資料內容為空 (年度/季別皆為空值)。")
            print("   👉 可能原因：證交所資料庫正在維護，或目前非財報公布期間。")
            print("   👉 建議方案：請改用 '9_batch_import_financials.py' 手動匯入 CSV。")
            return
        
        print(f"   ✅ 有效資料共 {len(df)} 筆，開始處理...")

        # 3. 資料清洗與轉換
        final_df = pd.DataFrame()
        final_df['stock_id'] = df['公司代號']
        
        # 數值處理函數 (防爆)
        def clean_number(series):
            return pd.to_numeric(series.astype(str).str.replace(',', ''), errors='coerce').fillna(0)

        # 單位轉換: 千元 -> 元
        final_df['revenue'] = clean_number(df['營業收入']) * 1000
        final_df['eps'] = clean_number(df['基本每股盈餘（元）'])
        
        # 補上缺失的研發費用 (設為 0)
        final_df['rd_expense'] = 0 
        
        # 時間處理 (取第一筆有效的年份季度作為整批資料的時間)
        try:
            current_roc_year = int(df['年度'].iloc[0])
            current_quarter = int(df['季別'].iloc[0])
            
            final_df['year'] = current_roc_year + 1911
            final_df['quarter'] = current_quarter
            
            print(f"   📅 識別財報時間: 民國{current_roc_year}年 Q{current_quarter}")
            
        except Exception as e:
            print(f"❌ 日期解析失敗: {e}")
            return

        # 4. 寫入資料庫
        engine = get_db_engine()
        with engine.begin() as conn:
            # 先刪除該季度舊資料
            print(f"   🧹 清除資料庫舊資料 ({final_df['year'].iloc[0]} Q{final_df['quarter'].iloc[0]})...")
            conn.execute(
                text("DELETE FROM financial_statements WHERE year = :y AND quarter = :q"),
                {'y': final_df['year'].iloc[0], 'q': final_df['quarter'].iloc[0]}
            )
            
            # 寫入新資料
            print(f"   💾 正在寫入 {len(final_df)} 筆資料...")
            final_df.to_sql('financial_statements', conn, if_exists='append', index=False, chunksize=2000)
            
        print(f"✅ API 更新成功！(注意：此模式下研發費用皆為 0，V35 策略將自動調整邏輯)")
        
    except Exception as e:
        print(f"❌ 更新發生未預期錯誤: {e}")

if __name__ == "__main__":
    update_from_api()