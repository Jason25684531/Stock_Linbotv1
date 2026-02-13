"""
策略診斷工具 - 快速檢查為何選不到股票
===================================
"""
import sys
import io
from datetime import datetime
from tool.db_helper import get_db_engine
from sqlalchemy import text

# Fix Windows UTF-8
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def main():
    engine = get_db_engine()
    
    print("=" * 60)
    print("📊 策略診斷報告")
    print("=" * 60)
    
    with engine.connect() as conn:
        # 1. Check latest dates
        result = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data"))
        market_date = result.fetchone()[0]
        
        result = conn.execute(text("SELECT MAX(trade_date) FROM temp_indicators"))
        indicator_date = result.fetchone()[0]
        
        print(f"\n【1. 資料時效性】")
        print(f"  price data (daily_market_data): {market_date}")
        print(f"  indicators (temp_indicators): {indicator_date}")
        
        if indicator_date < market_date:
            print(f"  ⚠️ 指標過期! 請執行: python 2_rundaily.py")
        else:
            print(f"  ✅ 指標已更新")
        
        # 2. Check indicator completeness
        query = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(ma60) as has_ma60,
                COUNT(ma20) as has_ma20,
                COUNT(rsi) as has_rsi,
                COUNT(natr) as has_natr
            FROM daily_market_data
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_market_data)
        """)
        result = conn.execute(query)
        stats = result.fetchone()
        
        print(f"\n【2. 指標完整性】(總共 {stats[0]} 檔)")
        print(f"  MA60: {stats[1]} ({stats[1]/stats[0]*100:.1f}%)")
        print(f"  MA20: {stats[2]} ({stats[2]/stats[0]*100:.1f}%)")
        print(f"  RSI: {stats[3]} ({stats[3]/stats[0]*100:.1f}%)")
        print(f"  NATR: {stats[4]} ({stats[4]/stats[0]*100:.1f}%)")
        
        if stats[1] == 0:
            print(f"  ❌ 技術指標未計算! 請執行: python 2_rundaily.py")
        
        # 3. Check strategy conditions
        query = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN natr < 4.0 AND natr > 0 THEN 1 END) as v33_natr,
                COUNT(CASE WHEN close_price > ma60 THEN 1 END) as above_ma60,
                COUNT(CASE WHEN close_price > ma20 THEN 1 END) as above_ma20,
                COUNT(CASE WHEN volume > 5000000 THEN 1 END) as vol_ok,
                COUNT(CASE WHEN revenue_yoy > 30 THEN 1 END) as yoy_over_30,
                COUNT(CASE WHEN revenue_yoy > 20 THEN 1 END) as yoy_over_20,
                COUNT(CASE WHEN natr < 4.0 AND close_price > ma60 AND volume > 5000000 THEN 1 END) as v33_pass
            FROM daily_market_data
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_market_data)
        """)
        result = conn.execute(query)
        cond = result.fetchone()
        
        print(f"\n【3. 策略條件通過率】")
        print(f"  [V33 Low Vol]")
        print(f"    NATR < 4%: {cond[1]} 檔 ({cond[1]/cond[0]*100:.1f}%)")
        print(f"    Close > MA60: {cond[2]} 檔 ({cond[2]/cond[0]*100:.1f}%)")
        print(f"    Volume > 500w: {cond[4]} 檔 ({cond[4]/cond[0]*100:.1f}%)")
        print(f"    ✓ 全部通過: {cond[7]} 檔")
        
        print(f"\n  [V34 Turbo]")
        print(f"    Revenue YoY > 30%: {cond[5]} 檔 ({cond[5]/cond[0]*100:.1f}%)")
        print(f"    Revenue YoY > 20%: {cond[6]} 檔 ({cond[6]/cond[0]*100:.1f}%)")
        print(f"    Close > MA20: {cond[3]} 檔 ({cond[3]/cond[0]*100:.1f}%)")
        
        # 4. Market condition
        query = text("""
            SELECT AVG(close_price / NULLIF(ma60, 0)) as avg_ratio
            FROM daily_market_data
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_market_data)
            AND ma60 IS NOT NULL AND ma60 > 0
        """)
        result = conn.execute(query)
        ratio = result.fetchone()[0]
        
        print(f"\n【4. 市場狀態】")
        if ratio:
            print(f"  平均 Close/MA60 = {ratio:.3f}")
            if ratio < 0.95:
                print(f"  ⚠️ 空頭市場 (建議使用 V31 較靈活)")
            elif ratio < 1.05:
                print(f"  ⚡ 盤整市場")
            else:
                print(f"  ✅ 多頭市場")
        
        # 5. Recommendations
        print(f"\n【5. 建議】")
        if stats[1] == 0:
            print(f"  🔧 執行: python 2_rundaily.py")
        elif cond[7] == 0:
            print(f"  💡 V33 條件太嚴格，建議:")
            print(f"     - 放寬 NATR < 5% (原 4%)")
            print(f"     - 改用 MA20 (原 MA60)")
        
        if cond[5] == 0 and cond[6] > 0:
            print(f"  💡 V34 條件太嚴格，建議降低到 YoY > 20%")
        
        print(f"\n  📝 檢查活躍策略: strategy_settings.json")
        print(f"  🚀 測試運行: python 2_rundaily.py")
        
    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
