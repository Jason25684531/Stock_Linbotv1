"""
多策略回測引擎
============================================
支援多種模式：
  1. V30 模式：純技術面策略（不需 AI 模型）
  2. V31 模式：V30 篩選 + AI 模型排名
    3. V33 模式：低波動策略（NATR < 3.5%，趨勢/量能強化）
    4. V34 模式：雙渦輪飆股策略（營收 YoY > 18% + 價格突破）
    5. V35 模式：經營效益策略（營業利益率 > 6%）

用法：
  python 4_run_backtest.py          # 預設 V31 模式
  python 4_run_backtest.py --v30    # 純 V30 模式
  python 4_run_backtest.py --v31    # V31 混合模式
  python 4_run_backtest.py --v33    # V33 低波動
  python 4_run_backtest.py --v34    # V34 雙渦輪飆股
  python 4_run_backtest.py --v35    # V35 經營效益
  python 4_run_backtest.py --portfolio --strategies v33_low_vol,v34_turbo
"""
import pandas as pd
from sqlalchemy import text
import joblib
import os
import sys
from config import Config
from tool.strategy import get_v30_candidates, get_v30_params_from_db
from tool.db_helper import get_db_engine, get_market_trend as db_get_market_trend
from tool.strategy_manager import StrategyManager

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


MODEL_PATH = Config.MODEL_PATH
MARKET_SYMBOL = Config.MARKET_SYMBOL
BOND_SYMBOL = Config.BOND_SYMBOL


class BacktestEngine:
    """
    多策略回測引擎
    支援 V30（純技術）、V31（技術 + AI）及多策略模式（v33/v34/v35 各自載入專屬模型）
    """
    
    def __init__(self, mode='v31'):
        """
        初始化回測引擎
        
        Args:
            mode: 'v30' = 純技術面, 'v31' = 技術 + AI,
                  'v33_low_vol' / 'v34_turbo' / 'v35_innovation' = 策略專屬模型
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
        
        # 🔥 快取：避免回測 177 天重複查詢同一張表
        self._revenue_cache = None      # monthly_revenue 快取
        self._financial_cache = None    # financial_statements 快取
        
        # 載入策略物件（用於 check_exit_signal 委派）
        self.strategy_obj = self._load_strategy_object()
        
        # 載入策略參數
        self._load_params()
        
        # V30 以外的模式都嘗試載入 AI 模型（各策略載入專屬模型）
        self.model = None
        self.features = None
        if self.mode != 'v30':
            self._load_model()
    
    def _load_strategy_object(self):
        """載入策略物件實例（用於 check_exit_signal 委派）
        
        支援短名稱映射：v31 → v31_hybrid, v33 → v33_low_vol 等
        """
        # 短名稱 → 完整名稱映射
        MODE_ALIAS = {
            'v30': 'v31_hybrid',   # V30 使用 V31 篩選（不含 AI 評分）
            'v31': 'v31_hybrid',
            'v33': 'v33_low_vol',
            'v34': 'v34_turbo',
            'v35': 'v35_innovation',
            'v36': 'v36_chip_momentum',
            'v37': 'v37_mean_reversion',
            'v38': 'v38_value_dividend',
        }
        
        registry_name = MODE_ALIAS.get(self.mode, self.mode)
        
        try:
            mgr = StrategyManager()
            if registry_name in mgr.STRATEGY_REGISTRY:
                return mgr._get_or_load_strategy(registry_name)
        except Exception as e:
            print(f"⚠️ 策略物件載入失敗 ({self.mode}): {e}")
        return None

    def _load_params(self):
        """載入策略參數（優先從策略物件，其次資料庫，最後 Config）
        
        注意：V30 模式雖然借用 V31 策略篩選，但使用 DB/Config 參數
        """
        # V30 模式：使用資料庫或 Config 參數
        if self.mode == 'v30':
            if USE_DB_PARAMS:
                params = get_v30_params_from_db()
                self.stop_loss_pct = params['STOP_LOSS']
                self.take_profit_pct = params['TAKE_PROFIT']
                self.max_hold_days = params['MAX_HOLD_DAYS']
            else:
                self.stop_loss_pct = Config.V30_PARAMS['STOP_LOSS']
                self.take_profit_pct = Config.V30_PARAMS['TAKE_PROFIT']
                self.max_hold_days = Config.V30_PARAMS['MAX_HOLD_DAYS']
        elif self.strategy_obj is not None:
            # 其他策略：使用策略物件定義的參數
            self.stop_loss_pct = self.strategy_obj.stop_loss
            self.take_profit_pct = self.strategy_obj.take_profit
            self.max_hold_days = self.strategy_obj.max_hold_days
        elif USE_DB_PARAMS:
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
    
    def _get_model_path(self) -> str:
        """根據策略模式取得對應的模型檔案路徑
        
        Returns:
            模型檔案路徑
        """
        if self.mode == 'v31':
            # V31 使用預設模型
            return MODEL_PATH
        
        # 策略專屬模型：ML_Data/pkl/stock_ai_model_{strategy_name}.pkl
        model_dir = os.path.dirname(MODEL_PATH)
        strategy_model = os.path.join(model_dir, f'stock_ai_model_{self.mode}.pkl')
        
        if os.path.exists(strategy_model):
            return strategy_model
        
        # Fallback: 通用模型
        print(f"⚠️ 找不到 {self.mode} 專屬模型，嘗試載入通用模型")
        return MODEL_PATH
    
    def _load_model(self):
        """動態載入 AI 模型（根據策略模式選擇對應檔案）"""
        model_path = self._get_model_path()
        
        if not os.path.exists(model_path):
            print(f"⚠️ 找不到 AI 模型 ({model_path})，切換為純規則模式")
            return
        
        try:
            data = joblib.load(model_path)
            if isinstance(data, dict) and 'model' in data:
                self.model = data['model']
                self.features = data.get('features', Config.FEATURES)
                print(f"🧠 [{self.mode}] AI 模型載入成功！({len(self.features)} 個特徵, {os.path.basename(model_path)})")
            else:
                # 舊格式相容
                self.model = data
                self.features = Config.FEATURES
                print(f"🧠 [{self.mode}] AI 模型載入成功（舊格式）")
        except Exception as e:
            print(f"⚠️ [{self.mode}] AI 模型載入失敗: {e}，切換為純規則模式")
    
    def get_data(self, stock_id, date_str):
        """取得個股當日資料"""
        with self.engine.connect() as conn:
            query = text("""
                SELECT * FROM daily_market_data 
                WHERE stock_id = :sid AND trade_date = :dt
            """)
            return conn.execute(query, {'sid': stock_id, 'dt': date_str}).mappings().fetchone()
    
    def check_and_execute_exit(self, sid: str, date_str: str, trend: str):
        """共用出場檢查邏輯（委派策略物件或 fallback）
        
        同時被 BacktestEngine.run() 和 PortfolioBacktestEngine 使用，
        消除重複的停損/停利 if-else 判斷區塊。
        
        Args:
            sid: 股票代碼
            date_str: 當前日期
            trend: 市場趨勢 ('BULL'/'BEAR'/'NEUTRAL')
        """
        self.positions[sid]['days'] += 1
        curr = self.get_data(sid, date_str)
        if not curr:
            return
        
        curr_price = curr['close_price']
        
        # 更新最高價
        if curr_price > self.positions[sid]['highest']:
            self.positions[sid]['highest'] = curr_price
        
        # 委派策略物件判斷出場
        if self.strategy_obj is not None:
            action, reason, new_stop = self.strategy_obj.check_exit_signal(
                stock_id=sid,
                current_price=curr_price,
                current_date=date_str,
                position_info=self.positions[sid],
                market_trend=trend
            )
            self.positions[sid]['stop_loss'] = new_stop
            if action == 'SELL':
                self.sell(sid, curr_price, date_str, reason)
        else:
            # Fallback: 基本停損停利邏輯
            cost = self.positions[sid]['cost']
            change = (curr_price - cost) / cost
            
            if curr_price <= self.positions[sid]['stop_loss']:
                self.sell(sid, curr_price, date_str, "停損")
            elif self.take_profit_pct > 0 and change >= self.take_profit_pct:
                self.sell(sid, curr_price, date_str, "停利")
            elif self.positions[sid]['days'] >= self.max_hold_days:
                self.sell(sid, curr_price, date_str, "時間到")
            elif trend == 'BEAR' and change < 0:
                self.sell(sid, curr_price, date_str, "趨勢轉空")
    
    def get_market_trend(self, date_str):
        """判斷大盤趨勢（使用共用函數）"""
        try:
            return db_get_market_trend(date_str)
        except Exception as e:
            print(f"⚠️ 市場趨勢判斷失敗: {e}")
            return 'NEUTRAL'
    
    def find_candidates(self, date_str):
        """
        尋找候選股票
        
        🔥 核心邏輯：
        - 使用 self.strategy_obj.filter_candidates() 篩選
        - AI 模式（有模型時）：加入 AI 評分排序
        - 使用快取避免重複查詢 monthly_revenue / financial_statements
        """
        # 🔥 統一使用單一連線（避免 Too many connections）
        with self.engine.connect() as conn:
            # 基礎查詢
            query = text("""
                SELECT * FROM daily_market_data
                WHERE trade_date = :dt
                AND stock_id NOT IN (:bond, :market, '00632R')
                AND close_price > 10
                AND close_price < 500
            """)
            df = pd.read_sql(query, conn, params={
                'dt': date_str, 'bond': BOND_SYMBOL, 'market': MARKET_SYMBOL
            })
            
            if df.empty:
                return []
            
            # 補充 volume_ratio（DB 無此欄位）
            if 'volume' in df.columns and 'volume_ratio' not in df.columns:
                df['volume_ratio'] = 1.0
                try:
                    vol_query = text("""
                        SELECT stock_id, AVG(volume) as vol_ma20
                        FROM daily_market_data
                        WHERE trade_date <= :dt
                        AND trade_date >= DATE_SUB(:dt, INTERVAL 40 DAY)
                        GROUP BY stock_id
                    """)
                    vol_df = pd.read_sql(vol_query, conn, params={'dt': date_str})
                    if not vol_df.empty:
                        vol_map = vol_df.set_index('stock_id')['vol_ma20'].to_dict()
                        df['volume_ratio'] = df.apply(
                            lambda r: r['volume'] / vol_map.get(r['stock_id'], r['volume']) 
                            if vol_map.get(r['stock_id'], 0) > 0 else 1.0, axis=1
                        )
                except Exception:
                    pass
            
            # 補充 revenue_yoy（使用快取，整個回測只查一次）
            if 'revenue_yoy' not in df.columns or df['revenue_yoy'].isna().all() or (df['revenue_yoy'] == 0).all():
                if self._revenue_cache is None:
                    try:
                        rev_query = text("""
                            SELECT mr1.stock_id, mr1.revenue_yoy
                            FROM monthly_revenue mr1
                            INNER JOIN (
                                SELECT stock_id, MAX(year * 100 + month) as max_period
                                FROM monthly_revenue
                                GROUP BY stock_id
                            ) mr2 ON mr1.stock_id = mr2.stock_id 
                                 AND (mr1.year * 100 + mr1.month) = mr2.max_period
                        """)
                        rev_df = pd.read_sql(rev_query, conn)
                        if not rev_df.empty:
                            rev_df['revenue_yoy'] = rev_df['revenue_yoy'].clip(-100, 500)
                            self._revenue_cache = rev_df.set_index('stock_id')['revenue_yoy'].to_dict()
                        else:
                            self._revenue_cache = {}
                    except Exception:
                        self._revenue_cache = {}
                
                df['revenue_yoy'] = df['stock_id'].map(self._revenue_cache).fillna(0)
            
            # 補充 op_profit_margin + eps（使用快取）
            if 'op_profit_margin' not in df.columns or df['op_profit_margin'].isna().all() or (df['op_profit_margin'] == 0).all():
                if self._financial_cache is None:
                    try:
                        fin_query = text("""
                            SELECT fs1.stock_id, 
                                   fs1.operating_margin / 100 as op_profit_margin, 
                                   fs1.eps
                            FROM financial_statements fs1
                            INNER JOIN (
                                SELECT stock_id, MAX(year * 10 + quarter) as max_period
                                FROM financial_statements
                                GROUP BY stock_id
                            ) fs2 ON fs1.stock_id = fs2.stock_id 
                                 AND (fs1.year * 10 + fs1.quarter) = fs2.max_period
                        """)
                        fin_df = pd.read_sql(fin_query, conn)
                        if not fin_df.empty:
                            self._financial_cache = {
                                'op_margin': fin_df.set_index('stock_id')['op_profit_margin'].to_dict(),
                                'eps': fin_df.set_index('stock_id')['eps'].to_dict(),
                            }
                        else:
                            self._financial_cache = {'op_margin': {}, 'eps': {}}
                    except Exception:
                        self._financial_cache = {'op_margin': {}, 'eps': {}}
                
                df['op_profit_margin'] = df['stock_id'].map(self._financial_cache['op_margin']).fillna(0)
                if 'eps' not in df.columns or df['eps'].isna().all():
                    df['eps'] = df['stock_id'].map(self._financial_cache['eps']).fillna(0)
        
        # 依據策略模式選擇篩選邏輯
        if self.strategy_obj is not None:
            try:
                candidates = self.strategy_obj.filter_candidates(df)
            except Exception as e:
                print(f"⚠️ [{self.mode}] 策略篩選失敗: {e}")
                return []
        else:
            candidates = get_v30_candidates(df)
        
        if candidates.empty:
            return []
        
        # AI 模式：加入 AI 評分排序
        if self.model is not None:
            for f in self.features:
                if f not in candidates.columns:
                    candidates[f] = 0
            
            X = candidates[self.features].fillna(0)
            
            try:
                probs = self.model.predict_proba(X)[:, 1]
                candidates['ai_score'] = probs
                
                high_conf = candidates[candidates['ai_score'] >= AI_CONFIDENCE_THRESHOLD]
                if not high_conf.empty:
                    candidates = high_conf.sort_values('ai_score', ascending=False)
                else:
                    candidates = candidates.sort_values('ai_score', ascending=False)
            except Exception as e:
                print(f"⚠️ AI 預測失敗: {e}")
        else:
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
        # 🔥 根據模式顯示正確名稱
        if self.mode == 'v30':
            mode_name = "V30 純技術"
        elif self.strategy_obj is not None:
            mode_name = self.strategy_obj.display_name
        elif self.mode == 'v31':
            mode_name = "V31 混合策略"
        else:
            mode_name = f"{self.mode.upper()} 策略"
        
        print("=" * 60)
        print(f"🚀 {mode_name} 回測")
        print("=" * 60)
        
        # 取得交易日期
        with self.engine.connect() as conn:
            dates = conn.execute(text("""
                SELECT DISTINCT trade_date 
                FROM daily_market_data 
                WHERE trade_date >= :start_date 
                ORDER BY trade_date
            """), {'start_date': BACKTEST_START}).fetchall()
        
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
                    last_price = curr['close_price']
                    info['last_price'] = last_price  # 快取最後已知價格
                else:
                    last_price = info.get('last_price', info.get('cost', 0))
                daily_asset += info['shares'] * last_price
            self.daily_assets.append(daily_asset)
            self.daily_dates.append(date_str)
            
            # === 賣出邏輯（委派 check_and_execute_exit 統一處理）===
            for sid in list(self.positions.keys()):
                self.check_and_execute_exit(sid, date_str, trend)
            
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
                query = text("""
                    SELECT close_price FROM daily_market_data 
                    WHERE stock_id = :sid 
                    ORDER BY trade_date DESC LIMIT 1
                """)
                price = conn.execute(query, {'sid': sid}).scalar() or info['cost']
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
        print(f"💰 {mode_name} 回測結果")
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


class PortfolioBacktestEngine:
    """
    多策略投資組合回測引擎 (Phase 5)
    ============================================
    支援同時回測多個策略，並將資金平均分配
    
    用法:
        engine = PortfolioBacktestEngine(
            strategies=['v33_low_vol', 'v35_innovation'],
            start_date='2025-06-01',
            end_date='2026-01-31'
        )
        result = engine.run_portfolio_backtest()
    """
    
    def __init__(self, strategies: list, start_date: str, end_date: str = None, initial_capital: float = 1000000):
        """初始化組合回測引擎
        
        Args:
            strategies: 策略名稱列表 (如 ['v33_low_vol', 'v35_innovation'])
            start_date: 回測起始日
            end_date: 回測結束日 (None = 最新日期)
            initial_capital: 初始資金
        """
        self.strategy_names = strategies
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        
        # 為每個策略建立獨立的回測引擎
        self.engines = {}
        capital_per_strategy = initial_capital / len(strategies)
        
        for strategy_name in strategies:
            # 建立該策略的回測引擎
            engine = BacktestEngine(mode=strategy_name)
            engine.capital = capital_per_strategy
            self.engines[strategy_name] = engine
        
        print(f"📊 組合回測：{len(strategies)} 個策略，每策略分配 ${capital_per_strategy:,.0f}")
    
    def run_portfolio_backtest(self) -> dict:
        """執行投資組合回測
        
        Returns:
            dict: {
                'equity_curve': DataFrame,  # 每日資產曲線
                'trades': List,              # 所有交易記錄
                'metrics': Dict,             # 績效指標
                'strategy_performance': Dict # 各策略績效
            }
        """
        from tool.db_helper import get_db_engine
        from sqlalchemy import text
        
        print("=" * 60)
        print(f"🚀 多策略組合回測")
        print(f"📅 策略: {', '.join(self.strategy_names)}")
        print("=" * 60)
        
        # 取得交易日期
        engine = get_db_engine()
        with engine.connect() as conn:
            params = {'start_date': self.start_date}
            query_str = """
                SELECT DISTINCT trade_date 
                FROM daily_market_data 
                WHERE trade_date >= :start_date
            """
            if self.end_date:
                query_str += " AND trade_date <= :end_date"
                params['end_date'] = self.end_date
            query_str += " ORDER BY trade_date"
            
            dates = conn.execute(text(query_str), params).fetchall()
        
        date_list = [d[0].strftime("%Y-%m-%d") for d in dates]
        print(f"📅 回測期間: {date_list[0]} ~ {date_list[-1]} ({len(date_list)} 天)")
        print("-" * 60)
        
        # 記錄每日總資產
        daily_portfolio_value = []
        daily_dates = []
        
        # 模擬每日交易
        for date_str in date_list:
            # 每個策略獨立執行交易邏輯
            daily_total = 0
            
            for strategy_name, strategy_engine in self.engines.items():
                # 取得該策略當日資產
                strategy_value = strategy_engine.capital
                
                # 加上持倉市值（若當日無資料，使用最後已知價格）
                for stock_id, position_info in strategy_engine.positions.items():
                    curr_data = strategy_engine.get_data(stock_id, date_str)
                    if curr_data:
                        last_price = curr_data['close_price']
                        position_info['last_price'] = last_price
                    else:
                        last_price = position_info.get('last_price', position_info.get('cost', 0))
                    strategy_value += position_info['shares'] * last_price
                
                daily_total += strategy_value
                
                # 執行該策略的交易邏輯（簡化版，只執行一天）
                trend = strategy_engine.get_market_trend(date_str)
                
                # 賣出邏輯 - 委派共用 check_and_execute_exit
                for sid in list(strategy_engine.positions.keys()):
                    strategy_engine.check_and_execute_exit(sid, date_str, trend)
                
                # 買入邏輯
                if trend == 'BULL':
                    candidates = strategy_engine.find_candidates(date_str)
                    for sid in candidates:
                        if sid not in strategy_engine.positions:
                            data = strategy_engine.get_data(sid, date_str)
                            if data:
                                strategy_engine.buy(sid, data['close_price'], date_str)
            
            daily_portfolio_value.append(daily_total)
            daily_dates.append(date_str)
        
        # 彙總結果
        all_trades = []
        strategy_performance = {}
        
        for strategy_name, strategy_engine in self.engines.items():
            # 收集該策略的交易記錄
            for trade in strategy_engine.trades:
                trade['strategy'] = strategy_name
                all_trades.append(trade)
            
            # 計算該策略的最終資產
            final_value = strategy_engine.capital
            for sid, info in strategy_engine.positions.items():
                with get_db_engine().connect() as conn:
                    query = text("""
                        SELECT close_price FROM daily_market_data 
                        WHERE stock_id = :sid 
                        ORDER BY trade_date DESC LIMIT 1
                    """)
                    price = conn.execute(query, {'sid': sid}).scalar() or info['cost']
                final_value += info['shares'] * price
            
            strategy_roi = (final_value - (self.initial_capital / len(self.strategy_names))) / (self.initial_capital / len(self.strategy_names)) * 100
            
            strategy_performance[strategy_name] = {
                'final_value': final_value,
                'roi': strategy_roi,
                'trade_count': strategy_engine.trade_count,
                'win_count': strategy_engine.win_count,
                'win_rate': (strategy_engine.win_count / strategy_engine.trade_count * 100) if strategy_engine.trade_count > 0 else 0
            }
        
        # 組合績效
        final_portfolio = daily_portfolio_value[-1] if daily_portfolio_value else self.initial_capital
        total_roi = (final_portfolio - self.initial_capital) / self.initial_capital * 100
        
        # 建立權益曲線 DataFrame
        equity_df = pd.DataFrame({
            'date': daily_dates,
            'asset_value': daily_portfolio_value,
            'roi': [(v - self.initial_capital) / self.initial_capital * 100 for v in daily_portfolio_value]
        })
        
        # 計算組合指標
        trade_count = sum(perf['trade_count'] for perf in strategy_performance.values())
        win_count = sum(perf['win_count'] for perf in strategy_performance.values())
        win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0
        
        # 計算 MDD
        max_dd = 0
        peak = daily_portfolio_value[0] if daily_portfolio_value else self.initial_capital
        for value in daily_portfolio_value:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        # 計算 Sharpe
        sharpe = 0
        if len(daily_portfolio_value) > 1:
            returns = [(daily_portfolio_value[i] - daily_portfolio_value[i-1]) / daily_portfolio_value[i-1] 
                      for i in range(1, len(daily_portfolio_value))]
            if returns:
                import numpy as np
                avg_ret = np.mean(returns)
                std_ret = np.std(returns)
                if std_ret > 0:
                    sharpe = (avg_ret * 252 - 0.02) / (std_ret * np.sqrt(252))
        
        metrics = {
            'total_return': round(total_roi, 2),
            'max_drawdown': round(max_dd * 100, 2),
            'sharpe_ratio': round(sharpe, 3),
            'trade_count': trade_count,
            'win_rate': round(win_rate, 1),
            'final_value': round(final_portfolio, 0),
            'initial_capital': self.initial_capital
        }
        
        # 輸出結果
        print("\n" + "=" * 60)
        print(f"💰 投資組合回測結果")
        print("=" * 60)
        print(f"📊 初始資金: ${self.initial_capital:,.0f}")
        print(f"📊 最終資產: ${int(final_portfolio):,.0f}")
        print(f"📈 總報酬率: {total_roi:+.2f}%")
        print("-" * 60)
        print(f"📊 總交易次數: {trade_count}")
        print(f"🎯 組合勝率: {win_rate:.1f}%")
        print(f"📉 最大回撤: {max_dd*100:.2f}%")
        print(f"📊 夏普比率: {sharpe:.3f}")
        print("-" * 60)
        print("各策略績效:")
        for strategy_name, perf in strategy_performance.items():
            print(f"  • {strategy_name}: ROI={perf['roi']:+.2f}%, 勝率={perf['win_rate']:.1f}%, 交易={perf['trade_count']}次")
        print("=" * 60)
        
        # 儲存結果
        equity_df.to_csv('ML_Data/backtest_profit_report.csv', index=False, encoding='utf-8-sig')
        if all_trades:
            pd.DataFrame(all_trades).to_csv('ML_Data/backtest_result.csv', index=False, encoding='utf-8-sig')
        
        return {
            'equity_curve': equity_df,
            'trades': all_trades,
            'metrics': metrics,
            'strategy_performance': strategy_performance
        }


def main():
    """主程式入口"""
    # 解析命令列參數
    mode = 'v31'  # 預設 V31
    
    if '--v30' in sys.argv:
        mode = 'v30'
    elif '--v31' in sys.argv:
        mode = 'v31'
    elif '--v33' in sys.argv:
        mode = 'v33_low_vol'
    elif '--v34' in sys.argv:
        mode = 'v34_turbo'
    elif '--v35' in sys.argv:
        mode = 'v35_innovation'
    elif '--v36' in sys.argv:
        mode = 'v36_chip_momentum'
    elif '--v37' in sys.argv:
        mode = 'v37_mean_reversion'
    elif '--v38' in sys.argv:
        mode = 'v38_value_dividend'
    elif '--portfolio' in sys.argv:
        # 多策略組合模式
        strategies = ['v33_low_vol', 'v35_innovation']  # 預設組合
        if '--strategies' in sys.argv:
            idx = sys.argv.index('--strategies')
            if idx + 1 < len(sys.argv):
                strategies = sys.argv[idx + 1].split(',')
        
        engine = PortfolioBacktestEngine(
            strategies=strategies,
            start_date=BACKTEST_START
        )
        engine.run_portfolio_backtest()
        return
    
    # 執行單一策略回測
    engine = BacktestEngine(mode=mode)
    engine.run()


if __name__ == "__main__":
    main()