"""Centralized scheduler for canonical job entrypoints.

This module is the official scheduled entrypoint for the daily recommendation
pipeline:

jobs/update_database.py -> jobs/run_daily.py -> daily_recommendations ->
/api/daily-signals and Line push
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import os
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
    'update_database': JobSpec('jobs/update_database.py', 'Update database'),
    'run_daily': JobSpec('jobs/run_daily.py', 'Generate recommendations'),
    'daily_backtest_validation': JobSpec(
        'jobs/run_daily_backtest_validation.py',
        'Lightweight backtest validation',
    ),
    'train_model': JobSpec('jobs/train_model.py', 'Train model'),
    'run_backtest': JobSpec('jobs/run_backtest.py', 'Run backtest'),
    'push_to_line': JobSpec('jobs/push_to_line.py', 'Push to Line'),
    'optimize_params': JobSpec('jobs/optimize_params.py', 'Optimize params'),
}

DRY_RUN_SUPPORTED_JOBS = {
    'run_daily',
    'run_backtest',
    'push_to_line',
}

PIPELINES: dict[str, tuple[ScheduledStep, ...]] = {
    'daily': (
        ScheduledStep('update_database'),
        ScheduledStep('run_daily'),
        ScheduledStep('daily_backtest_validation'),
        ScheduledStep('push_to_line', ('--time', 'evening')),
    ),
    'evening': (
        ScheduledStep('update_database'),
        ScheduledStep('run_daily'),
        ScheduledStep('daily_backtest_validation'),
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


def _record_validation_not_run(
    pipeline_name: str,
    run_date: str,
    error_summary: str | None = None,
) -> None:
    try:
        from core.db_helper import record_pipeline_step_finish

        record_pipeline_step_finish(
            pipeline_name=pipeline_name,
            step_name='lightweight_backtest_validation',
            run_date=run_date,
            status='not_run',
            error_summary=error_summary,
        )
    except Exception as exc:
        print(f'[{_timestamp()}] validation status record failed: {exc}')


def run_job(
    target: str,
    extra_args: list[str] | None = None,
    *,
    pipeline_name: str | None = None,
    pipeline_run_date: str | None = None,
    dry_run: bool = False,
) -> int:
    job = JOBS[target]
    script_path = REPO_ROOT / job.script_path
    normalized_args = _normalize_extra_args(extra_args or [])
    if dry_run:
        if target not in DRY_RUN_SUPPORTED_JOBS:
            preview = ' '.join([str(script_path.relative_to(REPO_ROOT)), *normalized_args])
            print(f'[{_timestamp()}] [DRY-RUN] would run {preview} (skipped/preview-only)')
            return 0
        if '--dry-run' not in normalized_args and '--no-persist' not in normalized_args:
            normalized_args = [*normalized_args, '--dry-run']

    command = [
        sys.executable,
        '-X',
        'utf8',
        str(script_path),
        *normalized_args,
    ]
    child_env = os.environ.copy()
    if pipeline_name:
        child_env['STOCK_PIPELINE_NAME'] = pipeline_name
    if pipeline_run_date:
        child_env['STOCK_PIPELINE_RUN_DATE'] = pipeline_run_date

    dry_prefix = '[DRY-RUN] ' if dry_run else ''
    print(f'[{_timestamp()}] >>> {dry_prefix}{job.label}: {script_path.relative_to(REPO_ROOT)}')
    result = subprocess.run(command, cwd=REPO_ROOT, check=False, env=child_env)
    if result.returncode == 0:
        print(f'[{_timestamp()}] <<< {job.label} complete')
    else:
        print(f'[{_timestamp()}] <<< {job.label} failed with exit code: {result.returncode}')
    return result.returncode


def _run_pipeline_steps(name: str, *, stop_on_error: bool, dry_run: bool) -> int:
    last_error = 0
    pipeline_run_date = datetime.now().strftime('%Y-%m-%d')
    skipped_steps: list[str] = []
    for step in PIPELINES[name]:
        if dry_run and step.target not in DRY_RUN_SUPPORTED_JOBS:
            skipped_steps.append(step.target)
        run_kwargs = {
            'pipeline_name': name,
            'pipeline_run_date': pipeline_run_date,
        }
        if dry_run:
            run_kwargs['dry_run'] = True
        exit_code = run_job(step.target, list(step.args), **run_kwargs)
        if exit_code != 0:
            last_error = exit_code
            if stop_on_error:
                if name in {'daily', 'evening'} and step.target != 'daily_backtest_validation':
                    _record_validation_not_run(
                        name,
                        pipeline_run_date,
                        error_summary='skipped because an earlier scheduled step failed',
                    )
                return exit_code

    if dry_run:
        skipped_text = ', '.join(skipped_steps) if skipped_steps else 'none'
        print(f'[{_timestamp()}] [DRY-RUN] summary: skipped/preview-only={skipped_text}')
    return last_error


def run_pipeline(name: str, *, stop_on_error: bool, dry_run: bool = False) -> int:
    print('=' * 60)
    print(f'Executing pipeline: {name}')
    if dry_run:
        print('[DRY-RUN] Scheduler preview mode enabled')
    print('=' * 60)

    return _run_pipeline_steps(name, stop_on_error=stop_on_error, dry_run=dry_run)


class SchedulerArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        forwarded = list(getattr(parsed, 'extra_args', []) or [])
        if '--dry-run' in forwarded:
            parsed.dry_run = True
            forwarded.remove('--dry-run')
        if '--stop-on-error' in forwarded and parsed.target in PIPELINES:
            parsed.stop_on_error = True
            forwarded.remove('--stop-on-error')
        parsed.extra_args = forwarded
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = SchedulerArgumentParser(
        description='Run canonical jobs/* entrypoints through the official scheduler.',
    )
    parser.add_argument(
        'target',
        choices=sorted({*JOBS.keys(), *PIPELINES.keys()}),
        help='Job or named pipeline to execute.',
    )
    parser.add_argument(
        '--stop-on-error',
        action='store_true',
        help='Stop the pipeline immediately if any step fails.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview supported jobs with --dry-run and skip unsupported jobs.',
    )
    parser.add_argument(
        'extra_args',
        nargs=argparse.REMAINDER,
        help='Arguments forwarded to the selected job after `--`.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.target in PIPELINES:
        if _normalize_extra_args(args.extra_args):
            parser.error('Named pipelines do not accept forwarded extra args.')
        return run_pipeline(args.target, stop_on_error=args.stop_on_error, dry_run=args.dry_run)

    return run_job(args.target, args.extra_args, dry_run=args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
