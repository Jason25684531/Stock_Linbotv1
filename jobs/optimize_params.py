"""
V33 Phase 2: 策略參數最佳化腳本

使用 Optuna 框架進行超參數最佳化，目標是找出最佳的策略參數組合。

最佳化目標：
- ROI (Return on Investment) - 總報酬率
- Sharpe Ratio - 風險調整後報酬
- MDD (Max Drawdown) - 最大回撤控制

使用方式：
    python jobs/optimize_params.py --objective roi --n-trials 100
"""
import argparse
import pandas as pd
import numpy as np
from datetime import datetime
import optuna
from optuna.samplers import TPESampler
from typing import Dict, Any
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import Config
from core.strategy import get_v30_candidates
from core.db_helper import get_stock_data


def run_backtest_with_params(params: Dict[str, Any], df: pd.DataFrame) -> Dict[str, float]:
    """
    使用給定參數執行回測
    
    Args:
        params: 策略參數字典
        df: 股價數據
    
    Returns:
        Dict: 包含 roi, sharpe, mdd 的績效指標
    """
    # 模擬簡單回測邏輯（實際應調用完整回測引擎）
    try:
        # 使用參數覆蓋 Config（暫時）
        original_values = {}
        for key, value in params.items():
            if hasattr(Config, key):
                original_values[key] = getattr(Config, key)
                setattr(Config, key, value)
        
        # 執行選股邏輯
        candidates = get_v30_candidates(df)
        
        # 恢復原始 Config
        for key, value in original_values.items():
            setattr(Config, key, value)
        
        # 簡化績效計算（實際應使用 4_run_backtest.py 的完整邏輯）
        if candidates.empty:
            return {'roi': 0.0, 'sharpe': 0.0, 'mdd': -1.0}
        
        # 假設每檔股票平均報酬率為 5%，風險為 2%（示例值）
        avg_return = len(candidates) * 0.05
        volatility = 0.02 * np.sqrt(len(candidates))
        sharpe = (avg_return - Config.RISK_FREE_RATE) / volatility if volatility > 0 else 0
        
        return {
            'roi': avg_return,
            'sharpe': sharpe,
            'mdd': -0.1  # 示例值
        }
    
    except Exception as e:
        print(f"❌ 回測執行失敗: {e}")
        return {'roi': -999, 'sharpe': -999, 'mdd': -999}


def objective_roi(trial: optuna.Trial) -> float:
    """
    最佳化目標：最大化 ROI
    
    Args:
        trial: Optuna trial 物件
    
    Returns:
        float: ROI 值（越大越好）
    """
    # 定義參數搜索空間
    params = {
        'V30_RSI_LOW': trial.suggest_int('V30_RSI_LOW', 20, 50),
        'V30_RSI_HIGH': trial.suggest_int('V30_RSI_HIGH', 60, 80),
        'V30_VOLUME_THRESHOLD': trial.suggest_int('V30_VOLUME_THRESHOLD', 2_000_000, 5_000_000, step=500_000),
        'V30_STOP_LOSS': trial.suggest_float('V30_STOP_LOSS', 0.05, 0.15, step=0.01),
        'V30_TAKE_PROFIT': trial.suggest_float('V30_TAKE_PROFIT', 0.10, 0.30, step=0.05),
    }
    
    # 確保 RSI_LOW < RSI_HIGH
    if params['V30_RSI_LOW'] >= params['V30_RSI_HIGH']:
        return -999.0  # 返回極低值表示無效組合
    
    # 載入歷史數據（簡化版，實際應從資料庫讀取）
    print(f"\n🔍 Trial {trial.number}: RSI={params['V30_RSI_LOW']}-{params['V30_RSI_HIGH']}, "
          f"StopLoss={params['V30_STOP_LOSS']:.2f}, TakeProfit={params['V30_TAKE_PROFIT']:.2f}")
    
    # 這裡應該載入真實數據，為了示範，使用空 DataFrame
    # df = get_stock_data(start_date='2024-01-01', end_date='2024-12-31')
    df = pd.DataFrame()  # 示例
    
    # 執行回測
    results = run_backtest_with_params(params, df)
    
    print(f"   📈 ROI: {results['roi']:.2%}, Sharpe: {results['sharpe']:.2f}, MDD: {results['mdd']:.2%}")
    
    return results['roi']


def objective_sharpe(trial: optuna.Trial) -> float:
    """
    最佳化目標：最大化 Sharpe Ratio
    
    Args:
        trial: Optuna trial 物件
    
    Returns:
        float: Sharpe Ratio 值（越大越好）
    """
    params = {
        'V30_RSI_LOW': trial.suggest_int('V30_RSI_LOW', 20, 50),
        'V30_RSI_HIGH': trial.suggest_int('V30_RSI_HIGH', 60, 80),
        'V30_VOLUME_THRESHOLD': trial.suggest_int('V30_VOLUME_THRESHOLD', 2_000_000, 5_000_000, step=500_000),
        'V30_STOP_LOSS': trial.suggest_float('V30_STOP_LOSS', 0.05, 0.15, step=0.01),
        'V30_TAKE_PROFIT': trial.suggest_float('V30_TAKE_PROFIT', 0.10, 0.30, step=0.05),
    }
    
    if params['V30_RSI_LOW'] >= params['V30_RSI_HIGH']:
        return -999.0
    
    df = pd.DataFrame()  # 示例
    results = run_backtest_with_params(params, df)
    
    return results['sharpe']


def main():
    """主程式"""
    parser = argparse.ArgumentParser(description='V33 策略參數最佳化')
    parser.add_argument('--objective', type=str, default='roi', 
                       choices=['roi', 'sharpe'], 
                       help='最佳化目標 (roi: 總報酬率, sharpe: 夏普比率)')
    parser.add_argument('--n-trials', type=int, default=50, 
                       help='最佳化迭代次數 (預設 50)')
    parser.add_argument('--timeout', type=int, default=None, 
                       help='最長執行時間 (秒)')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 V33 Phase 2: 策略參數最佳化")
    print("=" * 60)
    print(f"📌 最佳化目標: {args.objective.upper()}")
    print(f"🔢 迭代次數: {args.n_trials}")
    print(f"⏱️  超時設定: {args.timeout if args.timeout else '無限制'}")
    print("=" * 60)
    
    # 建立 Optuna Study
    study = optuna.create_study(
        study_name=f'v30_optimization_{args.objective}',
        direction='maximize',  # 最大化目標函數
        sampler=TPESampler(seed=42)  # 使用 TPE 採樣器，設定隨機種子
    )
    
    # 選擇目標函數
    objective_func = objective_roi if args.objective == 'roi' else objective_sharpe
    
    # 執行最佳化
    try:
        study.optimize(
            objective_func, 
            n_trials=args.n_trials, 
            timeout=args.timeout,
            show_progress_bar=True
        )
    except KeyboardInterrupt:
        print("\n⚠️ 使用者中斷最佳化")
    
    # 輸出結果
    print("\n" + "=" * 60)
    print("✅ 最佳化完成")
    print("=" * 60)
    
    print(f"\n🏆 最佳參數組合 (目標值: {study.best_value:.4f}):")
    for param, value in study.best_params.items():
        print(f"   {param}: {value}")
    
    print(f"\n📊 總嘗試次數: {len(study.trials)}")
    print(f"📈 最佳 Trial: {study.best_trial.number}")
    
    # 儲存結果到 CSV
    output_file = f"ML_Data/optimization_results_{args.objective}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    trials_df = study.trials_dataframe()
    trials_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n💾 結果已儲存至: {output_file}")
    
    # 視覺化（可選）
    try:
        import optuna.visualization as vis
        import plotly
        
        # 參數重要性圖
        fig = vis.plot_param_importances(study)
        fig.write_html(f"ML_Data/param_importance_{args.objective}.html")
        print(f"📊 參數重要性圖已生成: ML_Data/param_importance_{args.objective}.html")
        
        # 最佳化歷史圖
        fig = vis.plot_optimization_history(study)
        fig.write_html(f"ML_Data/optimization_history_{args.objective}.html")
        print(f"📈 最佳化歷史圖已生成: ML_Data/optimization_history_{args.objective}.html")
    
    except ImportError:
        print("\n⚠️ 未安裝 plotly，跳過視覺化生成")
        print("   安裝方式: pip install plotly")


if __name__ == '__main__':
    raise SystemExit(main())
