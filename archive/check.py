import pandas as pd
from sqlalchemy import text
from tool.db_helper import get_db_engine

def check_status():
    print("📊 正在檢查資料庫狀態 (financial_statements)...")
    
    engine = get_db_engine()
    
    try:
        with engine.connect() as conn:
            # 1. 查詢總筆數
            result = conn.execute(text("SELECT COUNT(*) FROM financial_statements"))
            total_count = result.scalar()
            print(f"✅ 資料庫總筆數：{total_count} 筆")
            
            if total_count == 0:
                print("⚠️ 目前資料庫是空的！請執行匯入腳本。")
                return

            # 2. 依照年份與季度分組統計
            query = text("""
                SELECT year, quarter, COUNT(*) as count 
                FROM financial_statements 
                GROUP BY year, quarter 
                ORDER BY year DESC, quarter DESC
            """)
            
            df = pd.read_sql(query, conn)
            
            print("\n📅 各季度資料統計：")
            print("-" * 30)
            print(f"{'年份 (Year)':<10} | {'季度 (Q)':<8} | {'筆數 (Count)':<10}")
            print("-" * 30)
            
            for index, row in df.iterrows():
                print(f"{int(row['year']):<12} | Q{int(row['quarter']):<7} | {row['count']:<10}")
            print("-" * 30)
            
            # 3. 隨機抽樣檢查 (MySQL 使用 RAND())
            print("\n👀 隨機抽樣 3 筆資料確認內容：")
            sample_query = text("SELECT stock_id, year, quarter, revenue, eps, rd_expense FROM financial_statements ORDER BY RAND() LIMIT 3")
            sample_df = pd.read_sql(sample_query, conn)
            print(sample_df.to_string(index=False))

    except Exception as e:
        print(f"❌ 查詢失敗: {e}")

if __name__ == "__main__":
    check_status()