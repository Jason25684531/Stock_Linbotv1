"""Research / Production boundary: one registry of operations, one decision function, enforced inside the functions that cause side effects.

RESEARCH is the default. PRODUCTION needs all three of: STOCK_LINBOT_MODE=production, a trusted grant file (grant_trusted: outside the repo,
Owner-owned, not writable by this account) and an unexpired production_mode grant in it. The environment variable alone never upgrades.
Production operations delegate to scripts.task_controller.require_grant, which is not changed. Forbidden operations (live orders,
authorization / quota / scope changes) are denied in every mode. Anything not in the registry is denied.
ponytail: text of the mode lives in the environment and OS ACLs, not here; the real boundary against a hostile same-account process is the OS.
"""
import json
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / 'outputs/authorization/decisions.jsonl'
MODE_ENV = 'STOCK_LINBOT_MODE'
ENFORCE_ENV = 'STOCK_LINBOT_ENFORCE'  # comma list of operation values (or 'all') whose gate() enforces; unset = audit-only phase
_RESEARCH_REQUEST = ContextVar('stock_linbot_research_request', default=False)


@contextmanager
def research_request_scope():
    """One-way restriction for a validated Research-origin RPC. Never upgrades a process or grants Production authority."""
    if current_mode() is not ExecutionMode.RESEARCH:
        raise AuthorizationDenied('RESEARCH_SCOPE_REQUIRES_RESEARCH_ADMISSION')
    token = _RESEARCH_REQUEST.set(True)
    try:
        yield
    finally:
        _RESEARCH_REQUEST.reset(token)


class ExecutionMode(str, Enum):
    RESEARCH = 'RESEARCH'
    PRODUCTION = 'PRODUCTION'


class Category(str, Enum):
    RESEARCH = 'research'
    LOCAL_RESEARCH = 'research'  # compatibility alias
    PUBLIC_RESEARCH_FETCH = 'public_research_fetch'
    RESTRICTED_EXTERNAL = 'restricted_external'
    EXTERNAL_RESEARCH = 'restricted_external'  # legacy external operations require context or separate authority
    PRODUCTION = 'production'
    FORBIDDEN = 'forbidden'


@dataclass(frozen=True)
class OperationPolicy:
    category: Category
    external_side_effect: bool
    production_sensitive: bool
    grant_required: bool


class Operation(str, Enum):
    RUN_BACKTEST = 'run_backtest'
    RUN_PARAMETER_SWEEP = 'run_parameter_sweep'
    RUN_LOCAL_ANALYSIS = 'run_local_analysis'
    GENERATE_REPORT = 'generate_report'
    RUN_REGISTERED_EXPERIMENT = 'run_registered_experiment'
    DOWNLOAD_MARKET_DATA = 'download_market_data'
    CALL_RESEARCH_API = 'call_research_api'
    FETCH_PUBLIC_DATA = 'public_research_fetch'
    WRITE_PRODUCTION_DB = 'write_production_db'
    PUBLISH_EXTERNAL = 'publish_external'
    CREATE_SCHEDULE = 'create_schedule'
    SUBMIT_LIVE_ORDER = 'submit_live_order'
    MODIFY_LIVE_ORDER = 'modify_live_order'
    CANCEL_LIVE_ORDER = 'cancel_live_order'
    MODIFY_AUTHORIZATION = 'modify_authorization'
    CHANGE_PRODUCTION_QUOTA = 'change_production_quota'
    CHANGE_PRODUCTION_SCOPE = 'change_production_scope'


_LOCAL = OperationPolicy(Category.RESEARCH, False, False, False)
_EXTERNAL = OperationPolicy(Category.RESTRICTED_EXTERNAL, True, False, True)
_PUBLIC = OperationPolicy(Category.PUBLIC_RESEARCH_FETCH, True, False, False)
_PRODUCTION = OperationPolicy(Category.PRODUCTION, True, True, True)
_FORBIDDEN = OperationPolicy(Category.FORBIDDEN, True, True, True)
POLICIES = {
    Operation.RUN_BACKTEST: _LOCAL, Operation.RUN_PARAMETER_SWEEP: _LOCAL, Operation.RUN_LOCAL_ANALYSIS: _LOCAL,
    Operation.GENERATE_REPORT: _LOCAL, Operation.RUN_REGISTERED_EXPERIMENT: _LOCAL,
    Operation.DOWNLOAD_MARKET_DATA: _EXTERNAL, Operation.CALL_RESEARCH_API: _EXTERNAL,
    Operation.FETCH_PUBLIC_DATA: _PUBLIC,
    Operation.WRITE_PRODUCTION_DB: _PRODUCTION, Operation.PUBLISH_EXTERNAL: _PRODUCTION, Operation.CREATE_SCHEDULE: _PRODUCTION,
    Operation.SUBMIT_LIVE_ORDER: _FORBIDDEN, Operation.MODIFY_LIVE_ORDER: _FORBIDDEN, Operation.CANCEL_LIVE_ORDER: _FORBIDDEN,
    Operation.MODIFY_AUTHORIZATION: _FORBIDDEN, Operation.CHANGE_PRODUCTION_QUOTA: _FORBIDDEN, Operation.CHANGE_PRODUCTION_SCOPE: _FORBIDDEN,
}
assert set(POLICIES) == set(Operation)  # a new operation without a policy fails at import


class AuthorizationDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    result: str
    mode: ExecutionMode
    operation: str
    reason: str = ''
    grant_id: str | None = None


def current_mode(grants_path=None) -> ExecutionMode:
    """RESEARCH unless the environment asks for production AND a trusted, unexpired production_mode grant exists. Any error -> RESEARCH."""
    if _RESEARCH_REQUEST.get() or os.environ.get(MODE_ENV, '').strip().lower() != 'production':
        return ExecutionMode.RESEARCH
    try:
        from scripts import task_controller as tc
        path = grants_path or tc.GRANTS
        if tc.grant_trusted(path) and tc.grant_for('production_mode', path) is not None:
            return ExecutionMode.PRODUCTION
    except Exception:  # fail closed: an unreadable grant file never makes a Production process
        pass
    return ExecutionMode.RESEARCH


def enforced_operations() -> frozenset:
    """Operations whose gate() enforces. The variable can only make checks stricter, so an agent setting it gains nothing."""
    raw = {x.strip().lower() for x in os.environ.get(ENFORCE_ENV, '').split(',') if x.strip()}
    return frozenset(o.value for o in Operation) if 'all' in raw else frozenset(raw)


def _caller() -> str:
    """The first frame outside this module: the function that asked for the check, whichever wrapper (gate, db_write_gate) it went through."""
    try:
        frame = sys._getframe(1)
        while frame is not None and frame.f_code.co_filename == __file__:
            frame = frame.f_back
        return f'{Path(frame.f_code.co_filename).name}:{frame.f_lineno}:{frame.f_code.co_name}' if frame else ''
    except ValueError:
        return ''


def _append(record: dict, strict: bool):
    """strict (Production): a failure raises, so no decision is acted on without its record. Otherwise a failure never stops local work."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        if strict:
            raise


def _record(decision: Decision, operation, policy, strategy, dataset, enforced, caller):
    _append({'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'mode': decision.mode.value, 'operation': decision.operation,
             'strategy': strategy, 'dataset': dataset, 'external_side_effect': policy.external_side_effect if policy else None,
             'grant_required': policy.grant_required if policy else None, 'grant_id': decision.grant_id, 'result': decision.result,
             'enforced': enforced, 'reason': decision.reason, 'caller': caller},
            strict=bool(policy and policy.category in (Category.PRODUCTION, Category.PUBLIC_RESEARCH_FETCH, Category.RESTRICTED_EXTERNAL)))


def official_scope_reason(context, grant) -> str:
    """Preserve legacy dataset/date scope AND top-level source restrictions. Call again on the grant actually consumed."""
    sources = grant.get('sources')
    if 'sources' in grant and (not isinstance(sources, list) or not all(isinstance(s, str) for s in sources) or context.dataset not in sources):
        return 'official_fetch grant does not cover the requested dataset/source'
    nested = grant.get('scope')
    if 'scope' in grant and not isinstance(nested, dict):
        return 'official_fetch legacy scope is malformed'
    if isinstance(nested, dict):
        if set(nested) - {'datasets', 'dates'} or not isinstance(nested.get('datasets'), list) or context.dataset not in nested['datasets']:
            return 'official_fetch legacy dataset scope does not cover this request'
        allowed = nested.get('dates')
        values = dict(context.parameters)
        if not isinstance(allowed, list) or not all(isinstance(day, str) for day in allowed):
            return 'official_fetch legacy date scope is missing'
        single = values.get('date', values.get('T'))
        if single:
            days = [datetime.strptime(single, '%Y%m%d').date()]
        elif values.get('startDate') and values.get('endDate'):
            start, end = (datetime.strptime(values[k], '%Y%m%d').date() for k in ('startDate', 'endDate'))
            days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        else:
            return 'official_fetch cannot establish the requested dates within the legacy grant scope'
        if not days or any(day.isoformat() not in allowed for day in days):
            return 'official_fetch legacy date scope does not cover this request'
    return ''


def _official_ok(context=None) -> str:
    """'' when the existing official_fetch authority holds (trusted file, unexpired grant); the Research budget never replaces it. Uses are
    counted by the RequestGate and the request ledger, not here. Any error is a refusal."""
    try:
        from scripts import task_controller as tc
        path = tc.GRANTS
        if not tc.grant_trusted(path):
            return f'official endpoint needs a trusted official_fetch grant ({path} is not OS-protected)'
        grant = tc.grant_for('official_fetch', path)
        if grant is None:
            return 'no unexpired official_fetch grant'
        return official_scope_reason(context, grant) if context is not None else ''
    except Exception as exc:
        return f'official_fetch check failed: {type(exc).__name__}: {exc}'[:200]


def authorize(operation, *, strategy=None, dataset=None, task_id='authorize', scope=None, uses=1, official=False, cost=0.0,
              request_context=None, restricted_source=None) -> Decision:
    """Decide. A refusal is a Decision, not an exception; the one exception is a Production-category operation whose decision record cannot be
    written (OSError: no record, no action). There is no production=, trusted=, grants_path= or root= parameter: the mode comes from
    current_mode() and the grant file and usage log are always task_controller's GRANTS and STATE, so a caller cannot reset a grant quota."""
    caller = _caller()
    mode = current_mode()
    if not isinstance(operation, Operation):
        decision = Decision(False, 'DENY', mode, str(operation), 'unknown operation (not in the registry)')
        _record(decision, operation, None, strategy, dataset, True, caller)
        return decision
    policy = POLICIES[operation]
    grant_id = None
    allowed, reason = True, ''
    if policy.category is Category.FORBIDDEN:
        allowed, reason = False, f'{operation.value} is forbidden in every mode (Owner act, never granted by code)'
    elif policy.category is Category.PRODUCTION:
        if mode is not ExecutionMode.PRODUCTION:
            allowed, reason = False, f'{operation.value} is a Production operation; mode is {mode.value}'
        else:
            try:
                LOG.parent.mkdir(parents=True, exist_ok=True)
                LOG.open('a', encoding='utf-8').close()  # no writable record, no action (checked before a grant use is consumed)
                from scripts import task_controller as tc
                grant = tc.require_grant(task_id, operation.value, root=tc.STATE, grants_path=tc.GRANTS,
                                         scope=scope or ({'strategy': strategy} if strategy else None), uses=uses)
                grant_id = tc.grant_id(grant)
            except Exception as exc:  # any failure, including an unreadable grant file or log, is a denial
                allowed, reason = False, f'{type(exc).__name__}: {exc}'[:300]
    elif policy.category in (Category.RESTRICTED_EXTERNAL, Category.PUBLIC_RESEARCH_FETCH):
        try:
            if request_context is not None:
                from core.runtime import public_data, research_budget
                context = public_data.validate_context(request_context)
                policy = _PUBLIC if mode is ExecutionMode.RESEARCH else _EXTERNAL
                dataset = context.dataset
                # Only effective Production retains the old official authority. RESEARCH never consults it.
                reason = _official_ok(context) if mode is ExecutionMode.PRODUCTION else ''
                if not reason and mode is ExecutionMode.RESEARCH:
                    research_budget.check(context.job, rate_group=context.rate_group)  # read-only; only transport.reserve() debits
            elif operation is not Operation.FETCH_PUBLIC_DATA and restricted_source in ('gemini', 'finlab', 'yahoo', 'news_google', 'news_cnyes', 'news_yahoo', 'tpex'):
                policy = _EXTERNAL
                from scripts import task_controller as tc
                grant = tc.require_grant(task_id, 'restricted_external', root=tc.STATE, grants_path=tc.GRANTS,
                                         scope={'source': restricted_source}, uses=1)
                grant_id = tc.grant_id(grant)
            else:
                reason = 'PUBLIC_REQUEST_CONTEXT_REQUIRED: unknown or restricted source needs separate authorization'
        except FileNotFoundError as exc:
            reason = (f'RESTRICTED_EXTERNAL: {restricted_source} needs independent trusted authorization' if restricted_source
                      else f'{type(exc).__name__}: {exc}'[:300])
        except Exception as exc:
            reason = f'{type(exc).__name__}: {exc}'[:300]
        allowed = not reason
    decision = Decision(allowed, 'ALLOW' if allowed else 'DENY', mode, operation.value, reason, grant_id)
    _record(decision, operation, policy, strategy, dataset, True, caller)
    return decision


def enforce(operation, **kwargs) -> Decision:
    """authorize(), raising AuthorizationDenied on a denial: the call every side-effect function makes first."""
    decision = authorize(operation, **kwargs)
    if not decision.allowed:
        raise AuthorizationDenied(f'{decision.operation}: {decision.reason}')
    return decision


def observe(operation, *, strategy=None, dataset=None):
    """Audit-only: record that an operation ran, with mode and call path. Not an authorization: it never denies, never consults or consumes a
    grant and is never an ALLOW. ponytail: a failed record is dropped (it decides nothing); enforcement is db_write_gate with the env switch."""
    try:
        policy = POLICIES.get(operation)
        _record(Decision(True, 'OBSERVED', current_mode(), getattr(operation, 'value', str(operation)), 'audit-only phase: not enforced'),
                operation, policy, strategy, dataset, False, _caller())
    except Exception:
        pass


def gate(operation, **kwargs):
    """First line of a side-effect function that is still in its audit-only phase (database writes, official fetches, LINE publishing):
    observe by default, authorize-or-raise once the operation is listed in STOCK_LINBOT_ENFORCE. A forbidden (or unregistered) operation is
    never audit-only: it always goes through enforce() and is denied."""
    policy = POLICIES.get(operation)
    if not isinstance(operation, Operation) or policy is None or policy.category in (Category.FORBIDDEN, Category.PUBLIC_RESEARCH_FETCH, Category.RESTRICTED_EXTERNAL) or operation.value in enforced_operations():
        enforce(operation, **kwargs)
    else:
        observe(operation, strategy=kwargs.get('strategy'), dataset=kwargs.get('dataset'))


def db_write_gate(*, strategy=None, dataset=None):
    gate(Operation.WRITE_PRODUCTION_DB, strategy=strategy, dataset=dataset)


def banner(name='') -> str:
    mode = current_mode()
    enforced = sorted(enforced_operations())
    text = f"MODE: {mode.value} | GATES: {'ENFORCING ' + ','.join(enforced) if enforced else 'AUDIT_ONLY (not enforced)'}"
    text += ' | PUBLIC_RESEARCH_HTTP: ENFORCED'
    if mode is ExecutionMode.PRODUCTION:
        text += ' | WARNING: Production mode; official and irreversible actions need Owner grants'
    return f'[{name}] {text}' if name else text


def announce(name=''):
    """Entry-point banner on stderr (stdout of the CLIs and its parsers stays unchanged)."""
    print(banner(name), file=sys.stderr)
