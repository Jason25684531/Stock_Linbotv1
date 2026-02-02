"""
視覺化輔助模組 (Visualization Helper)
============================================
使用 Plotly 繪製互動式圖表供 Web Dashboard 使用

功能：
1. 權益曲線圖 (Equity Curve)
2. 回撤圖 (Drawdown Chart)
3. 月度報酬熱力圖 (Monthly Returns Heatmap)
4. 績效指標卡片資料

作者：Stock AI Bot Team
最後更新：2026-02-02
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import json
from typing import Dict, Any, Optional, List


class PerformanceVisualizer:
    """績效視覺化器
    
    將回測結果轉換為 Plotly 互動式圖表
    """
    
    def __init__(self, equity_data: pd.DataFrame, trades_data: Optional[pd.DataFrame] = None):
        """初始化視覺化器
        
        Args:
            equity_data: 權益曲線資料 (需包含 date, asset_value, roi 欄位)
            trades_data: 交易明細資料 (需包含 buy_date, sell_date, profit_pct 等)
        """
        self.equity_data = equity_data
        self.trades_data = trades_data
        
        # 計算衍生指標
        self._calculate_metrics()
    
    def _calculate_metrics(self):
        """計算績效指標"""
        if self.equity_data.empty:
            self.metrics = {
                'total_return': 0,
                'cagr': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0
            }
            return
        
        # 總報酬率
        final_roi = self.equity_data['roi'].iloc[-1] if 'roi' in self.equity_data.columns else 0
        
        # CAGR (年化複合成長率)
        days = len(self.equity_data)
        years = days / 252  # 假設 252 個交易日/年
        if years > 0 and final_roi != 0:
            cagr = ((1 + final_roi / 100) ** (1 / years) - 1) * 100
        else:
            cagr = 0
        
        # 最大回撤 (MDD)
        peak = self.equity_data['asset_value'].iloc[0]
        max_dd = 0
        for value in self.equity_data['asset_value']:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        # Sharpe Ratio
        if len(self.equity_data) > 1:
            daily_returns = self.equity_data['roi'].diff().dropna()
            if len(daily_returns) > 0 and daily_returns.std() > 0:
                avg_return = daily_returns.mean()
                std_return = daily_returns.std()
                # 年化
                annualized_return = avg_return * 252
                annualized_std = std_return * np.sqrt(252)
                sharpe = (annualized_return / 100 - 0.02) / (annualized_std / 100)  # 假設無風險利率 2%
            else:
                sharpe = 0
        else:
            sharpe = 0
        
        # 交易統計
        if self.trades_data is not None and not self.trades_data.empty:
            wins = self.trades_data[self.trades_data['profit_pct'] > 0]
            losses = self.trades_data[self.trades_data['profit_pct'] <= 0]
            
            win_rate = len(wins) / len(self.trades_data) * 100 if len(self.trades_data) > 0 else 0
            avg_win = wins['profit_pct'].mean() if not wins.empty else 0
            avg_loss = abs(losses['profit_pct'].mean()) if not losses.empty else 0
            profit_factor = (avg_win * len(wins)) / (avg_loss * len(losses)) if len(losses) > 0 and avg_loss > 0 else 0
        else:
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            profit_factor = 0
        
        self.metrics = {
            'total_return': round(final_roi, 2),
            'cagr': round(cagr, 2),
            'sharpe_ratio': round(sharpe, 3),
            'max_drawdown': round(max_dd * 100, 2),
            'win_rate': round(win_rate, 1),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2)
        }
    
    def plot_equity_curve(self, benchmark_data: Optional[pd.DataFrame] = None) -> str:
        """繪製權益曲線圖
        
        Args:
            benchmark_data: 基準指數資料 (可選，如 0050 或加權指數)
        
        Returns:
            Plotly 圖表的 JSON 字串
        """
        fig = go.Figure()
        
        # 策略曲線
        fig.add_trace(go.Scatter(
            x=self.equity_data['date'],
            y=self.equity_data['asset_value'],
            mode='lines',
            name='策略組合',
            line=dict(color='#00D9FF', width=2.5),
            hovertemplate='<b>日期</b>: %{x}<br><b>資產</b>: $%{y:,.0f}<extra></extra>'
        ))
        
        # 基準線（如果提供）
        if benchmark_data is not None and not benchmark_data.empty:
            fig.add_trace(go.Scatter(
                x=benchmark_data['date'],
                y=benchmark_data['value'],
                mode='lines',
                name='基準指數',
                line=dict(color='#FF6B6B', width=1.5, dash='dash'),
                hovertemplate='<b>日期</b>: %{x}<br><b>指數</b>: %{y:,.0f}<extra></extra>'
            ))
        
        # 圖表樣式
        fig.update_layout(
            title={
                'text': '📈 投資組合權益曲線',
                'x': 0.5,
                'xanchor': 'center',
                'font': {'size': 20, 'color': '#2C3E50'}
            },
            xaxis_title='日期',
            yaxis_title='資產價值 (TWD)',
            hovermode='x unified',
            template='plotly_white',
            height=500,
            margin=dict(l=60, r=60, t=80, b=60),
            legend=dict(
                orientation='h',
                yanchor='bottom',
                y=1.02,
                xanchor='right',
                x=1
            )
        )
        
        # 格式化 Y 軸為貨幣格式
        fig.update_yaxes(tickformat='$,.0f')
        
        return fig.to_json()
    
    def plot_drawdown(self) -> str:
        """繪製回撤圖 (Underwater Plot)
        
        Returns:
            Plotly 圖表的 JSON 字串
        """
        # 計算回撤序列
        peak = self.equity_data['asset_value'].iloc[0]
        drawdowns = []
        
        for value in self.equity_data['asset_value']:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100 if peak > 0 else 0
            drawdowns.append(-dd)  # 負值表示回撤
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=self.equity_data['date'],
            y=drawdowns,
            mode='lines',
            fill='tozeroy',
            name='回撤',
            line=dict(color='#FF6B6B', width=0),
            fillcolor='rgba(255, 107, 107, 0.3)',
            hovertemplate='<b>日期</b>: %{x}<br><b>回撤</b>: %{y:.2f}%<extra></extra>'
        ))
        
        # 標記最大回撤點
        max_dd_idx = np.argmin(drawdowns)
        fig.add_trace(go.Scatter(
            x=[self.equity_data['date'].iloc[max_dd_idx]],
            y=[drawdowns[max_dd_idx]],
            mode='markers+text',
            name='最大回撤',
            marker=dict(color='red', size=12, symbol='x'),
            text=[f'MDD: {drawdowns[max_dd_idx]:.2f}%'],
            textposition='bottom center',
            showlegend=False
        ))
        
        fig.update_layout(
            title={
                'text': '📉 回撤分析 (Drawdown)',
                'x': 0.5,
                'xanchor': 'center',
                'font': {'size': 20, 'color': '#2C3E50'}
            },
            xaxis_title='日期',
            yaxis_title='回撤百分比 (%)',
            hovermode='x unified',
            template='plotly_white',
            height=400,
            margin=dict(l=60, r=60, t=80, b=60)
        )
        
        return fig.to_json()
    
    def plot_monthly_returns(self) -> str:
        """繪製月度報酬熱力圖
        
        Returns:
            Plotly 圖表的 JSON 字串
        """
        if self.equity_data.empty or len(self.equity_data) < 2:
            # 返回空圖表
            fig = go.Figure()
            fig.add_annotation(
                text="資料不足，無法生成月度報酬圖",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=16, color="gray")
            )
            fig.update_layout(height=300, template='plotly_white')
            return fig.to_json()
        
        # 轉換日期格式
        df = self.equity_data.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df['year'] = df.index.year
        df['month'] = df.index.month
        
        # 計算每月報酬
        monthly_returns = df.groupby(['year', 'month'])['roi'].last().unstack(fill_value=0)
        
        # 月份名稱
        month_names = ['1月', '2月', '3月', '4月', '5月', '6月', 
                       '7月', '8月', '9月', '10月', '11月', '12月']
        
        fig = go.Figure(data=go.Heatmap(
            z=monthly_returns.values,
            x=[month_names[i-1] for i in monthly_returns.columns],
            y=monthly_returns.index.astype(str),
            colorscale='RdYlGn',
            zmid=0,
            text=monthly_returns.values,
            texttemplate='%{text:.1f}%',
            textfont={"size": 10},
            hovertemplate='<b>%{y}年 %{x}</b><br>報酬率: %{z:.2f}%<extra></extra>',
            colorbar=dict(title="報酬率 (%)")
        ))
        
        fig.update_layout(
            title={
                'text': '📅 月度報酬熱力圖',
                'x': 0.5,
                'xanchor': 'center',
                'font': {'size': 20, 'color': '#2C3E50'}
            },
            xaxis_title='月份',
            yaxis_title='年份',
            template='plotly_white',
            height=400,
            margin=dict(l=60, r=60, t=80, b=60)
        )
        
        return fig.to_json()
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """取得績效指標摘要
        
        Returns:
            包含所有績效指標的字典
        """
        return self.metrics
    
    def generate_full_report(self, benchmark_data: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """生成完整回測報告
        
        Args:
            benchmark_data: 基準指數資料 (可選)
        
        Returns:
            包含所有圖表 JSON 和指標的字典
        """
        return {
            'metrics': self.get_metrics_summary(),
            'equity_curve': self.plot_equity_curve(benchmark_data),
            'drawdown': self.plot_drawdown(),
            'monthly_returns': self.plot_monthly_returns(),
            'trade_count': len(self.trades_data) if self.trades_data is not None else 0
        }


def create_simple_equity_chart(dates: List[str], values: List[float], title: str = "權益曲線") -> str:
    """快速建立簡單的權益曲線圖
    
    Args:
        dates: 日期列表
        values: 資產價值列表
        title: 圖表標題
    
    Returns:
        Plotly 圖表的 JSON 字串
    """
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=dates,
        y=values,
        mode='lines',
        name='資產價值',
        line=dict(color='#00D9FF', width=2.5),
        fill='tozeroy',
        fillcolor='rgba(0, 217, 255, 0.1)'
    ))
    
    fig.update_layout(
        title=title,
        xaxis_title='日期',
        yaxis_title='資產價值 (TWD)',
        template='plotly_white',
        height=400
    )
    
    fig.update_yaxes(tickformat='$,.0f')
    
    return fig.to_json()


# ============================================
# 便捷函數：從 CSV 檔案直接生成報告
# ============================================

def generate_report_from_csv(
    equity_csv: str = 'ML_Data/backtest_profit_report.csv',
    trades_csv: str = 'ML_Data/backtest_result.csv',
    benchmark_csv: Optional[str] = None
) -> Dict[str, Any]:
    """從 CSV 檔案生成視覺化報告
    
    Args:
        equity_csv: 權益曲線 CSV 路徑
        trades_csv: 交易明細 CSV 路徑
        benchmark_csv: 基準指數 CSV 路徑 (可選)
    
    Returns:
        完整報告字典
    """
    import os
    
    # 讀取資料
    if not os.path.exists(equity_csv):
        raise FileNotFoundError(f"找不到權益曲線檔案: {equity_csv}")
    
    equity_data = pd.read_csv(equity_csv)
    
    trades_data = None
    if os.path.exists(trades_csv):
        trades_data = pd.read_csv(trades_csv)
    
    benchmark_data = None
    if benchmark_csv and os.path.exists(benchmark_csv):
        benchmark_data = pd.read_csv(benchmark_csv)
    
    # 建立視覺化器
    visualizer = PerformanceVisualizer(equity_data, trades_data)
    
    # 生成報告
    return visualizer.generate_full_report(benchmark_data)
