import json
from datetime import datetime, timedelta, timezone

import pytest

from core.runtime import operation_policy as op
from core.runtime import research_budget as rb
from core.runtime.operation_policy import Category, ExecutionMode, Operation, authorize
from scripts import task_controller as tc

FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(op, 'LOG', tmp_path / 'authorization/decisions.jsonl')
    monkeypatch.setattr(rb, 'LEDGER', tmp_path / 'budget/usage.jsonl')
    for name in (op.MODE_ENV, op.ENFORCE_ENV, rb.MAX_REQUESTS_ENV, rb.MAX_COST_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tc, 'STATE', tmp_path)  # authorize() has no grants_path/root parameter: tests move the module defaults
    monkeypatch.setattr(tc, 'GRANTS', tmp_path / 'grants.json')


def grants_file(tmp_path, *grants):
    path = tmp_path / 'grants.json'
    path.write_text(json.dumps({'grants': [{'expires_at': FUTURE, 'max_uses': 5, **g} for g in grants]}))
    return path


def production(monkeypatch, tmp_path, *grants, trusted=True):
    monkeypatch.setenv(op.MODE_ENV, 'production')
    monkeypatch.setattr(tc, 'grant_trusted', lambda path: trusted)
    tc.create('T1', 'p', ['a'], 'quant-researcher', 'G', root=tmp_path)
    return grants_file(tmp_path, {'operation': 'production_mode'}, *grants)


def log_lines():
    return [json.loads(line) for line in op.LOG.read_text(encoding='utf-8').splitlines()]


# ---- registry
def test_every_operation_has_a_policy_and_production_ones_need_grants():
    assert set(op.POLICIES) == set(Operation)
    for operation, policy in op.POLICIES.items():
        if policy.category in (Category.PRODUCTION, Category.FORBIDDEN):
            assert policy.grant_required and policy.production_sensitive, operation
        if policy.category is Category.RESEARCH:
            assert not policy.grant_required and not policy.external_side_effect, operation


# ---- Research
@pytest.mark.parametrize('operation', [Operation.RUN_BACKTEST, Operation.RUN_PARAMETER_SWEEP, Operation.RUN_LOCAL_ANALYSIS,
                                       Operation.GENERATE_REPORT, Operation.RUN_REGISTERED_EXPERIMENT])
def test_research_operations_are_allowed_without_any_grant(operation, tmp_path):
    decision = authorize(operation, strategy='new_strategy_01')
    assert decision.allowed and decision.mode is ExecutionMode.RESEARCH
    assert not (tmp_path / tc.USAGE).exists()


def test_ten_thousand_local_backtests_are_not_limited_by_a_grant(tmp_path):
    grants = grants_file(tmp_path, {'operation': 'registered_experiment', 'max_uses': 1, 'strategies': ['A191_132']})
    assert all(authorize(Operation.RUN_BACKTEST, strategy='experimental').allowed for _ in range(10000))
    assert not (tmp_path / tc.USAGE).exists()


def test_research_strategy_scope_is_not_production_scope(monkeypatch, tmp_path):
    assert authorize(Operation.RUN_BACKTEST, strategy='A191_133').allowed
    grants = production(monkeypatch, tmp_path, {'operation': 'publish_external', 'strategies': ['A191_132']})
    assert authorize(Operation.PUBLISH_EXTERNAL, strategy='A191_132', task_id='T1').allowed
    assert not authorize(Operation.PUBLISH_EXTERNAL, strategy='A191_133', task_id='T1').allowed


# ---- boundary
@pytest.mark.parametrize('operation', [Operation.WRITE_PRODUCTION_DB, Operation.PUBLISH_EXTERNAL, Operation.CREATE_SCHEDULE])
def test_research_mode_denies_production_operations_without_consulting_grants(operation, tmp_path):
    grants = grants_file(tmp_path, {'operation': operation.value}, {'operation': 'production_mode'})
    decision = authorize(operation)
    assert not decision.allowed and decision.mode is ExecutionMode.RESEARCH and not (tmp_path / tc.USAGE).exists()


@pytest.mark.parametrize('operation', [Operation.SUBMIT_LIVE_ORDER, Operation.MODIFY_LIVE_ORDER, Operation.CANCEL_LIVE_ORDER,
                                       Operation.MODIFY_AUTHORIZATION, Operation.CHANGE_PRODUCTION_QUOTA, Operation.CHANGE_PRODUCTION_SCOPE])
def test_forbidden_operations_are_denied_in_both_modes_even_with_a_matching_grant(operation, monkeypatch, tmp_path):
    assert not authorize(operation).allowed
    grants = production(monkeypatch, tmp_path, {'operation': operation.value})
    decision = authorize(operation, task_id='T1')
    assert decision.mode is ExecutionMode.PRODUCTION and not decision.allowed and 'forbidden' in decision.reason


@pytest.mark.parametrize('unknown', ['experimental_new_side_effect', None, 'run_backtest', 42])
def test_unknown_operations_are_denied(unknown):
    decision = authorize(unknown)
    assert not decision.allowed and 'unknown operation' in decision.reason


def test_environment_variable_alone_never_upgrades_to_production(monkeypatch, tmp_path):
    monkeypatch.setenv(op.MODE_ENV, 'production')
    grants = grants_file(tmp_path, {'operation': 'production_mode'}, {'operation': 'publish_external'})
    monkeypatch.setattr(tc, 'grant_trusted', lambda path: False)  # the grant file is inside the repo or writable
    assert op.current_mode(grants) is ExecutionMode.RESEARCH
    assert not authorize(Operation.PUBLISH_EXTERNAL).allowed
    monkeypatch.setattr(tc, 'grant_trusted', lambda path: True)
    assert op.current_mode(tmp_path / 'absent.json') is ExecutionMode.RESEARCH  # trusted but no production_mode grant
    assert op.current_mode(grants) is ExecutionMode.PRODUCTION


def test_a_broken_grant_check_resolves_to_research(monkeypatch):
    monkeypatch.setenv(op.MODE_ENV, 'production')
    monkeypatch.setattr(tc, 'grant_trusted', lambda path: 1 / 0)
    assert op.current_mode() is ExecutionMode.RESEARCH


# ---- Production
def test_production_allows_with_a_valid_grant_and_records_the_grant_id(monkeypatch, tmp_path):
    grants = production(monkeypatch, tmp_path, {'operation': 'publish_external', 'strategies': ['A191_132']})
    decision = authorize(Operation.PUBLISH_EXTERNAL, strategy='A191_132', task_id='T1')
    assert decision.allowed and decision.grant_id and tc.grant_uses(tc.grant_for('publish_external', grants), tmp_path) == 1
    last = log_lines()[-1]
    assert (last['mode'], last['operation'], last['grant_required'], last['grant_id'], last['result']) == ('PRODUCTION', 'publish_external', True, decision.grant_id, 'ALLOW')


def test_production_denies_without_grant_outside_scope_and_over_quota(monkeypatch, tmp_path):
    grants = production(monkeypatch, tmp_path, {'operation': 'publish_external', 'strategies': ['A191_132'], 'max_uses': 2})
    kw = {'task_id': 'T1'}
    assert not authorize(Operation.CREATE_SCHEDULE, **kw).allowed  # no grant for this operation
    assert not authorize(Operation.PUBLISH_EXTERNAL, strategy='D5', **kw).allowed  # strategy outside the grant
    assert authorize(Operation.PUBLISH_EXTERNAL, strategy='A191_132', uses=2, **kw).allowed
    refused = authorize(Operation.PUBLISH_EXTERNAL, strategy='A191_132', **kw)
    assert not refused.allowed and 'exhausted' in refused.reason
    assert tc.grant_uses(tc.grant_for('publish_external', grants), tmp_path) == 2  # a refusal consumes nothing


def test_production_denies_when_the_decision_log_cannot_be_written(monkeypatch, tmp_path):
    grants = production(monkeypatch, tmp_path, {'operation': 'publish_external'})
    blocker = tmp_path / 'blocker'
    blocker.write_text('a file where the log directory should be')
    monkeypatch.setattr(op, 'LOG', blocker / 'decisions.jsonl')
    with pytest.raises(OSError):
        authorize(Operation.PUBLISH_EXTERNAL, task_id='T1')
    assert not (tmp_path / tc.USAGE).exists()  # nothing was consumed: no record, no action


def test_production_mode_still_runs_research_operations_without_a_grant(monkeypatch, tmp_path):
    grants = production(monkeypatch, tmp_path)
    decision = authorize(Operation.RUN_BACKTEST)
    assert decision.allowed and decision.mode is ExecutionMode.PRODUCTION and not (tmp_path / tc.USAGE).exists()


# ---- audit-only versus enforce
def test_observe_never_denies_never_consults_grants_and_is_not_an_allow(tmp_path):
    assert op.observe(Operation.WRITE_PRODUCTION_DB, dataset='daily_market_data') is None
    (record,) = log_lines()
    assert record['result'] == 'OBSERVED' and record['enforced'] is False and record['mode'] == 'RESEARCH' and record['caller']
    assert not (tmp_path / tc.USAGE).exists()


def test_db_write_gate_is_audit_only_by_default_and_enforced_on_request(monkeypatch, tmp_path):
    op.db_write_gate(dataset='t')  # phase 1: proceeds
    assert [r['result'] for r in log_lines()] == ['OBSERVED']
    monkeypatch.setenv(op.ENFORCE_ENV, 'write_production_db')
    with pytest.raises(op.AuthorizationDenied, match='Production operation'):  # Research mode, enforcement on
        op.db_write_gate(dataset='t')
    assert log_lines()[-1]['result'] == 'DENY' and log_lines()[-1]['enforced'] is True
    grants = production(monkeypatch, tmp_path, {'operation': 'write_production_db'})
    monkeypatch.setattr(tc, 'GRANTS', grants)
    op.db_write_gate(dataset='t')  # Production mode with a covering grant: the enforced gate lets the write through
    assert log_lines()[-1]['result'] == 'ALLOW' and log_lines()[-1]['grant_id']


def test_observe_does_not_stop_a_service_when_the_log_is_unwritable(monkeypatch, tmp_path):
    blocker = tmp_path / 'blocker'
    blocker.write_text('x')
    monkeypatch.setattr(op, 'LOG', blocker / 'decisions.jsonl')
    op.observe(Operation.WRITE_PRODUCTION_DB)  # no exception


# ---- Research budget and official endpoints
def test_external_research_needs_context_even_with_an_environment_budget(monkeypatch):
    assert 'PUBLIC_REQUEST_CONTEXT_REQUIRED' in authorize(Operation.CALL_RESEARCH_API).reason
    monkeypatch.setenv(rb.MAX_REQUESTS_ENV, '2')
    assert not authorize(Operation.CALL_RESEARCH_API).allowed and not authorize(Operation.DOWNLOAD_MARKET_DATA).allowed
    assert rb.used()['requests'] == 0


def test_public_context_needs_no_grant_and_precheck_does_not_debit(monkeypatch, tmp_path, public_budget):
    from core.runtime.public_data import context
    monkeypatch.setenv(rb.MAX_REQUESTS_ENV, '100')
    ctx = context('https://www.twse.com.tw/rwd/zh/reducation/TWTAUU', params={'startDate': '20220101', 'endDate': '20261023', 'response': 'json'})
    monkeypatch.setattr(tc, 'grant_trusted', lambda path: False)
    assert authorize(Operation.DOWNLOAD_MARKET_DATA, official=True, request_context=ctx).allowed
    assert not authorize(Operation.DOWNLOAD_MARKET_DATA, official=True).allowed
    assert rb.status()['used'] == 7 and not (tmp_path / tc.USAGE).exists()


def test_gate_enforces_only_the_listed_operations(monkeypatch):
    op.gate(Operation.PUBLISH_EXTERNAL)  # audit-only by default
    monkeypatch.setenv(op.ENFORCE_ENV, 'write_production_db')
    op.gate(Operation.PUBLISH_EXTERNAL)  # not listed: still audit-only
    with pytest.raises(op.AuthorizationDenied):
        op.gate(Operation.WRITE_PRODUCTION_DB)
    monkeypatch.setenv(op.ENFORCE_ENV, 'all')
    with pytest.raises(op.AuthorizationDenied):
        op.gate(Operation.PUBLISH_EXTERNAL)
    assert [(r['result'], r['enforced']) for r in log_lines()] == [('OBSERVED', False)] * 2 + [('DENY', True)] * 2


def test_banner_shows_mode_and_enforcement_phase(monkeypatch):
    assert op.banner('x') == '[x] MODE: RESEARCH | GATES: AUDIT_ONLY (not enforced) | PUBLIC_RESEARCH_HTTP: ENFORCED'
    monkeypatch.setenv(op.ENFORCE_ENV, 'write_production_db')
    assert 'ENFORCING write_production_db' in op.banner()


# ---- review round 1
def test_authorize_cannot_be_pointed_at_another_grant_file_or_usage_log():
    """A caller-supplied root would start a fresh grant_usage.jsonl and reset max_uses: the parameters do not exist."""
    for name in ('root', 'grants_path', 'trusted', 'production'):
        with pytest.raises(TypeError):
            authorize(Operation.PUBLISH_EXTERNAL, **{name: 'x'})


def test_a_forbidden_or_unregistered_operation_is_never_audit_only():
    with pytest.raises(op.AuthorizationDenied, match='forbidden'):
        op.gate(Operation.SUBMIT_LIVE_ORDER)
    with pytest.raises(op.AuthorizationDenied, match='unknown'):
        op.gate('submit_live_order')


def test_the_logged_caller_is_the_function_that_asked_not_the_wrapper():
    op.db_write_gate(dataset='t')
    assert log_lines()[-1]['caller'].endswith(':test_the_logged_caller_is_the_function_that_asked_not_the_wrapper')


def test_enforced_db_write_is_allowed_in_production_with_a_covering_grant(monkeypatch, tmp_path):
    production(monkeypatch, tmp_path, {'operation': 'write_production_db'})
    monkeypatch.setenv(op.ENFORCE_ENV, 'write_production_db')
    assert op.authorize(Operation.WRITE_PRODUCTION_DB, task_id='T1').allowed
    assert [r['result'] for r in log_lines()] == ['ALLOW']


def test_a_production_official_check_that_raises_is_a_refusal_not_an_error(monkeypatch, public_budget):
    from core.runtime.public_data import context
    monkeypatch.setenv(rb.MAX_REQUESTS_ENV, '5')
    monkeypatch.setattr(op, 'current_mode', lambda: ExecutionMode.PRODUCTION)
    monkeypatch.setattr(tc, 'grant_trusted', lambda path: 1 / 0)
    ctx = context('https://www.twse.com.tw/rwd/zh/reducation/TWTAUU', params={'startDate': '20220101', 'endDate': '20261023', 'response': 'json'})
    decision = authorize(Operation.DOWNLOAD_MARKET_DATA, official=True, request_context=ctx)
    assert not decision.allowed and 'check failed' in decision.reason and rb.used()['requests'] == 0


def test_real_fetch_boundaries_deny_unknown_endpoints_even_under_audit_only(monkeypatch):
    from core.research.sources import twse
    calls = []

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {'ok': True}

    monkeypatch.setattr(twse.requests, 'get', lambda *a, **k: calls.append(a) or Reply())
    with pytest.raises(op.AuthorizationDenied):
        twse._fetch_openapi('x/y')
    monkeypatch.setenv(op.ENFORCE_ENV, 'download_market_data')
    with pytest.raises(op.AuthorizationDenied):  # enforced, Research mode, no trusted official_fetch grant
        twse._fetch_openapi('x/y')
    assert calls == []  # denied before either request regardless of the audit-only setting


def test_every_database_write_function_calls_the_gate_first():
    import re
    from pathlib import Path
    source = (Path(op.__file__).parents[2] / 'core/db_helper.py').read_text(encoding='utf-8')  # the file, not the module: conftest replaces save_backtest_results
    for name in ('record_pipeline_step_start', 'record_pipeline_step_finish', 'save_dashboard_aggregation_cache', 'update_setting', 'upsert_stock_data',
                 'create_user_simulation_trade', 'save_backtest_results', 'upsert_financial_statements', 'save_news_sentiment'):
        body = re.search(rf'^def {name}\(.*?(?=^def |\Z)', source, re.S | re.M).group(0)
        assert 'db_write_gate(' in body, name
