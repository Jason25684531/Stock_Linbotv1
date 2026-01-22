"""
V31 統一回測引擎
============================================
支援兩種模式：
  1. V30 模式：純技術面策略（不需 AI 模型）
  2. V31 模式：V30 篩選 + AI 模型排名

用法：
  python 4_run_backtest.py          # 預設 V31 模式
  python 4_run_backtest.py --v30    # 純 V30 模式
  python 4_run_backtest.py --v31    # V31 混合模式
"""
import pandas as pd
from sqlalchemy import create_engine, text
import joblib
import os
import sys
from config import Config
from tool.strategy import get_v30_candidates, get_v30_params_from_db
from tool.db_helper import get_db_engine

# ============================================
# ⚙️ 設定區（統一使用 Config + db_helper）
# ============================================

# 交易參數
INITIAL_CAPITAL = 1000000
FEE_RATE = 0.001425
MIN_FEE = 20
TAX_RATE = 0.003

# 🔥 V31 Optimization: 持倉限制（降低單檔風險）
MAX_HOLDINGS = 3
POSITION_SIZE = 0.20  # 優化：從 30% 降至 20%（配合放寬停損）

# 回測起始日
BACKTEST_START = '2025-06-01'

# V31 AI 參數
AI_CONFIDENCE_THRESHOLD = 0.60  # AI 信心門檻

# 從資料庫讀取參數
USE_DB_PARAMS = True


class BacktestEngine:
    """
    V31 統一回測引擎
    支援 V30（純技術）和 V31（技術 + AI）兩種模式
    """
    
    def __init__(self, mode='v31'):
        """
        初始化回測引擎
        
        Args:
            mode: 'v30' = 純技術面, 'v31' = 技術 + AI
        """
        self.mode = mode.lower()
        self.engine = get_db_engine()
        self.capital = INITIAL_CAPITAL
        self.positions = {}
        self.trade_count = 0
        self.win_count = 0
        self.trades = []  # 記錄所有交易
        
        # V32: 每日資產追蹤（用於計算 MDD 和 Sharpe）
        self.daily_assets = []
        self.daily_dates = []
        
        # 載入策略參數
        self._load_params()
        
        # V31 模式才載入 AI 模型
        self.model = None
        self.features = None
        if self.mode == 'v31':
            self._load_model()
    
    def _load_params(self):
        """載入策略參數（從資料庫或本地）"""
        if USE_DB_PARAMS:
            params = get_v30_params_from_db()
            self.stop_loss_pct = params['STOP_LOSS']
            self.take_profit_pct = params['TAKE_PROFIT']
            self.max_hold_days = params['MAX_HOLD_DAYS']
        else:
            self.stop_loss_pct = Config.V30_PARAMS['STOP_LOSS']
            self.take_profit_pct = Config.V30_PARAMS['TAKE_PROFIT']
            self.max_hold_days = Config.V30_PARAMS['MAX_HOLD_DAYS']
        
        # 格式化停利顯示
        tp_display = f"{self.take_profit_pct*100:.0f}%" if self.take_profit_pct > 0 else "不停利"
        print(f"📊 策略參數: 停損={self.stop_loss_pct*100:.0f}% | 停利={tp_display} | 持有={self.max_hold_days}天")
    
    def _load_model(self):
        """載入 V31 AI 模型"""
        if not os.path.exists(MODEL_PATH):
            print(f"⚠️ 找不到 AI 模型，切換為 V30 模式")
            self.mode = 'v30'
            return
        
        try:
            data = joblib.load(MODEL_PATH)
            if isinstance(data, dict) and 'model' in data:
                self.model = data['model']
                self.features = data.get('features', Config.FEATURES)
                print(f"🧠 V31 AI 模型載入成功！({len(self.features)} 個特徵)")
            else:
                # 舊格式相容
                self.model = data
                self.features = Config.FEATURES
                print("🧠 AI 模型載入成功（舊格式）")
        except Exception as e:
            print(f"⚠️ AI 模型載入失敗: {e}，切換為 V30 模式")
            self.mode = 'v30'
    
    def get_data(self, stock_id, date_str):
        """取得個股當日資料"""
        with self.engine.connect() as conn:
            query = text(f"""
                SELECT * FROM daily_market_data 
                WHERE stock_id='{stock_id}' AND trade_date='{date_str}'
            """)
            return conn.execute(query).mappings().fetchone()
    
    def get_market_trend(self, date_str):
        """判斷大盤趨勢"""
        data = self.get_data(MARKET_SYMBOL, date_str)
        if not data or not data.get('ma20') or not data.get('ma60'):
            return 'NEUTRAL'
        
        close = data['close_price']
        ma20 = data['ma20']
        ma60 = data['ma60']
        
        if close > ma20 > ma60:
            return 'BULL'
        elif close < ma20 < ma60:
            return 'BEAR'
        return 'NEUTRAL'
    
    def find_candidates(self, date_str):
        """
        尋找候選股票
        - V30 模式：純技術面篩選
        - V31 模式：技術面篩選 + AI 排名
        """
        # 基礎查詢
        query = text(f"""
            SELECT * FROM daily_market_data
            WHERE trade_date = '{date_str}'
            AND stock_id NOT IN ('{BOND_SYMBOL}', '{MARKET_SYMBOL}', '00632R')
            AND close_price > 10
            AND close_price < 500
        """)
        
        with self.engine.connect() as conn:
            df = pd.read_sql(query, conn)
        
        if df.empty:
            return []
        
        # V30 技術面篩選
        candidates = get_v30_candidates(df)
        if candidates.empty:
            return []
        
        # V31 模式：加入 AI 評分排序
        if self.mode == 'v31' and self.model is not None:
            # 準備特徵
            for f in self.features:
                if f not in candidates.columns:
                    candidates[f] = 0
            
            X = candidates[self.features].fillna(0)
            
            try:
                probs = self.model.predict_proba(X)[:, 1]
                candidates['ai_score'] = probs
                
                # 篩選高信心股票
                high_conf = candidates[candidates['ai_score'] >= AI_CONFIDENCE_THRESHOLD]
                if not high_conf.empty:
                    candidates = high_conf.sort_values('ai_score', ascending=False)
                else:
                    # 如果沒有達標的，取前 3 名
                    candidates = candidates.sort_values('ai_score', ascending=False)
            except Exception as e:
                print(f"⚠️ AI 預測失敗: {e}")
        else:
            # V30 模式：按外資買超或成交量排序
            if 'foreign_buy' in candidates.columns:
                candidates = candidates.sort_values('foreign_buy', ascending=False)
            else:
                candidates = candidates.sort_values('volume', ascending=False)
        
        return candidates.head(3)['stock_id'].tolist()
    
    def buy(self, stock_id, price, date_str):
        """買入"""
        if len(self.positions) >= MAX_HOLDINGS:
            return
        if stock_id in self.positions:
            return
        
        budget = INITIAL_CAPITAL * POSITION_SIZE
        if budget > self.capital:
            budget = self.capital * 0.9
        
        if budget < price * 1000:
            return
        
        shares = int(budget / price)
        
        # V32: 滑價模擬（買入時價格上滑 0.2%）
        slippage_price = price * (1 + Config.SLIPPAGE_RATE)
        cost = shares * slippage_price
        fee = max(int(cost * FEE_RATE), MIN_FEE)
        
        if self.capital < cost + fee:
            return
        
        self.capital -= (cost + fee)
        stop_loss = price * (1 - self.stop_loss_pct)
        
        self.positions[stock_id] = {
            'shares': shares,
            'cost': slippage_price,  # V32: 記錄滑價後的實際成本
            'total_cost': cost + fee,
            'days': 0,
            'stop_loss': stop_loss,
            'highest': slippage_price,
            'buy_date': date_str
        }
        
        print(f"🟢 {date_str} 買入 {stock_id} ({shares}股) @ {slippage_price:.2f} (滑價+{Config.SLIPPAGE_RATE*100:.1f}%) | 停損: {stop_loss:.2f}")
    
    def sell(self, stock_id, price, date_str, reason):
        """賣出"""
        if stock_id not in self.positions:
            return
        
        info = self.positions[stock_id]
        
        # V32: 滑價模擬（賣出時價格下滑 0.2%）
        slippage_price = price * (1 - Config.SLIPPAGE_RATE)
        revenue = info['shares'] * slippage_price
        fee = max(int(revenue * FEE_RATE), MIN_FEE)
        tax = int(revenue * TAX_RATE)
        net = revenue - fee - tax
        
        profit = net - info['total_cost']
        profit_pct = profit / info['total_cost'] * 100
        
        self.capital += net
        
        # 記錄交易
        self.trades.append({
            'stock_id': stock_id,
            'buy_date': info['buy_date'],
            'sell_date': date_str,
            'buy_price': info['cost'],
            'sell_price': slippage_price,  # V32: 記錄滑價後的實際賣價
            'profit_pct': profit_pct,
            'reason': reason,
            'days': info['days']
        })
        
        del self.positions[stock_id]
        
        self.trade_count += 1
        if profit_pct > 0:
            self.win_count += 1
        
        emoji = "🔴" if profit_pct < 0 else "🟢"
        print(f"{emoji} {date_str} {reason} {stock_id} | {profit_pct:+.2f}% ({info['days']}天)")
    
    def run(self):
        """執行回測"""
        mode_name = "V31 混合策略" if self.mode == 'v31' else "V30 純技術"
        print("=" * 60)
        print(f"🚀 {mode_name} 回測")
        print("=" * 60)
        
        # 取得交易日期
        with self.engine.connect() as conn:
            dates = conn.execute(text(f"""
                SELECT DISTINCT trade_date 
                FROM daily_market_data 
                WHERE trade_date >= '{BACKTEST_START}' 
                ORDER BY trade_date
            """)).fetchall()
        
        date_list = [d[0].strftime("%Y-%m-%d") for d in dates]
        print(f"📅 回測期間: {date_list[0]} ~ {date_list[-1]} ({len(date_list)} 天)")
        print("-" * 60)
        
        for date_str in date_list:
            trend = self.get_market_trend(date_str)
            
            # V32: 記錄每日資產（用於 MDD 和 Sharpe 計算）
            daily_asset = self.capital
            for sid, info in self.positions.items():
                curr = self.get_data(sid, date_str)
                if curr:
                    daily_asset += info['shares'] * curr['close_price']
            self.daily_assets.append(daily_asset)
            self.daily_dates.append(date_str)
            
            # === 賣出邏輯 ===
            for sid in list(self.positions.keys()):
                self.positions[sid]['days'] += 1
                curr = self.get_data(sid, date_str)
                if not curr:
                    continue
                
                curr_price = curr['close_price']
                cost = self.positions[sid]['cost']
                change = (curr_price - cost) / cost
                
                # 更新最高價
                if curr_price > self.positions[sid]['highest']:
                    self.positions[sid]['highest'] = curr_price
                
                # ============================================
                # 🔥 V31 Optimization: 階梯式移動停損
                # ============================================
                old_stop = self.positions[sid]['stop_loss']
                
                if change >= 0.30:
                    # Level 3: 獲利 >= 30%，鎖定 25% 利潤
                    new_stop = cost * 1.25
                    if new_stop > old_stop:
                        self.positions[sid]['stop_loss'] = new_stop
                        print(f"  🔒 {sid} 進入 Level 3，停損上移至 {new_stop:.2f} (鎖定+25%)")
                
                elif change >= 0.20:
                    # Level 2: 獲利 >= 20%，鎖定 15% 利潤
                    new_stop = cost * 1.15
                    if new_stop > old_stop:
                        self.positions[sid]['stop_loss'] = new_stop
                        print(f"  🔒 {sid} 進入 Level 2，停損上移至 {new_stop:.2f} (鎖定+15%)")
                
                elif change >= 0.10:
                    # Level 1: 獲利 >= 10%，保本 + 手續費
                    new_stop = cost * 1.01
                    if new_stop > old_stop:
                        self.positions[sid]['stop_loss'] = new_stop
                        print(f"  🔒 {sid} 進入 Level 1，停損上移至 {new_stop:.2f} (保本+1%)")
                
                # 判斷賣出
                if curr_price <= self.positions[sid]['stop_loss']:
                    self.sell(sid, curr_price, date_str, "停損")
                elif self.take_profit_pct > 0 and change >= self.take_profit_pct:
                    self.sell(sid, curr_price, date_str, "停利")
                elif self.positions[sid]['days'] >= self.max_hold_days:
                    self.sell(sid, curr_price, date_str, "時間到")
                elif trend == 'BEAR' and change < 0:
                    self.sell(sid, curr_price, date_str, "趨勢轉空")
            
            # === 買入邏輯 ===
            if trend == 'BULL':
                candidates = self.find_candidates(date_str)
                for sid in candidates:
                    if sid not in self.positions:
                        data = self.get_data(sid, date_str)
                        if data:
                            self.buy(sid, data['close_price'], date_str)
        
        # === 結算未平倉 ===
        final = self.capital
        for sid, info in self.positions.items():
            with self.engine.connect() as conn:
                query = text(f"""
                    SELECT close_price FROM daily_market_data 
                    WHERE stock_id='{sid}' 
                    ORDER BY trade_date DESC LIMIT 1
                """)
                price = conn.execute(query).scalar() or info['cost']
            final += info['shares'] * price
        
        # === 統計結果 ===
        roi = (final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        win_rate = (self.win_count / self.trade_count * 100) if self.trade_count > 0 else 0
        
        # 計算平均持有天數和盈虧比
        if self.trades:
            avg_days = sum(t['days'] for t in self.trades) / len(self.trades)
            wins = [t['profit_pct'] for t in self.trades if t['profit_pct'] > 0]
            losses = [abs(t['profit_pct']) for t in self.trades if t['profit_pct'] < 0]
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 1
            profit_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        else:
            avg_days = 0
            profit_ratio = 0
        
        # ==========================================
        # V32: 計算風險指標 (MDD & Sharpe Ratio)
        # ==========================================
        max_drawdown = 0
        sharpe_ratio = 0
        
        if len(self.daily_assets) > 1:
            # 計算最大回撤 (MDD)
            peak = self.daily_assets[0]
            for asset in self.daily_assets:
                if asset > peak:
                    peak = asset
                drawdown = (peak - asset) / peak if peak > 0 else 0
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
            
            # 計算 Sharpe Ratio
            daily_returns = []
            for i in range(1, len(self.daily_assets)):
                ret = (self.daily_assets[i] - self.daily_assets[i-1]) / self.daily_assets[i-1]
                daily_returns.append(ret)
            
            if daily_returns:
                import numpy as np
                avg_return = np.mean(daily_returns)
                std_return = np.std(daily_returns)
                
                # 年化報酬與波動 (假設 252 個交易日)
                annualized_return = avg_return * 252
                annualized_std = std_return * np.sqrt(252)
                
                if annualized_std > 0:
                    sharpe_ratio = (annualized_return - Config.RISK_FREE_RATE) / annualized_std
        
        print("\n" + "=" * 60)
        print(f"💰 {mode_name} 回測結果 (V32 擬真版)")
        print("=" * 60)
        print(f"📊 初始資金: ${INITIAL_CAPITAL:,}")
        print(f"📊 最終資產: ${int(final):,}")
        print(f"📈 報酬率: {roi:+.2f}%")
        print("-" * 60)
        print(f"📊 交易次數: {self.trade_count}")
        print(f"🎯 勝率: {win_rate:.1f}%")
        print(f"📊 盈虧比: {profit_ratio:.2f}")
        print(f"⏱️ 平均持有: {avg_days:.1f} 天")
        print("-" * 60)
        print(f"📉 最大回撤 (MDD): {max_drawdown*100:.2f}%")
        print(f"📊 夏普比率 (Sharpe): {sharpe_ratio:.3f}")
        print(f"💸 滑價成本: {Config.SLIPPAGE_RATE*100:.1f}% (買高賣低)")
        print("=" * 60)
        
        # 輸出交易明細到 CSV
        if self.trades:
            df_trades = pd.DataFrame(self.trades)
            output_path = 'ML_Data/backtest_result.csv'
            df_trades.to_csv(output_path, index=False, encoding='utf-8-sig')
            print(f"📄 交易明細已輸出至: {output_path}")
        
        # V32: 輸出每日資產曲線（用於 Dashboard 視覺化）
        if self.daily_assets:
            df_profit = pd.DataFrame({
                'date': self.daily_dates,
                'asset_value': self.daily_assets,
                'roi': [(a - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100 for a in self.daily_assets]
            })
            profit_path = 'ML_Data/backtest_profit_report.csv'
            df_profit.to_csv(profit_path, index=False, encoding='utf-8-sig')
            print(f"📈 資產曲線已輸出至: {profit_path}")
        
        return roi


def main():
    """主程式入口"""
    # 解析命令列參數
    mode = 'v31'  # 預設 V31
    
    if '--v30' in sys.argv:
        mode = 'v30'
    elif '--v31' in sys.argv:
        mode = 'v31'
    
    # 執行回測
    engine = BacktestEngine(mode=mode)
    engine.run()


if __name__ == "__main__":
    main()
