"""
Phase 3.5 & 4 整合測試腳本
============================================
測試所有新功能是否正常運作
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from sqlalchemy import text
from tool.db_helper import get_db_engine
from tool.strategy_manager import StrategyManager


def test_financial_table():
    """測試 1: 財報資料表是否存在"""
    print("\n" + "="*50)
    print("測試 1: 檢查財報資料表")
    print("="*50)
    
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES LIKE 'financial_statements'"))
            if result.fetchone():
                print("✅ 資料表 financial_statements 存在")
                
                # 檢查欄位
                result = conn.execute(text("DESCRIBE financial_statements"))
                columns = [row[0] for row in result]
                required_cols = ['stock_id', 'year', 'quarter', 'revenue', 'rd_expense', 'eps']
                
                missing = [col for col in required_cols if col not in columns]
                if missing:
                    print(f"❌ 缺少欄位: {missing}")
                    return False
                else:
                    print(f"✅ 所有必要欄位都存在: {required_cols}")
                    return True
            else:
                print("❌ 資料表不存在")
                return False
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        return False


def test_quarterly_scraper():
    """測試 2: 季報爬蟲模組是否可載入"""
    print("\n" + "="*50)
    print("測試 2: 檢查季報爬蟲模組")
    print("="*50)
    
    try:
        from tool.crawlers.quarterly_scraper import QuarterlyScraper
        scraper = QuarterlyScraper()
        print("✅ QuarterlyScraper 類別載入成功")
        print(f"  • User-Agent 數量: {len(scraper.USER_AGENTS)}")
        print(f"  • 重試次數: {scraper.retry_count}")
        return True
    except Exception as e:
        print(f"❌ 載入失敗: {e}")
        return False


def test_v35_strategy():
    """測試 3: V35 策略是否註冊"""
    print("\n" + "="*50)
    print("測試 3: 檢查 V35 策略")
    print("="*50)
    
    try:
        manager = StrategyManager()
        
        # 檢查策略註冊表
        if 'v35_innovation' in manager.STRATEGY_REGISTRY:
            print("✅ V35 策略已註冊到 StrategyManager")
        else:
            print("❌ V35 策略未註冊")
            return False
        
        # 嘗試載入策略
        from tool.strategies.v35_innovation import V35InnovationStrategy
        strategy = V35InnovationStrategy()
        
        print(f"✅ V35 策略載入成功")
        print(f"  • 策略名稱: {strategy.name}")
        print(f"  • 顯示名稱: {strategy.display_name}")
        print(f"  • 目標報酬: {strategy.target_return*100}%")
        print(f"  • 預測天數: {strategy.look_ahead_days}天")
        print(f"  • 必要特徵: {strategy.features[:5]}...")
        
        # 測試篩選邏輯（空資料框）
        test_df = pd.DataFrame({
            'stock_id': ['2330', '2454'],
            'rd_ratio': [0.05, 0.02],
            'revenue_yoy': [10, -5],
            'eps': [1.5, 0.3],
            'close': [500, 100],
            'ma60': [480, 105],
            'volume_ratio': [1.2, 0.5],
        })
        
        result = strategy.filter_candidates(test_df)
        print(f"✅ 篩選邏輯測試通過 (輸入2檔 -> 輸出{len(result)}檔)")
        
        return True
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_merge_financial_data():
    """測試 4: 財報數據合併功能"""
    print("\n" + "="*50)
    print("測試 4: 測試財報數據合併")
    print("="*50)
    
    try:
        # 建立測試數據
        from tool.db_helper import get_db_engine
        
        # 插入測試財報數據
        engine = get_db_engine()
        with engine.connect() as conn:
            # 清除測試數據
            conn.execute(text("DELETE FROM financial_statements WHERE stock_id = '9999'"))
            
            # 插入測試數據
            conn.execute(text("""
                INSERT INTO financial_statements 
                (stock_id, year, quarter, revenue, rd_expense, eps)
                VALUES ('9999', 2024, 3, 1000000, 50000, 1.5)
            """))
            conn.commit()
            print("✅ 測試數據插入成功")
        
        # 測試合併函數
        test_df = pd.DataFrame({
            'stock_id': ['9999', '8888'],
            'close': [100, 200],
        })
        
        # 載入合併函數
        from importlib import import_module
        rundaily = import_module('2_rundaily')
        merged = rundaily.merge_financial_data(test_df, engine)
        
        print(f"✅ 合併函數執行成功")
        print(f"  • 新增欄位: {[col for col in merged.columns if col not in test_df.columns]}")
        print(f"  • 9999 的 rd_ratio: {merged[merged['stock_id']=='9999']['rd_ratio'].values[0]:.2%}")
        
        # 清理測試數據
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM financial_statements WHERE stock_id = '9999'"))
            conn.commit()
        
        return True
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_line_push_integration():
    """測試 5: LINE 推播整合"""
    print("\n" + "="*50)
    print("測試 5: 檢查 LINE 推播整合")
    print("="*50)
    
    try:
        # 檢查策略顯示名稱
        from importlib import import_module
        push_module = import_module('5_push_to_line')
        
        if 'v35_innovation' in push_module.STRATEGY_DISPLAY_NAMES:
            print(f"✅ V35 已加入 LINE 推播")
            print(f"  • 顯示名稱: {push_module.STRATEGY_DISPLAY_NAMES['v35_innovation']}")
        else:
            print("❌ V35 未加入顯示名稱對照表")
            return False
        
        print("✅ LINE 推播整合完成")
        return True
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """執行所有測試"""
    print("\n" + "="*60)
    print("  Phase 3.5 & 4 整合測試")
    print("="*60)
    
    tests = [
        ("財報資料表", test_financial_table),
        ("季報爬蟲", test_quarterly_scraper),
        ("V35 策略", test_v35_strategy),
        ("財報數據合併", test_merge_financial_data),
        ("LINE 推播整合", test_line_push_integration),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"\n❌ {name} 測試發生錯誤: {e}")
            results.append((name, False))
    
    # 總結
    print("\n" + "="*60)
    print("  測試結果總結")
    print("="*60)
    
    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}  {name}")
    
    passed = sum(1 for _, s in results if s)
    total = len(results)
    
    print(f"\n總計: {passed}/{total} 項測試通過")
    
    if passed == total:
        print("\n🎉 所有測試通過！系統已準備就緒。")
        return 0
    else:
        print("\n⚠️ 部分測試失敗，請檢查上述錯誤訊息。")
        return 1


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
