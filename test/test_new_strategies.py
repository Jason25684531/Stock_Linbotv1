"""
測試新策略 (V33 & V34)
============================================
驗證新策略的載入、切換與篩選功能
"""

import pandas as pd
from tool.strategy_manager import StrategyManager
from tool.db_helper import get_db_engine


def test_strategy_registration():
    """測試策略註冊"""
    print("\n" + "="*60)
    print(" 測試 1: 策略註冊檢查")
    print("="*60)
    
    mgr = StrategyManager()
    available = mgr.list_available_strategies()
    
    print(f"\n可用策略清單：")
    for name, display in available.items():
        print(f"  • {name}: {display}")
    
    expected = ['v31_hybrid', 'v33_low_vol', 'v34_turbo']
    all_registered = all(s in available for s in expected)
    
    if all_registered:
        print(f"\n✅ 所有策略已註冊成功")
        return True
    else:
        print(f"\n❌ 部分策略註冊失敗")
        return False


def test_v33_loading():
    """測試 V33 策略載入"""
    print("\n" + "="*60)
    print(" 測試 2: V33 策略載入")
    print("="*60)
    
    try:
        mgr = StrategyManager()
        success = mgr.set_active_strategy('v33_low_vol')
        
        if not success:
            print(f"❌ V33 策略切換失敗")
            return False
        
        strategy = mgr.get_active_strategy()
        
        print(f"\n✅ 策略名稱: {strategy.display_name}")
        print(f"✅ 策略描述: {strategy.description}")
        print(f"✅ 特徵數量: {len(strategy.features)} 個")
        print(f"✅ 目標報酬: {strategy.target_return*100}%")
        print(f"✅ 停損: {strategy.stop_loss*100}%")
        print(f"✅ 持有期: {strategy.max_hold_days} 天")
        
        # 檢查核心特徵
        core_features = ['natr', 'std_20']
        has_core = all(f in strategy.features for f in core_features)
        
        if has_core:
            print(f"✅ 核心特徵: {core_features}")
        else:
            print(f"⚠️ 缺少核心特徵")
        
        return True
        
    except Exception as e:
        print(f"❌ V33 載入失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_v34_loading():
    """測試 V34 策略載入"""
    print("\n" + "="*60)
    print(" 測試 3: V34 策略載入")
    print("="*60)
    
    try:
        mgr = StrategyManager()
        success = mgr.set_active_strategy('v34_turbo')
        
        if not success:
            print(f"❌ V34 策略切換失敗")
            return False
        
        strategy = mgr.get_active_strategy()
        
        print(f"\n✅ 策略名稱: {strategy.display_name}")
        print(f"✅ 策略描述: {strategy.description}")
        print(f"✅ 特徵數量: {len(strategy.features)} 個")
        print(f"✅ 目標報酬: {strategy.target_return*100}%")
        print(f"✅ 停損: {strategy.stop_loss*100}%")
        print(f"✅ 持有期: {strategy.max_hold_days} 天")
        
        # 檢查核心特徵
        core_features = ['revenue_yoy', 'volume_ratio']
        has_core = all(f in strategy.features for f in core_features)
        
        if has_core:
            print(f"✅ 核心特徵: {core_features}")
        else:
            print(f"⚠️ 缺少核心特徵")
        
        return True
        
    except Exception as e:
        print(f"❌ V34 載入失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_v33_filtering():
    """測試 V33 篩選邏輯"""
    print("\n" + "="*60)
    print(" 測試 4: V33 篩選邏輯")
    print("="*60)
    
    try:
        # 切換到 V33
        mgr = StrategyManager()
        mgr.set_active_strategy('v33_low_vol')
        strategy = mgr.get_active_strategy()
        
        # 讀取資料
        engine = get_db_engine()
        query = """
            SELECT * FROM daily_market_data 
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_market_data)
            LIMIT 200
        """
        df = pd.read_sql(query, engine)
        
        print(f"\n📊 載入資料: {len(df)} 筆")
        print(f"   包含欄位: {', '.join(df.columns[:10].tolist())}...")
        
        # 檢查必要欄位
        required = ['natr', 'std_20']
        missing = [col for col in required if col not in df.columns]
        
        if missing:
            print(f"⚠️ 缺少欄位: {missing}")
            print(f"   V33 策略需要這些欄位才能運作")
        
        # 執行篩選
        print(f"\n🔍 執行 V33 篩選...")
        candidates = strategy.filter_candidates(df)
        
        print(f"\n✅ V33 篩選結果: {len(candidates)} 檔")
        
        if not candidates.empty:
            print(f"\n前 5 檔低波動股票:")
            display_cols = ['stock_id', 'close_price', 'natr', 'std_20', 'rsi']
            display_cols = [c for c in display_cols if c in candidates.columns]
            print(candidates[display_cols].head().to_string(index=False))
        
        return True
        
    except Exception as e:
        print(f"❌ V33 篩選測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_v34_filtering():
    """測試 V34 篩選邏輯"""
    print("\n" + "="*60)
    print(" 測試 5: V34 篩選邏輯")
    print("="*60)
    
    try:
        # 切換到 V34
        mgr = StrategyManager()
        mgr.set_active_strategy('v34_turbo')
        strategy = mgr.get_active_strategy()
        
        # 讀取資料
        engine = get_db_engine()
        query = """
            SELECT * FROM daily_market_data 
            WHERE trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM daily_market_data), INTERVAL 60 DAY)
            ORDER BY trade_date DESC
            LIMIT 5000
        """
        df = pd.read_sql(query, engine)
        
        print(f"\n📊 載入資料: {len(df)} 筆（需要歷史資料計算60日高）")
        
        # 檢查必要欄位
        required = ['revenue_yoy', 'volume_ratio']
        missing = [col for col in required if col not in df.columns]
        
        if missing:
            print(f"⚠️ 缺少欄位: {missing}")
            print(f"   V34 策略需要這些欄位才能運作")
        
        # 執行篩選
        print(f"\n🚀 執行 V34 篩選...")
        candidates = strategy.filter_candidates(df)
        
        print(f"\n✅ V34 篩選結果: {len(candidates)} 檔")
        
        if not candidates.empty:
            print(f"\n前 5 檔高成長突破股:")
            display_cols = ['stock_id', 'close_price', 'revenue_yoy', 'volume_ratio']
            display_cols = [c for c in display_cols if c in candidates.columns]
            print(candidates[display_cols].head().to_string(index=False))
        
        return True
        
    except Exception as e:
        print(f"❌ V34 篩選測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_strategy_switching():
    """測試策略切換"""
    print("\n" + "="*60)
    print(" 測試 6: 策略切換功能")
    print("="*60)
    
    try:
        mgr = StrategyManager()
        
        strategies = ['v31_hybrid', 'v33_low_vol', 'v34_turbo']
        
        for strat_name in strategies:
            success = mgr.set_active_strategy(strat_name)
            if success:
                current = mgr.get_active_strategy()
                print(f"✅ 切換至 {current.display_name}")
            else:
                print(f"❌ 切換至 {strat_name} 失敗")
                return False
        
        # 恢復預設
        mgr.set_active_strategy('v31_hybrid')
        print(f"\n✅ 策略切換測試通過")
        return True
        
    except Exception as e:
        print(f"❌ 策略切換測試失敗: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*70)
    print(" 新策略測試 (V33 & V34)".center(70))
    print("="*70)
    
    results = []
    
    # 測試 1: 策略註冊
    results.append(("策略註冊", test_strategy_registration()))
    
    # 測試 2: V33 載入
    results.append(("V33 載入", test_v33_loading()))
    
    # 測試 3: V34 載入
    results.append(("V34 載入", test_v34_loading()))
    
    # 測試 4: V33 篩選
    results.append(("V33 篩選", test_v33_filtering()))
    
    # 測試 5: V34 篩選
    results.append(("V34 篩選", test_v34_filtering()))
    
    # 測試 6: 策略切換
    results.append(("策略切換", test_strategy_switching()))
    
    # 總結
    print("\n" + "="*70)
    print(" 測試總結".center(70))
    print("="*70)
    
    for name, result in results:
        status = "✅ 通過" if result else "❌ 失敗"
        print(f"{status} - {name}")
    
    all_passed = all(r for _, r in results)
    
    if all_passed:
        print("\n🎉 所有測試通過！新策略實作成功！")
        print("\n📋 可用策略：")
        print("  • V31 混合策略 (v31_hybrid) - 平衡型")
        print("  • V33 低波動策略 (v33_low_vol) - 穩健型")
        print("  • V34 雙渦輪策略 (v34_turbo) - 積極型")
        print("\n🔄 切換方式：")
        print("  from tool.strategy_manager import StrategyManager")
        print("  mgr = StrategyManager()")
        print("  mgr.set_active_strategy('v33_low_vol')")
    else:
        print("\n⚠️ 部分測試失敗，請檢查錯誤訊息")
