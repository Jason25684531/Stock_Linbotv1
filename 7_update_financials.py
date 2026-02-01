"""
季報財務數據更新腳本
============================================
功能：定期從 MOPS 抓取季報數據並更新資料庫
執行方式：python 7_update_financials.py [year] [quarter]
範例：python 7_update_financials.py 113 3  (抓取 2024 Q3)
"""
import sys
import argparse
from datetime import datetime
from sqlalchemy import text
from tool.crawlers.quarterly_scraper import QuarterlyScraper
from tool.db_helper import get_db_engine


def get_current_quarter() -> tuple:
    """
    取得當前季度
    
    Returns:
        (民國年, 季度)
    """
    now = datetime.now()
    west_year = now.year
    roc_year = west_year - 1911  # 轉民國年
    
    # 季度對應月份
    month = now.month
    if 1 <= month <= 3:
        quarter = 4  # Q4 通常在隔年 3 月公布
        roc_year -= 1  # 使用前一年
    elif 4 <= month <= 5:
        quarter = 1  # Q1 在 5 月公布
    elif 6 <= month <= 8:
        quarter = 2  # Q2 在 8 月公布
    elif 9 <= month <= 11:
        quarter = 3  # Q3 在 11 月公布
    else:  # 12月
        quarter = 3  # 還在等 Q3
    
    return roc_year, quarter


def upsert_financial_data(df, engine):
    """
    Upsert 財報數據到資料庫
    
    Args:
        df: pandas DataFrame
        engine: SQLAlchemy Engine
    
    Returns:
        成功筆數
    """
    if df.empty:
        print("⚠️ 無資料需要更新")
        return 0
    
    success_count = 0
    
    with engine.connect() as conn:
        for _, row in df.iterrows():
            try:
                sql = text("""
                    INSERT INTO financial_statements 
                    (stock_id, year, quarter, revenue, rd_expense, eps)
                    VALUES (:stock_id, :year, :quarter, :revenue, :rd_expense, :eps)
                    ON DUPLICATE KEY UPDATE
                        revenue = VALUES(revenue),
                        rd_expense = VALUES(rd_expense),
                        eps = VALUES(eps),
                        updated_at = CURRENT_TIMESTAMP
                """)
                
                conn.execute(sql, {
                    'stock_id': row['stock_id'],
                    'year': int(row['year']),
                    'quarter': int(row['quarter']),
                    'revenue': int(row['revenue']) if row['revenue'] is not None else None,
                    'rd_expense': int(row['rd_expense']) if row['rd_expense'] is not None else None,
                    'eps': float(row['eps']) if row['eps'] is not None else None,
                })
                success_count += 1
                
            except Exception as e:
                print(f"❌ 插入失敗 ({row['stock_id']}): {e}")
        
        conn.commit()
    
    return success_count


def update_financials(year: int = None, quarter: int = None, auto_detect: bool = True):
    """
    更新財報數據主函數
    
    Args:
        year: 民國年 (None = 自動偵測)
        quarter: 季度 (None = 自動偵測)
        auto_detect: 是否自動偵測最新季度
    """
    print("=" * 70)
    print("  📊 季報財務數據更新程式")
    print("=" * 70)
    
    # 自動偵測季度
    if auto_detect and (year is None or quarter is None):
        year, quarter = get_current_quarter()
        print(f"\n🔍 自動偵測：{year} 年 Q{quarter} (最新可取得季度)")
    
    # 驗證參數
    if year is None or quarter is None:
        print("❌ 請提供年度和季度")
        return False
    
    if not (1 <= quarter <= 4):
        print(f"❌ 季度必須介於 1-4，得到: {quarter}")
        return False
    
    # 初始化爬蟲
    print(f"\n🚀 開始抓取 {year} 年 Q{quarter} 財報...")
    scraper = QuarterlyScraper(retry_count=3, delay_range=(3.0, 6.0))
    
    # 抓取資料
    df = scraper.fetch_all_markets(year, quarter)
    
    if df.empty:
        print("\n❌ 未能抓取任何資料")
        print("\n💡 可能原因：")
        print("  1. 該季度財報尚未公布")
        print("  2. MOPS 網站暫時無法存取")
        print("  3. 網站結構已變更")
        return False
    
    print(f"\n✅ 成功抓取 {len(df)} 檔股票")
    
    # 數據統計
    print("\n📈 數據統計：")
    print(f"  • 有研發費用：{(df['rd_expense'] > 0).sum()} 檔")
    print(f"  • 平均 EPS：{df['eps'].mean():.2f} 元")
    print(f"  • 營收中位數：{df['revenue'].median() / 1e6:.1f} 百萬元")
    
    # 寫入資料庫
    print(f"\n💾 寫入資料庫...")
    engine = get_db_engine()
    
    try:
        success_count = upsert_financial_data(df, engine)
        print(f"✅ 成功更新 {success_count} 筆資料")
        
        # 檢查資料庫狀態
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT 
                    year, quarter, 
                    COUNT(*) as stock_count,
                    SUM(CASE WHEN rd_expense > 0 THEN 1 ELSE 0 END) as rd_count
                FROM financial_statements
                WHERE year = :year AND quarter = :quarter
                GROUP BY year, quarter
            """), {'year': year, 'quarter': quarter})
            
            row = result.fetchone()
            if row:
                print(f"\n📊 資料庫狀態：")
                print(f"  • {row[0]} 年 Q{row[1]}")
                print(f"  • 總股票數：{row[2]} 檔")
                print(f"  • 有研發費用：{row[3]} 檔")
        
        return True
        
    except Exception as e:
        print(f"❌ 資料庫操作失敗: {e}")
        return False


def main():
    """命令列介面"""
    parser = argparse.ArgumentParser(
        description='更新季報財務數據',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python 7_update_financials.py              # 自動偵測最新季度
  python 7_update_financials.py 113 3        # 指定 2024 Q3
  python 7_update_financials.py 112 4        # 指定 2023 Q4
        """
    )
    
    parser.add_argument('year', type=int, nargs='?', help='民國年 (例: 113)')
    parser.add_argument('quarter', type=int, nargs='?', help='季度 (1-4)')
    parser.add_argument('--no-auto', action='store_true', help='停用自動偵測')
    
    args = parser.parse_args()
    
    try:
        success = update_financials(
            year=args.year,
            quarter=args.quarter,
            auto_detect=not args.no_auto
        )
        
        if success:
            print("\n" + "=" * 70)
            print("  ✅ 更新完成！")
            print("=" * 70)
            print("\n💡 接下來可以：")
            print("  1. 執行 V35 策略：python 2_rundaily.py --strategy v35_innovation")
            print("  2. 查看資料庫：SELECT * FROM financial_statements LIMIT 10;")
            sys.exit(0)
        else:
            print("\n" + "=" * 70)
            print("  ❌ 更新失敗")
            print("=" * 70)
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 發生錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
