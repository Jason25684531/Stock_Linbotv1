"""Read-only inventory; SQL guarded, environment values never serialized."""
import json
import os
from pathlib import Path
import subprocess

from scripts.system_acceptance import ROOT, OUT, digest, write, now
from scripts.system_operations_probe import readonly_sql


def main():
    readonly_sql()
    from sqlalchemy import inspect, text
    from core.db_helper import get_db_engine
    from jobs.scheduler import PIPELINES
    from core.research.reference import batch_job
    from services import web_dashboard
    from app import app, intelligence_routes, platform_v2_routes as routes, web_server
    database = {'status': 'UNAVAILABLE'}
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        inspector = inspect(engine)
        database = {'status': 'PASS_READ_ONLY', 'schema': {table: [c['name'] for c in inspector.get_columns(table)]
                    for table in inspector.get_table_names()}}
    except Exception as error:
        database = {'status': 'UNAVAILABLE', 'error_type': type(error).__name__}  # avoid connection credentials in exception text
    with app.test_request_context('/'):
        strategies = web_dashboard.strategies(routes, intelligence_routes)
    status, code = web_server._build_system_status_payload(include_freshness=True)
    # Only select public fields; never serialize connection strings or raw errors.
    status['warnings'] = ['STATUS_LOAD_WARNING' for _ in status.get('warnings', [])]
    for step in status.get('data', {}).get('steps', []):
        if step.get('error_summary'):
            step['error_summary'] = 'ERROR_RECORDED_IN_EXISTING_PIPELINE_LOG'
    changes = []
    for path in sorted((ROOT / 'openspec/changes').glob('*/tasks.md')):
        source = path.read_text(encoding='utf-8')
        changes.append({'name': path.parent.name, 'complete': source.count('- [x]'), 'remaining': source.count('- [ ]'), 'sha256': digest(path)})
    reports = ['outputs/strategy_intelligence/STRATEGY_INTELLIGENCE_FINAL_ACCEPTANCE_REPORT.md',
               'outputs/platform_v2/PLATFORM_V2_FINAL_ACCEPTANCE_REPORT.md', 'outputs/web_v3/WEB_V3_FINAL_ACCEPTANCE_REPORT.md',
               'outputs/strategy_v4/V4_FINAL_ACCEPTANCE_REPORT.md']
    reports += [str(p.relative_to(ROOT)).replace('\\', '/') for p in (ROOT / 'outputs/strategy_intelligence').glob('*ACCEPTANCE*')]
    layers = {
        'data': ['core/db_helper.py', 'jobs/update_database.py', 'scripts/historical_foundation.py', 'core/research/platform/evidence.py'],
        'strategy': ['core/strategy_manager.py', 'jobs/run_daily.py', 'core/research/reference/catalog.py'],
        'research': ['core/research/platform/engine_v2.py', 'core/research/platform/ledger_v2.py', 'core/research/platform/portfolio_v2.py',
                     'core/research/platform/prediction.py', 'scripts/strategy_intelligence.py', 'services/strategy_v4.py'],
        'web': ['app/platform_v2_routes.py', 'services/web_dashboard.py', 'templates/platform_system.html'],
        'operations': ['jobs/scheduler.py', 'jobs/run_research_platform.py', 'core/research/reference/batch_job.py', 'scripts/task_controller.py',
                       'execution/evening_run.bat', 'execution/morning_run.bat', 'execution/restart_services.bat']}
    environment = {name: bool(os.environ.get(name)) for name in ['STOCK_LINBOT_GRANTS', 'LINE_CHANNEL_ACCESS_TOKEN', 'GEMINI_API_KEY']}
    windows = json.loads((OUT / 'windows_tasks.json').read_text(encoding='utf-8-sig'))
    windows['repository_candidates'] = [row for row in windows.get('repository_candidates', [])
                                       if any(str(ROOT).lower() in str(action.get('Execute', '')).lower() for action in row['actions'])]
    write('windows_repository_tasks.json', windows)
    payload = {'captured_at': now(), 'database': database, 'pipeline_status': status,
               'pipelines': {name: [{'target': s.target, 'args': s.args} for s in steps] for name, steps in PIPELINES.items()},
               'slot': {k: v for k, v in batch_job.state().items() if k in ('status', 'kind', 'pid', 'pids', 'started_at')},
               'resources': web_dashboard.resources(), 'environment_present_process_only': environment,
               'strategies': [{'strategy_id': r['strategy_id'], 'state': r['state'], 'trust': r['trust'], 'has_report': bool(r['report']),
                               'web_runnable': bool(r['readiness'].get('web_runnable'))} for r in strategies],
               'changes': changes, 'reports': {p: digest(ROOT / p) if (ROOT / p).is_file() else None for p in sorted(set(reports))},
               'architecture': {layer: {p: digest(ROOT / p) if (ROOT / p).is_file() else None for p in paths} for layer, paths in layers.items()}}
    write('inventory.json', payload)
    print(json.dumps({'database': database['status'], 'strategies': len(strategies), 'with_reports': sum(r['has_report'] for r in payload['strategies']),
                      'repository_tasks': len(windows['repository_candidates'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
