import pandas as pd
from sqlalchemy import create_engine, text
import joblib
import os
from config import Config

# ============================================
# ⚙️ V27: AI 賦能版 (統一使用 Config)
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI
MODEL_PATH = Config.MODEL_PATH
BOND_SYMBOL = Config.BOND_SYMBOL
MARKET_SYMBOL = Config.MARKET_SYMBOL
FEATURES = Config.FEATURES

INITIAL_CAPITAL = 1000000
FEE_RATE = 0.001425
MIN_FEE = 20
TAX_RATE = 0.003
MAX_HOLDINGS = 3        
POSITION_SIZE = 0.33    

# 策略參數
STOP_LOSS_PCT = 0.08    # 放寬停損給 AI 空間
TAKE_PROFIT_PCT = 0.20  # 讓利潤奔跑
MAX_HOLD_DAYS = 20      # 持有久一點

class BacktestEngine:
    def __init__(self):
        self.engine = create_engine(DB_URL)
        self.capital = INITIAL_CAPITAL
        self.positions = {}
        # 載入 AI 模型
        if os.path.exists(MODEL_PATH):
            self.model = joblib.load(MODEL_PATH)
            print("🧠 AI 模型載入成功！")
        else:
            raise FileNotFoundError("找不到 AI 模型，請先跑 3_train_model.py")

    def get_data(self, stock_id, date_str):
        with self.engine.connect() as conn:
            query = text(f"SELECT * FROM daily_market_data WHERE stock_id='{stock_id}' AND trade_date='{date_str}'")
            return conn.execute(query).mappings().fetchone()

    def get_latest_price(self, stock_id, current_date_str):
        with self.engine.connect() as conn:
            query = text(f"SELECT close_price FROM daily_market_data WHERE stock_id='{stock_id}' AND trade_date <= '{current_date_str}' ORDER BY trade_date DESC LIMIT 1")
            return conn.execute(query).scalar() or 0

    def get_market_status(self, date_str):
        data = self.get_data(MARKET_SYMBOL, date_str)
        if not data or data['ma60'] == 0: return 'NEUTRAL'
        # 雙重濾網: 股價 > MA20 > MA60
        ma20 = data['ma20'] if data['ma20'] > 0 else data['close_price']
        if data['close_price'] > ma20 and data['close_price'] > data['ma60']:
            return 'BULL'
        return 'BEAR'

    def get_ai_predictions(self, date_str):
        # 1. 初步篩選 (流動性 + 基本面)
        query = text(f"""
            SELECT * FROM daily_market_data
            WHERE trade_date = '{date_str}' 
            AND stock_id NOT IN ('{BOND_SYMBOL}', '{MARKET_SYMBOL}')
            AND volume > 2000000
            AND close_price > ma20
        """)
        with self.engine.connect() as conn:
            candidates = pd.read_sql(query, conn)
        
        if candidates.empty: return []

        # 2. 準備特徵 (使用 Config 統一定義)
        X = candidates[FEATURES].fillna(0)
        
        # 3. AI 預測 (機率)
        probs = self.model.predict_proba(X)[:, 1] # 取出 "會漲" 的機率
        candidates['ai_score'] = probs
        
        # 4. 選出 AI 最有信心的前 3 名
        top_picks = candidates.sort_values('ai_score', ascending=False).head(3)
        return top_picks['stock_id'].tolist()

    def buy(self, stock_id, price, date_str, asset_type='stock'):
        if len(self.positions) >= MAX_HOLDINGS and asset_type == 'stock': return
        budget = self.capital * (0.95 if asset_type == 'bond' else POSITION_SIZE)
        if budget < price * 1000: return
        
        shares = int(budget / price)
        cost = shares * price
        fee = max(int(cost * FEE_RATE), MIN_FEE)
        if self.capital < cost + fee: return

        self.capital -= (cost + fee)
        self.positions[stock_id] = {'shares': shares, 'cost': price, 'total_cost': cost+fee, 'days': 0, 'type': asset_type}
        print(f"{'🛡️' if asset_type=='bond' else '🤖'} {date_str} 買入 {stock_id} ({shares}股) @ {price}")

    def sell(self, stock_id, price, date_str, reason):
        info = self.positions[stock_id]
        rev = info['shares'] * price
        fee = max(int(rev * FEE_RATE), MIN_FEE)
        tax = int(rev * TAX_RATE)
        net = rev - fee - tax
        profit = (net - info['total_cost']) / info['total_cost'] * 100
        
        self.capital += net
        del self.positions[stock_id]
        print(f"{'🛑' if profit<0 else '🎉'} {date_str} {reason} 賣出 {stock_id} | 損益: {profit:.2f}%")

    def run(self):
        print(f"🚀 V27 AI 賦能回測 (xgboost + 雙重濾網)...")
        with self.engine.connect() as conn:
            dates = conn.execute(text(f"SELECT DISTINCT trade_date FROM daily_market_data WHERE trade_date >= '2025-08-01' ORDER BY trade_date")).fetchall()
        date_list = [d[0].strftime("%Y-%m-%d") for d in dates]

        for date_str in date_list:
            trend = self.get_market_status(date_str)
            bond_data = self.get_data(BOND_SYMBOL, date_str)
            
            # 賣出檢查
            for sid in list(self.positions.keys()):
                self.positions[sid]['days'] += 1
                curr = self.get_data(sid, date_str)
                if not curr: continue
                
                curr_price = curr['close_price']
                change = (curr_price - self.positions[sid]['cost']) / self.positions[sid]['cost']
                
                if self.positions[sid]['type'] == 'bond':
                    if trend == 'BULL': self.sell(sid, curr_price, date_str, "市場回穩")
                else:
                    if change <= -STOP_LOSS_PCT: self.sell(sid, curr_price, date_str, "停損")
                    elif change >= TAKE_PROFIT_PCT: self.sell(sid, curr_price, date_str, "停利")
                    elif self.positions[sid]['days'] >= MAX_HOLD_DAYS: self.sell(sid, curr_price, date_str, "時間到")
                    elif trend == 'BEAR' and change < 0: self.sell(sid, curr_price, date_str, "逃命")

            # 進場檢查
            if trend == 'BULL':
                targets = self.get_ai_predictions(date_str)
                for t in targets: 
                    d = self.get_data(t, date_str)
                    if d: self.buy(t, d['close_price'], date_str)
            elif trend == 'BEAR':
                # 買債避險
                if BOND_SYMBOL not in self.positions and bond_data:
                    self.buy(BOND_SYMBOL, bond_data['close_price'], date_str, asset_type='bond')

        # 最終結算
        final = self.capital
        for sid, info in self.positions.items():
            price = self.get_latest_price(sid, date_list[-1])
            final += info['shares'] * price
            
        print(f"\n💰 最終資產: {int(final)} | 報酬率: {(final-INITIAL_CAPITAL)/INITIAL_CAPITAL*100:.2f}%")

if __name__ == "__main__":
    BacktestEngine().run()