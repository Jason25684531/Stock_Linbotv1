"""
測試策略工廠重構
============================================
驗證 V31 策略在新架構下的運作
"""

import pandas as pd
from tool.strategy_manager import StrategyManager
from tool.db_helper import get_db_engine

def test_strategy_loading():
    """測試策略載入"""
    print("\n" + "="*60)
    print(" 測試 1: 策略載入")
    print("="*60)
    
    mgr = StrategyManager()
    strategy = mgr.get_active_strategy()
    
    print(f"✅ 策略名稱: {strategy.display_name}")
    print(f"✅ 策略描述: {strategy.description}")
    print(f"✅ 特徵數量: {len(strategy.features)} 個")
    print(f"✅ 目標報酬: {strategy.target_return*100}%")
    print(f"✅ 預測天數: {strategy.look_ahead_days} 天")
    
    return True


def test_filter_candidates():
    """測試篩選邏輯"""
    print("\n" + "="*60)
    print(" 測試 2: 篩選邏輯")
    print("="*60)
    
    try:
        # 讀取最新資料
        engine = get_db_engine()
        query = """
            SELECT * FROM daily_market_data 
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_market_data)
            LIMIT 100
        """
        df = pd.read_sql(query, engine)
        
        print(f"📊 載入資料: {len(df)} 筆")
        
        # 使用策略篩選
        mgr = StrategyManager()
        strategy = mgr.get_active_strategy()
        
        print(f"\n🔍 使用策略: {strategy.display_name}")
        candidates = strategy.filter_candidates(df)
        
        print(f"✅ 篩選結果: {len(candidates)} 檔候選股票")
        
        if not candidates.empty:
            print(f"\n前 5 檔:")
            print(candidates[['stock_id', 'close_price', 'volume', 'rsi']].head())
        
        return True
        
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_backward_compatibility():
    """測試向後兼容性"""
    print("\n" + "="*60)
    print(" 測試 3: 向後兼容性")
    print("="*60)
    
    try:
        # 測試舊的 API 是否依然可用
        from tool.strategy import get_v30_candidates
        
        engine = get_db_engine()
        query = """
            SELECT * FROM daily_market_data 
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_market_data)
            LIMIT 100
        """
        df = pd.read_sql(query, engine)
        
        print(f"📊 載入資料: {len(df)} 筆")
        
        # 使用舊的 get_v30_candidates
        print(f"\n🔍 測試舊 API: get_v30_candidates()")
        candidates = get_v30_candidates(df)
        
        print(f"✅ 向後兼容測試通過: {len(candidates)} 檔")
        
        return True
        
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n" + "="*70)
    print(" 策略工廠重構驗證測試".center(70))
    print("="*70)
    
    results = []
    
    # 測試 1: 策略載入
    results.append(("策略載入", test_strategy_loading()))
    
    # 測試 2: 篩選邏輯
    results.append(("篩選邏輯", test_filter_candidates()))
    
    # 測試 3: 向後兼容性
    results.append(("向後兼容性", test_backward_compatibility()))
    
    # 總結
    print("\n" + "="*70)
    print(" 測試總結".center(70))
    print("="*70)
    
    for name, result in results:
        status = "✅ 通過" if result else "❌ 失敗"
        print(f"{status} - {name}")
    
    all_passed = all(r for _, r in results)
    
    if all_passed:
        print("\n🎉 所有測試通過！策略工廠重構成功！")
        print("\n下一步:")
        print("  1. 執行 python 2_rundaily.py 測試完整流程")
        print("  2. 開始實作 V33 Low Vol 策略")
        print("  3. 開始實作 V34 Twin-Turbo 策略")
    else:
        print("\n⚠️ 部分測試失敗，請檢查錯誤訊息")
