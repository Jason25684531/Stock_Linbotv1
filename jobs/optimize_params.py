"""
V33 Phase 2: 策略參數最佳化腳本

使用 Optuna 框架進行超參數最佳化，目標是找出最佳的策略參數組合。

最佳化目標：
- ROI (Return on Investment) - 總報酬率
- Sharpe Ratio - 風險調整後報酬
- MDD (Max Drawdown) - 最大回撤控制

使用方式：
    python jobs/optimize_params.py --objective roi --n-trials 100
    python jobs/optimize_params.py --objective roi --lookback-days 30
    python jobs/optimize_params.py --objective sharpe --date 2026-03-26 --end-date 2026-04-15
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
import sys

import numpy as np
import optuna
import pandas as pd
from optuna.samplers import TPESampler
from optuna.trial import TrialState
from sqlalchemy import text
from typing import Dict, Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import Config
from core.db_helper import get_db_engine, get_stock_data
from core.strategies.v31_hybrid import V31HybridStrategy
from jobs.run_backtest import BacktestEngine


REQUIRED_OPTIMIZATION_COLUMNS = frozenset({
    'close_price',
    'ma20',
    'ma60',
    'volume',
    'rsi',
})

DEFAULT_LOOKBACK_DAYS = 30


def load_optimization_data(date_str: str | None = None) -> tuple[pd.DataFrame, str | None]:
    """載入最佳化所需的最新市場快照。"""
    df, resolved_date = get_stock_data(date_str=date_str)

    if df.empty:
        raise RuntimeError(
            '查無可用的市場資料，請先執行 jobs/update_database.py 與 jobs/run_daily.py。'
        )

    missing_columns = sorted(REQUIRED_OPTIMIZATION_COLUMNS.difference(df.columns))
    if missing_columns:
        raise RuntimeError(
            '最佳化資料缺少必要欄位: '
            f"{', '.join(missing_columns)}。請先執行 jobs/run_daily.py 更新技術指標。"
        )

    return df.copy(), resolved_date


def get_recent_trade_dates(anchor_date: str | None = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[str]:
    """取得指定錨點往前最近 N 個交易日。"""
    lookback_days = int(lookback_days)
    if lookback_days <= 0:
        raise ValueError('lookback_days 必須大於 0')

    engine = get_db_engine()
    params = {}
    query_str = """
        SELECT DISTINCT trade_date
        FROM daily_market_data
    """

    if anchor_date:
        query_str += " WHERE trade_date <= :anchor_date"
        params['anchor_date'] = anchor_date

    query_str += f" ORDER BY trade_date DESC LIMIT {lookback_days}"

    with engine.connect() as conn:
        rows = conn.execute(text(query_str), params).fetchall()

    return [row[0].strftime('%Y-%m-%d') for row in rows]


def can_generate_param_importances(study: optuna.Study) -> tuple[bool, str | None]:
    """判斷目前 study 是否適合計算參數重要性。"""
    completed_values = [
        trial.value
        for trial in study.trials
        if trial.state == TrialState.COMPLETE and trial.value is not None
    ]

    if len(completed_values) < 2:
        return False, '完成的 trial 少於 2 筆，無法計算參數重要性'

    if np.isclose(np.var(completed_values), 0.0):
        return False, '所有完成 trial 的目標值相同，無法計算參數重要性'

    return True, None


def resolve_optimization_date(preferred_date: str | None = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """自動回溯最近 N 個交易日，找到有候選股的起始日期。"""
    trade_dates = get_recent_trade_dates(preferred_date, lookback_days)
    if not trade_dates:
        raise RuntimeError('找不到可用的交易日，請先執行 jobs/update_database.py。')

    checked_dates = []

    for trade_date in trade_dates:
        with redirect_stdout(StringIO()):
            market_df, resolved_date = load_optimization_data(trade_date)
            candidate_count = get_candidate_universe_size(market_df)

        checked_dates.append({'date': resolved_date, 'candidate_count': candidate_count})
        if candidate_count > 0:
            return {
                'date': resolved_date,
                'snapshot_df': market_df,
                'candidate_count': candidate_count,
                'checked_dates': checked_dates,
                'backtracked_days': len(checked_dates) - 1,
            }

    checked_summary = ', '.join(f"{item['date']}({item['candidate_count']})" for item in checked_dates)
    raise RuntimeError(
        f"近 {lookback_days} 個交易日都找不到可用候選股。已檢查: {checked_summary}。"
    )


def build_objective(metric: str, start_date: str, end_date: str | None = None) -> Callable[[optuna.Trial], float]:
    """建立使用真實回測引擎的目標函數。"""

    def objective(trial: optuna.Trial) -> float:
        params = {
            'V30_RSI_LOW': trial.suggest_int('V30_RSI_LOW', 20, 50),
            'V30_RSI_HIGH': trial.suggest_int('V30_RSI_HIGH', 60, 80),
            'V30_VOLUME_THRESHOLD': trial.suggest_int('V30_VOLUME_THRESHOLD', 2_000_000, 5_000_000, step=500_000),
            'V30_STOP_LOSS': trial.suggest_float('V30_STOP_LOSS', 0.05, 0.15, step=0.01),
            'V30_TAKE_PROFIT': trial.suggest_float('V30_TAKE_PROFIT', 0.10, 0.30, step=0.05),
        }

        if params['V30_RSI_LOW'] >= params['V30_RSI_HIGH']:
            return -999.0

        print(
            f"\n🔍 Trial {trial.number}: RSI={params['V30_RSI_LOW']}-{params['V30_RSI_HIGH']}, "
            f"StopLoss={params['V30_STOP_LOSS']:.2f}, TakeProfit={params['V30_TAKE_PROFIT']:.2f}"
        )

        results = run_backtest_with_params(params, start_date, end_date)
        print(
            f"   📈 ROI: {results['roi']:.2%}, Sharpe: {results['sharpe']:.2f}, "
            f"MDD: {results['mdd']:.2%}, Trades: {results['trade_count']}"
        )

        return results[metric]

    return objective


def get_optimization_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """最佳化時固定使用 V31/V30 技術面篩選，不受 active strategy 影響。"""
    return V31HybridStrategy().filter_candidates(df.copy())


def get_candidate_universe_size(df: pd.DataFrame) -> int:
    """用最寬鬆的搜尋條件預檢指定日期是否存在可最佳化的候選股。"""
    preview_params = {
        'V30_RSI_LOW': 20,
        'V30_RSI_HIGH': 80,
        'V30_VOLUME_THRESHOLD': 2_000_000,
    }
    original_values = {}

    try:
        for key, value in preview_params.items():
            original_values[key] = getattr(Config, key)
            setattr(Config, key, value)

        return len(get_optimization_candidates(df))
    finally:
        for key, value in original_values.items():
            setattr(Config, key, value)


def run_backtest_with_params(params: Dict[str, Any], start_date: str, end_date: str | None = None) -> Dict[str, float]:
    """
    使用給定參數執行真實回測
    
    Args:
        params: 策略參數字典
        start_date: 回測起始日
        end_date: 回測結束日
    
    Returns:
        Dict: 包含 roi, sharpe, mdd 的績效指標
    """
    original_values = {}
    captured_output = StringIO()
    try:
        for key, value in params.items():
            if hasattr(Config, key):
                original_values[key] = getattr(Config, key)
                setattr(Config, key, value)

        with redirect_stdout(captured_output):
            engine = BacktestEngine(
                mode='v30',
                start_date=start_date,
                end_date=end_date,
                persist_results=False,
                use_db_params=False,
            )
            metrics = engine.run(return_metrics=True)

        return {
            'roi': metrics['roi'] / 100.0,
            'sharpe': metrics['sharpe_ratio'],
            'mdd': -(metrics['max_drawdown'] / 100.0),
            'trade_count': metrics['trade_count'],
            'start_date': metrics['start_date'],
            'end_date': metrics['end_date'],
        }
    
    except Exception as e:
        tail = captured_output.getvalue().splitlines()[-5:]
        if tail:
            print("⚠️ 回測最後輸出:")
            for line in tail:
                print(f"   {line}")
        print(f"❌ 回測執行失敗: {e}")
        return {
            'roi': -999.0,
            'sharpe': -999.0,
            'mdd': -999.0,
            'trade_count': 0,
            'start_date': start_date,
            'end_date': end_date or start_date,
        }
    finally:
        for key, value in original_values.items():
            setattr(Config, key, value)


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
    parser.add_argument('--date', type=str, default=None,
                       help='指定搜尋起點日期 (YYYY-MM-DD)，若當天無候選股會自動往前回溯')
    parser.add_argument('--end-date', type=str, default=None,
                       help='指定回測結束日 (YYYY-MM-DD)，預設為最新交易日')
    parser.add_argument('--lookback-days', type=int, default=DEFAULT_LOOKBACK_DAYS,
                       help='自動回溯最近 N 個交易日，直到找到有候選股的日期')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 V33 Phase 2: 策略參數最佳化")
    print("=" * 60)
    print(f"📌 最佳化目標: {args.objective.upper()}")
    print(f"🔢 迭代次數: {args.n_trials}")
    print(f"⏱️  超時設定: {args.timeout if args.timeout else '無限制'}")

    try:
        resolved_target = resolve_optimization_date(args.date, args.lookback_days)
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1

    resolved_date = resolved_target['date']
    market_df = resolved_target['snapshot_df']
    candidate_universe_size = resolved_target['candidate_count']
    backtracked_days = resolved_target['backtracked_days']

    if args.end_date and args.end_date < resolved_date:
        print(f"❌ end-date ({args.end_date}) 不可早於回測起始日 ({resolved_date})")
        return 1

    if args.date:
        print(f"📍 搜尋起點: {args.date}")
    else:
        print("📍 搜尋起點: 最新交易日")

    if backtracked_days > 0:
        print(f"↩️ 自動回溯 {backtracked_days} 個交易日後，選用起始日: {resolved_date}")
    else:
        print(f"📅 使用起始日: {resolved_date}")

    print(f"🏁 回測結束日: {args.end_date or '最新交易日'}")
    print(f"🧾 起始日市場筆數: {len(market_df)}")
    print(f"🎯 起始日候選股數: {candidate_universe_size}")
    print("=" * 60)
    
    # 建立 Optuna Study
    study = optuna.create_study(
        study_name=f'v30_optimization_{args.objective}',
        direction='maximize',  # 最大化目標函數
        sampler=TPESampler(seed=42)  # 使用 TPE 採樣器，設定隨機種子
    )
    
    # 選擇目標函數
    objective_func = build_objective(args.objective, resolved_date, args.end_date)
    
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
    print(f"🗓️ 回測區間: {resolved_date} ~ {args.end_date or '最新交易日'}")
    
    # 儲存結果到 CSV
    output_file = f"ML_Data/optimization_results_{args.objective}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    trials_df = study.trials_dataframe()
    trials_df['backtest_start_date'] = resolved_date
    trials_df['backtest_end_date'] = args.end_date or ''
    trials_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n💾 結果已儲存至: {output_file}")
    
    # 視覺化（可選）
    try:
        import optuna.visualization as vis

        can_plot_importance, skip_reason = can_generate_param_importances(study)
        if can_plot_importance:
            try:
                fig = vis.plot_param_importances(study)
                fig.write_html(f"ML_Data/param_importance_{args.objective}.html")
                print(f"📊 參數重要性圖已生成: ML_Data/param_importance_{args.objective}.html")
            except RuntimeError as exc:
                print(f"⚠️ 略過參數重要性圖: {exc}")
        else:
            print(f"⚠️ 略過參數重要性圖: {skip_reason}")
        
        # 最佳化歷史圖
        fig = vis.plot_optimization_history(study)
        fig.write_html(f"ML_Data/optimization_history_{args.objective}.html")
        print(f"📈 最佳化歷史圖已生成: ML_Data/optimization_history_{args.objective}.html")
    
    except ImportError:
        print("\n⚠️ 未安裝 plotly，跳過視覺化生成")
        print("   安裝方式: pip install plotly")


if __name__ == '__main__':
    raise SystemExit(main())
