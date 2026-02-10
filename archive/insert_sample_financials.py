"""
插入範例財報數據（供測試使用）
============================================
在無法從 MOPS 抓取資料時，可用此腳本產生測試數據
"""
import random
from sqlalchemy import text
from tool.db_helper import get_db_engine


def insert_sample_financials():
    """插入範例財報數據"""
    engine = get_db_engine()
    
    # 取得現有股票代號
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT DISTINCT stock_id 
            FROM daily_market_data 
            WHERE stock_id NOT IN ('0050', '0056', '00632R', '00878')
            ORDER BY stock_id
            LIMIT 100
        """))
        stock_ids = [row[0] for row in result]
    
    if not stock_ids:
        print("❌ 資料庫中無股票資料")
        return
    
    print(f"📊 準備為 {len(stock_ids)} 檔股票插入範例財報...")
    
    success_count = 0
    
    with engine.connect() as conn:
        for stock_id in stock_ids:
            # 產生隨機但合理的財報數據
            revenue = random.randint(100000, 10000000) * 1000  # 0.1億 - 100億
            rd_ratio = random.uniform(0, 0.15)  # 0-15% 研發費用率
            rd_expense = int(revenue * rd_ratio)
            eps = random.uniform(-2.0, 10.0)  # EPS -2 ~ 10 元
            
            try:
                sql = text("""
                    INSERT INTO financial_statements 
                    (stock_id, year, quarter, revenue, rd_expense, eps)
                    VALUES (:stock_id, :year, :quarter, :revenue, :rd_expense, :eps)
                    ON DUPLICATE KEY UPDATE
                        revenue = VALUES(revenue),
                        rd_expense = VALUES(rd_expense),
                        eps = VALUES(eps)
                """)
                
                # 插入 113 Q3 (2024 Q3) 的資料
                conn.execute(sql, {
                    'stock_id': stock_id,
                    'year': 113,
                    'quarter': 3,
                    'revenue': revenue,
                    'rd_expense': rd_expense,
                    'eps': round(eps, 2)
                })
                success_count += 1
                
            except Exception as e:
                print(f"❌ {stock_id}: {e}")
        
        conn.commit()
    
    print(f"✅ 成功插入 {success_count} 筆範例財報")
    
    # 顯示統計
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN rd_expense > 0 THEN 1 ELSE 0 END) as has_rd,
                AVG(eps) as avg_eps
            FROM financial_statements
            WHERE year = 113 AND quarter = 3
        """))
        row = result.fetchone()
        
        print("\n📊 資料庫統計 (113 Q3):")
        print(f"  • 總筆數: {row[0]}")
        print(f"  • 有研發費用: {row[1]} 檔 ({row[1]/row[0]*100:.1f}%)")
        print(f"  • 平均 EPS: {row[2]:.2f} 元")


if __name__ == '__main__':
    print("=" * 60)
    print("  📊 插入範例財報數據 (測試用)")
    print("=" * 60)
    print("\n⚠️ 注意：這是隨機產生的測試數據，非真實財報\n")
    
    response = input("確定要繼續嗎？(y/N): ")
    if response.lower() == 'y':
        insert_sample_financials()
        print("\n✅ 完成！現在可以執行 2_rundaily.py 測試 V35 策略")
    else:
        print("❌ 已取消")
