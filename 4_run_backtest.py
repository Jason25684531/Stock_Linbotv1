import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
import random

# ============================================
# ⚙️ V26 策略設定 (結算修復 + 雙重濾網)
# ============================================
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"
BOND_SYMBOL = '00679B'      # 避險標的
MARKET_SYMBOL = '0050'      # 大盤風向球

INITIAL_CAPITAL = 1000000   
FEE_RATE = 0.001425         
TAX_RATE = 0.003            
MIN_FEE = 20                

# 策略參數
PANIC_THRESHOLD = -8        # 恐慌指數
MAX_HOLDINGS = 3            # 最多持倉 3 檔
POSITION_SIZE = 0.33        # 每檔 33%

STOP_LOSS_PCT = 0.06        # 停損 6% (稍微放寬一點點，避免洗盤)
TAKE_PROFIT_PCT = 0.15      # 停利 15%
MAX_HOLD_DAYS = 15          # 持有 15 天

class BacktestEngine:
    def __init__(self):
        self.engine = create_engine(DB_URL)
        self.capital = INITIAL_CAPITAL
        self.positions = {} 
        self.history = []
        self.cool_down_counter = 0

    def get_data(self, stock_id, date_str):
        query = text(f"SELECT * FROM daily_market_data WHERE stock_id = '{stock_id}' AND trade_date = '{date_str}'")
        with self.engine.connect() as conn:
            result = conn.execute(query).fetchone()
            if result: return dict(zip(result._mapping.keys(), result))
        return None

    def get_latest_price(self, stock_id, current_date_str):
        """🟢 [V26 新增] 往前尋找最近的收盤價 (防止結算變 0)"""
        query = text(f"""
            SELECT close_price FROM daily_market_data 
            WHERE stock_id = '{stock_id}' AND trade_date <= '{current_date_str}'
            ORDER BY trade_date DESC LIMIT 1
        """)
        with self.engine.connect() as conn:
            result = conn.execute(query).scalar()
            return result if result else 0

    def get_market_status(self, date_str):
        """判斷市場狀態 (V26 雙重濾網)"""
        data = self.get_data(MARKET_SYMBOL, date_str)
        if not data or data['ma60'] == 0: return 'NEUTRAL', 0
        
        bias = (data['close_price'] - data['ma60']) / data['ma60'] * 100
        
        # 🟢 [雙重濾網] 必須 股價 > MA20 且 股價 > MA60 (強多頭)
        ma20 = data['ma20'] if data['ma20'] > 0 else data['close_price']
        
        if data['close_price'] > ma20 and data['close_price'] > data['ma60']:
            trend = 'BULL'
        else:
            trend = 'BEAR' # 只要跌破月線就轉空，反應更快
            
        return trend, bias

    def get_ai_predictions(self, date_str):
        """(V26) 選股邏輯：RSI 黃金交叉 + 強勢股"""
        query = text(f"""
            SELECT stock_id, close_price FROM daily_market_data
            WHERE trade_date = '{date_str}' 
            AND stock_id NOT IN ('{BOND_SYMBOL}', '{MARKET_SYMBOL}', '00632R')
            AND rsi > 55 AND rsi < 75  -- 動能強勁
            AND close_price > ma20     -- 站上月線
            AND volume > 2000000       -- 成交量 > 2000張 (流動性佳)
            ORDER BY rsi DESC
            LIMIT 5
        """)
        with self.engine.connect() as conn:
            results = conn.execute(query).fetchall()
        return [row[0] for row in results]

    def buy(self, stock_id, price, date_str, asset_type='stock'):
        if len(self.positions) >= MAX_HOLDINGS and asset_type == 'stock':
            return

        if asset_type == 'bond':
            budget = self.capital * 0.95 
        else:
            budget = self.capital * POSITION_SIZE 
            
        if budget < price * 1000: return 
        
        shares = int(budget / price)
        cost = shares * price
        fee = max(int(cost * FEE_RATE), MIN_FEE)
        
        total_cost = cost + fee
        if self.capital < total_cost: return

        self.capital -= total_cost
        
        self.positions[stock_id] = {
            'shares': shares, 
            'cost': price, 
            'total_cost': total_cost,
            'days': 0, 
            'type': asset_type
        }
        
        icon = "🛡️" if asset_type == 'bond' else "💰"
        print(f"{icon} {date_str} 買入 {stock_id} ({shares}股) @ {price}")

    def sell(self, stock_id, price, date_str, reason):
        if stock_id not in self.positions: return
        
        info = self.positions[stock_id]
        shares = info['shares']
        
        revenue = shares * price
        fee = max(int(revenue * FEE_RATE), MIN_FEE)
        tax = int(revenue * TAX_RATE)
        net_revenue = revenue - fee - tax
        
        profit_amount = net_revenue - info['total_cost']
        profit_pct = (profit_amount / info['total_cost']) * 100
        
        self.capital += net_revenue
        del self.positions[stock_id]
        
        icon = "🛑" if profit_pct < 0 else "🎉"
        if "避險" in reason: icon = "🌤️"
        
        print(f"{icon} {date_str} {reason} 賣出 {stock_id} @ {price} | 損益: {profit_pct:.2f}% (${int(profit_amount)})")

    def run(self):
        print(f"🚀 V26 結算修復回測 (雙重濾網 + 智能結算)...")
        
        with self.engine.connect() as conn:
            dates = conn.execute(text(f"SELECT DISTINCT trade_date FROM daily_market_data WHERE trade_date >= '2025-08-01' ORDER BY trade_date")).fetchall()
        date_list = [d[0].strftime("%Y-%m-%d") for d in dates]
        
        for date_str in date_list:
            for sid in list(self.positions.keys()):
                self.positions[sid]['days'] += 1
            
            trend, bias = self.get_market_status(date_str)
            bond_data = self.get_data(BOND_SYMBOL, date_str)
            
            # --- 1. 賣出檢查 ---
            for stock_id in list(self.positions.keys()):
                info = self.positions[stock_id]
                stock_data = self.get_data(stock_id, date_str)
                if not stock_data: continue
                
                curr_price = stock_data['close_price']
                buy_price = info['cost']
                pct_change = (curr_price - buy_price) / buy_price
                
                if info['type'] == 'bond':
                    if bias > -3: 
                        self.sell(stock_id, curr_price, date_str, "債券退場")
                        self.cool_down_counter = 3
                else:
                    if pct_change <= -STOP_LOSS_PCT:
                        self.sell(stock_id, curr_price, date_str, "停損")
                    elif pct_change >= TAKE_PROFIT_PCT:
                        self.sell(stock_id, curr_price, date_str, "停利")
                    elif info['days'] >= MAX_HOLD_DAYS:
                        self.sell(stock_id, curr_price, date_str, "時間到")
                    elif trend == 'BEAR' and pct_change < 0:
                         self.sell(stock_id, curr_price, date_str, "趨勢轉空逃命")

            # --- 2. 避險檢查 ---
            if bias < PANIC_THRESHOLD:
                for sid in list(self.positions.keys()):
                    if self.positions[sid]['type'] == 'stock':
                        data = self.get_data(sid, date_str)
                        if data: self.sell(sid, data['close_price'], date_str, "恐慌逃命")
                
                if BOND_SYMBOL not in self.positions and bond_data:
                    self.buy(BOND_SYMBOL, bond_data['close_price'], date_str, asset_type='bond')
                continue 

            if self.cool_down_counter > 0:
                self.cool_down_counter -= 1
                continue

            # --- 3. 股票進場 ---
            if trend == 'BULL':
                targets = self.get_ai_predictions(date_str)
                for target in targets:
                    data = self.get_data(target, date_str)
                    if data: self.buy(target, data['close_price'], date_str)

        # 🟢 [V26] 智慧結算 (Fix Settlement)
        final_assets = self.capital
        print("\n📊 正在計算最終持倉價值 (自動補價)...")
        final_date = date_list[-1]
        
        for sid, info in self.positions.items():
            # 優先抓最後一天的價格
            data = self.get_data(sid, final_date)
            price = 0
            if data:
                price = data['close_price']
            else:
                # 抓不到就往前找
                price = self.get_latest_price(sid, final_date)
                print(f"  ⚠️ {sid} 缺 {final_date} 資料，使用最近收盤價: {price}")
            
            market_value = info['shares'] * price
            final_assets += market_value
            print(f"  🔹 持倉 {sid}: {info['shares']} 股 x {price} = ${int(market_value)}")

        print("\n===========================")
        print(f"💰 初始本金: {INITIAL_CAPITAL}")
        print(f"💰 最終資產: {int(final_assets)}")
        total_ret = (final_assets - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        print(f"📈 總報酬率: {total_ret:.2f}%")
        print("===========================")

if __name__ == "__main__":
    engine = BacktestEngine()
    engine.run()