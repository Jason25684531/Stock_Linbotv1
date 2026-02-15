"""
歷史財報批量更新工具
============================================
功能：批量爬取多年度/多季度的財報數據
注意：會在每次請求之間加入延遲避免被封鎖
使用方式：python tool/update_history_financials.py --start-year 110 --end-year 113

⚠️ 反爬蟲機制提醒:
- 請確認 QuarterlyScraper 類別有設定真實的 User-Agent (例如 Chrome/120.0)
- 建議在 crawlers/quarterly_scraper.py 中檢查 headers 設定
"""
import sys
import os
import time
import random
import argparse
from datetime import datetime

# 將專案根目錄加入路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.crawlers.quarterly_scraper import QuarterlyScraper
from tool.db_helper import get_db_engine, ensure_financial_columns, upsert_financial_statements
from sqlalchemy import text


def update_history(start_year: int, end_year: int, delay: int = 10):
    """
    批量更新歷史財報數據
    
    Args:
        start_year: 起始民國年（如 110）
        end_year: 結束民國年（如 113）
        delay: 每次請求之間的延遲秒數（預設 10 秒）
    """
    print(f"\n{'='*70}")
    print(f"📊 歷史財報批量更新工具 - V35 版本")
    print(f"{'='*70}")
    print(f"📅 更新範圍: 民國 {start_year}-{end_year} 年（共 {end_year - start_year + 1} 年）")
    print(f"⏱️ 請求延遲: {delay} 秒")
    print(f"⏰ 預計耗時: 約 {(end_year - start_year + 1) * 4 * (delay + 10) // 60} 分鐘")
    print(f"{'='*70}\n")
    
    # 1. 驗證參數
    if not (100 <= start_year <= end_year <= 120):
        print("❌ 年份範圍錯誤（應為 100-120 且 start_year <= end_year）")
        return False
    
    # 2. 初始化
    scraper = QuarterlyScraper()
    engine = get_db_engine()
    
    # 🔧 自動檢查並新增選填欄位（只執行一次）
    try:
        with engine.begin() as conn:
            ensure_financial_columns(conn)
    except Exception as e:
        print(f"⚠️ 欄位檢查錯誤（可能已存在）: {e}\n")
    
    total_tasks = (end_year - start_year + 1) * 4
    current_task = 0
    success_count = 0
    fail_count = 0
    
    start_time = datetime.now()
    
    # 3. 遍歷所有年份和季度
    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            current_task += 1
            west_year = year + 1911
            
            print(f"\n{'─'*70}")
            print(f"📌 進度: [{current_task}/{total_tasks}] 民國 {year} Q{quarter} (西元 {west_year})")
            print(f"{'─'*70}")
            
            # 🔄 智能重試機制：最多重試 3 次
            retry_count = 0
            max_retries = 3
            success = False
            
            while retry_count <= max_retries and not success:
                try:
                    # 爬取資料
                    df = scraper.fetch_all_markets(year, quarter)
                    
                    if df is None or df.empty:
                        print("⚠️ 無資料，跳過此季度")
                        fail_count += 1
                        break  # 無資料不算失敗，直接跳出重試循環
                    
                    # 寫入資料庫（使用共用函式）
                    with engine.begin() as conn:
                        inserted_count = upsert_financial_statements(conn, df, west_year, quarter)
                        print(f"  ✅ 成功寫入 {inserted_count} 筆")
                        success_count += 1
                        success = True  # 標記成功，跳出重試循環
                    
                except (ConnectionResetError, ConnectionAbortedError) as ce:
                    retry_count += 1
                    if retry_count <= max_retries:
                        # 🛡️ 偵測到連線封鎖，進入冷卻期
                        cooldown = 60 + random.uniform(10, 30)  # 60-90 秒冷卻
                        print(f"  ⚠️ 偵測到 IP 封鎖或連線重置 (嘗試 {retry_count}/{max_retries})")
                        print(f"  🧊 進入冷卻期 {cooldown:.1f} 秒...")
                        time.sleep(cooldown)
                        print(f"  🔄 重試第 {retry_count} 次...")
                    else:
                        print(f"  ❌ 已達最大重試次數，跳過此季度")
                        fail_count += 1
                
                except Exception as e:
                    print(f"  ❌ 錯誤: {e}")
                    fail_count += 1
                    break  # 其他錯誤不重試，直接跳出
            
            # 🎲 隨機延遲避免被封鎖（除非是最後一次請求）
            if current_task < total_tasks and success:
                jittered_delay = random.uniform(0.8 * delay, 1.5 * delay)
                print(f"  ⏱️ 休息 {jittered_delay:.1f} 秒避免被封鎖 (基準: {delay}s)...")
                time.sleep(jittered_delay)
    
    # 4. 統計報告
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    
    print(f"\n{'='*70}")
    print(f"📊 批量更新完成統計")
    print(f"{'='*70}")
    print(f"✅ 成功: {success_count}/{total_tasks} 個季度")
    print(f"❌ 失敗: {fail_count}/{total_tasks} 個季度")
    print(f"⏱️ 總耗時: {elapsed // 60:.0f} 分 {elapsed % 60:.0f} 秒")
    print(f"{'='*70}\n")
    
    return success_count > 0


def main():
    parser = argparse.ArgumentParser(
        description="批量更新歷史財報數據",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  # 更新 110-113 年所有季度（使用預設 10 秒延遲）
  python tool/update_history_financials.py --start-year 110 --end-year 113
  
  # 更新單一年度（112 年）
  python tool/update_history_financials.py --start-year 112 --end-year 112
  
  # 自訂延遲時間為 15 秒
  python tool/update_history_financials.py --start-year 110 --end-year 113 --delay 15
  
注意事項:
  - 每個季度之間會自動延遲，避免被 MOPS 封鎖 IP
  - 建議在非交易時段執行，避免影響即時資料更新
  - 如果中途被封鎖，可以調整 --delay 參數後繼續執行
        """
    )
    # 定義爬取的年限和延遲時間參數
    parser.add_argument('--start-year', type=int, default=110, help='起始民國年（預設 110）')
    parser.add_argument('--end-year', type=int, default=113, help='結束民國年（預設 113）')
    parser.add_argument('--delay', type=int, default=10, help='每次請求之間的延遲秒數（預設 10）')
    
    args = parser.parse_args()
    
    # 確認提示
    print("\n⚠️ 注意: 此操作將會:")
    print(f"  1. 爬取 {args.start_year}-{args.end_year} 年共 {(args.end_year - args.start_year + 1) * 4} 個季度")
    print(f"  2. 每次請求間隔 {args.delay} 秒")
    print(f"  3. 覆蓋資料庫中的現有資料")
    
    confirm = input("\n❓ 確定要繼續嗎？(yes/no): ").strip().lower()
    if confirm not in ['yes', 'y']:
        print("❌ 已取消操作")
        sys.exit(0)
    
    success = update_history(args.start_year, args.end_year, args.delay)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
