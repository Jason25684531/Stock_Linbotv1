"""Centralized scheduler for canonical job entrypoints."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class JobSpec:
    script_path: str
    label: str


@dataclass(frozen=True)
class ScheduledStep:
    target: str
    args: tuple[str, ...] = ()


JOBS: dict[str, JobSpec] = {
    'update_database': JobSpec('jobs/update_database.py', '更新資料庫'),
    'run_daily': JobSpec('jobs/run_daily.py', '每日選股'),
    'train_model': JobSpec('jobs/train_model.py', '模型訓練'),
    'run_backtest': JobSpec('jobs/run_backtest.py', '回測引擎'),
    'push_to_line': JobSpec('jobs/push_to_line.py', 'Line 推播'),
    'optimize_params': JobSpec('jobs/optimize_params.py', '參數最佳化'),
}

PIPELINES: dict[str, tuple[ScheduledStep, ...]] = {
    'daily': (
        ScheduledStep('update_database'),
        ScheduledStep('run_daily'),
        ScheduledStep('push_to_line', ('--time', 'evening')),
    ),
    'evening': (
        ScheduledStep('update_database'),
        ScheduledStep('run_daily'),
        ScheduledStep('push_to_line', ('--time', 'evening')),
    ),
    'morning': (
        ScheduledStep('push_to_line', ('--time', 'morning')),
    ),
    'push_evening': (
        ScheduledStep('push_to_line', ('--time', 'evening')),
    ),
}


def _timestamp() -> str:
    return datetime.now().strftime('%H:%M:%S')


def _normalize_extra_args(extra_args: list[str]) -> list[str]:
    if extra_args and extra_args[0] == '--':
        return extra_args[1:]
    return extra_args


def run_job(target: str, extra_args: list[str] | None = None) -> int:
    job = JOBS[target]
    script_path = REPO_ROOT / job.script_path
    command = [
        sys.executable,
        '-X',
        'utf8',
        str(script_path),
        *_normalize_extra_args(extra_args or []),
    ]

    print(f'[{_timestamp()}] >>> {job.label}: {script_path.relative_to(REPO_ROOT)}')
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if result.returncode == 0:
        print(f'[{_timestamp()}] <<< {job.label} 完成')
    else:
        print(f'[{_timestamp()}] <<< {job.label} 失敗，錯誤碼: {result.returncode}')
    return result.returncode


def run_pipeline(name: str, *, stop_on_error: bool) -> int:
    print('=' * 60)
    print(f'🚀 執行排程流程: {name}')
    print('=' * 60)

    last_error = 0
    for step in PIPELINES[name]:
        exit_code = run_job(step.target, list(step.args))
        if exit_code != 0:
            last_error = exit_code
            if stop_on_error:
                return exit_code

    return last_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='統一管理 canonical jobs/* 的排程與單點執行。',
    )
    parser.add_argument(
        'target',
        choices=sorted({*JOBS.keys(), *PIPELINES.keys()}),
        help='要執行的 job 名稱或排程流程名稱。',
    )
    parser.add_argument(
        '--stop-on-error',
        action='store_true',
        help='排程流程遇到第一個失敗就立刻停止。',
    )
    parser.add_argument(
        'extra_args',
        nargs=argparse.REMAINDER,
        help='傳給單一 job 的額外參數；若有 option 請先加 --。',
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.target in PIPELINES:
        if _normalize_extra_args(args.extra_args):
            parser.error('排程流程不接受額外參數。')
        return run_pipeline(args.target, stop_on_error=args.stop_on_error)

    return run_job(args.target, args.extra_args)


if __name__ == '__main__':
    raise SystemExit(main())