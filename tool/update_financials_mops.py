"""
單季財報更新工具 (MOPS 來源)
============================================
功能：抓取指定季度的財報並更新至資料庫
使用方式：python tool/update_financials_mops.py --year 112 --quarter 3
"""
import sys
import os
import argparse

# 將專案根目錄加入 Python 路徑，確保能 import tool 模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.crawlers.quarterly_scraper import QuarterlyScraper
from tool.db_helper import get_db_engine, ensure_financial_columns, upsert_financial_statements
from sqlalchemy import text
import pandas as pd


def update_quarter(year: int, quarter: int, dry_run: bool = False):
    """
    更新單一季度的財報數據
    
    Args:
        year: 民國年（如 112）
        quarter: 季度（1-4）
        dry_run: 是否為測試模式（不實際寫入資料庫）
    """
    print(f"\n{'='*60}")
    print(f"📊 財報更新工具 - V35 版本 (含營業費用/營業利益)")
    print(f"{'='*60}")
    print(f"📅 目標季度: 民國 {year} 年 Q{quarter} (西元 {year + 1911} 年)")
    print(f"🔧 模式: {'測試模式 (不寫入)' if dry_run else '正式模式'}")
    print(f"{'='*60}\n")
    
    # 1. 驗證參數
    if not (100 <= year <= 120):
        print("❌ 民國年份應介於 100-120")
        return False
    if not (1 <= quarter <= 4):
        print("❌ 季度應介於 1-4")
        return False
    
    # 2. 爬取資料
    scraper = QuarterlyScraper()
    df = scraper.fetch_all_markets(year, quarter)
    
    if df is None or df.empty:
        print("❌ 爬取失敗或無資料")
        return False
    
    print(f"\n✅ 成功爬取 {len(df)} 筆資料")
    print("\n📋 資料預覽（前 5 筆）:")
    print(df.head().to_string(index=False))
    
    if dry_run:
        print("\n⚠️ 測試模式結束，未寫入資料庫")
        return True
    
    # 3. 寫入資料庫
    print("\n📝 準備寫入資料庫...")
    engine = get_db_engine()
    
    try:
        west_year = year + 1911
        with engine.begin() as conn:
            ensure_financial_columns(conn)
            inserted_count = upsert_financial_statements(conn, df, west_year, quarter)
            print(f"   ✅ 新增/更新資料: {inserted_count} 筆")
        
        print(f"\n{'='*60}")
        print("✅ 更新完成！")
        print(f"{'='*60}\n")
        return True
        
    except Exception as e:
        print(f"\n❌ 資料庫寫入失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="更新指定季度的財報數據",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  # 更新 112 年 Q3
  python tool/update_financials_mops.py --year 112 --quarter 3
  
  # 測試模式（不寫入資料庫）
  python tool/update_financials_mops.py --year 112 --quarter 3 --dry-run
  
  # 使用預設值（年份和季度）
  python tool/update_financials_mops.py
        """
    )
    
    parser.add_argument('--year', type=int, default=113, help='民國年（預設 113）')
    parser.add_argument('--quarter', type=int, default=3, help='季度 1-4（預設 3）')
    parser.add_argument('--dry-run', action='store_true', help='測試模式，不實際寫入資料庫')
    
    args = parser.parse_args()
    
    success = update_quarter(args.year, args.quarter, args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
