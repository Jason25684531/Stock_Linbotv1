"""One public Research HTTP boundary: exact source scope, durable debit, host pacing, verified cache and audit.

No caller-supplied budget, ledger path, round, free/official flag, redirect or retry transport override.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import parse_qsl, urlsplit

import requests

from core.runtime import research_budget as budget
from core.runtime.operation_policy import AuthorizationDenied

SCOPE = Path(__file__).with_name('public_data_scope.json')
SCOPE_SHA256 = 'bb1ec91bc703ae1b1ed5f18024e8fbff5e9a09ce1be29bbdd53f21d07f5410f8'
DETAIL_PATHS = ('/rwd/zh/exRight/TWT49UDetail', '/exchangeReport/TWT49UDetail')
T1B_PATHS = ('/rwd/zh/reducation/TWTAUU', '/rwd/zh/change/TWTB8U')
TWSE_PATHS = {
    '/rwd/zh/afterTrading/MI_INDEX': 'MI_INDEX',
    '/rwd/zh/fund/T86': 'T86',
    '/rwd/zh/marginTrading/MI_MARGN': 'MI_MARGN',
    '/rwd/zh/exRight/TWT49U': 'TWT49U',
}
OPENAPI_PATHS = {
    '/v1/holidaySchedule/holidaySchedule': 'holidaySchedule',
    '/v1/opendata/t187ap03_L': 'company_profile',
    '/v1/company/suspendListingCsvAndHtml': 'delisted',
}
POLICY_VERSION = 'research-public-data-v1'
RESEARCH_HEADER = 'X-Stock-Research-Policy'


def _scope():
    raw = SCOPE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCOPE_SHA256:
        raise AuthorizationDenied('SOURCE_SCOPE_INVALID: registered event manifest changed')
    return json.loads(raw)


@dataclass(frozen=True)
class RequestContext:
    method: str
    url: str
    parameters: tuple
    purpose: str
    dataset: str
    source: str
    job: str | None
    rate_group: str

    @property
    def key(self):
        # Both registered TWT49UDetail URLs identify the same official dataset.
        endpoint = 'twse:TWT49UDetail' if self.dataset == 'TWT49U_DETAIL' else self.url
        return hashlib.sha256(json.dumps([self.source, self.dataset, endpoint, self.method, self.parameters, self.purpose], sort_keys=True).encode()).hexdigest()

    def audit_json(self):
        return json.dumps({'source': self.source, 'endpoint': self.url, 'method': self.method,
                           'parameters': self.parameters, 'purpose': self.purpose, 'dataset': self.dataset,
                           'round': budget.ROUND, 'job': self.job, 'request_key': self.key}, ensure_ascii=False, sort_keys=True)


def context(url, *, method='GET', params=None, data=None, purpose=None):
    """Resolve identity from the actual URL/form. No wildcard domains or arbitrary purpose-to-budget mapping."""
    try:
        if not isinstance(url, str) or any(ord(char) < 32 or ord(char) == 127 for char in url):
            raise ValueError('URL control characters')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
            raise ValueError('origin')
        if not isinstance(method, str):
            raise ValueError('method')
        method = method.upper()
        if method == 'GET' and data is not None:
            raise ValueError('GET request body is not an approved query')
        if method == 'POST' and (parsed.query or params):
            raise ValueError('POST scope requires the registered form, not query parameters')
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        pairs += list((params or {}).items()) + list((data or {}).items())
        if len({k for k, _ in pairs}) != len(pairs) or any(not isinstance(k, str) or not isinstance(v, (str, int)) or isinstance(v, bool) for k, v in pairs):
            raise ValueError('ambiguous parameters')
        values = {k: str(v) for k, v in pairs}
        method = method.upper()
        dataset, job, source, group, expected = None, None, 'twse', 'twse', None
        path, host = parsed.path, parsed.hostname
        if host == 'www.twse.com.tw' and method == 'GET':
            if path in DETAIL_PATHS:
                if set(values) != {'STK_NO', 'T', 'response'} or values['response'] != 'json':
                    raise ValueError('detail parameters')
                if [values['STK_NO'], values['T']] not in _scope()['od_p6_targets']:
                    raise ValueError('event outside registered research scope')
                dataset, job, expected = 'TWT49U_DETAIL', 'od_p6', 'od_p6'
            elif path in T1B_PATHS:
                if values != {'startDate': '20220101', 'endDate': '20261023', 'response': 'json'}:
                    raise ValueError('reference-price window')
                dataset, job, expected = path.rsplit('/', 1)[-1], 't1b', 't1b'
            elif path in TWSE_PATHS:
                allowed = {'date', 'type', 'selectType', 'response'} if path != '/rwd/zh/exRight/TWT49U' else {'startDate', 'endDate', 'response'}
                if not set(values) <= allowed or values.get('response') != 'json':
                    raise ValueError('market parameters')
                for key in ('date', 'startDate', 'endDate'):
                    if key in values:
                        datetime.strptime(values[key], '%Y%m%d')
                if any(values.get(k, 'ALL') != 'ALL' for k in ('type', 'selectType')):
                    raise ValueError('unsupported market selector')
                dataset, expected = TWSE_PATHS[path], 'market_research'
        elif host == 'openapi.twse.com.tw' and method == 'GET' and path in OPENAPI_PATHS:
            dataset = OPENAPI_PATHS[path]
            if dataset == 'holidaySchedule':
                if values != {'year': '2026'}:
                    raise ValueError('explicit 2026 calendar required')
                job, expected = 'calendar_2026', 'calendar_2026'
            elif values:
                raise ValueError('unexpected OpenAPI parameters')
            else:
                expected = 'market_research'
        elif host == 'doc.twse.com.tw' and path == '/server-java/t57sb01' and method in ('GET', 'POST'):
            allowed = {'id', 'key', 'step', 'co_id', 'year', 'seamon', 'season', 'mtype', 'dtype'}
            if not set(values) <= allowed or values.get('id', '') or values.get('key', ''):
                raise ValueError('unapproved publication form')
            if values.get('co_id') and not re.fullmatch(r'\d{4,6}', values['co_id']):
                raise ValueError('ticker')
            if method == 'POST':
                if set(values) != {'id', 'key', 'step', 'co_id', 'year', 'seamon', 'mtype', 'dtype'} or any(values[k] != v for k, v in {'id': '', 'key': '', 'step': '1', 'mtype': 'A', 'dtype': 'AI1'}.items()):
                    raise ValueError('only the registered public filing form is approved')
                if values['seamon'] not in ('', '1', '2', '3', '4') or (values['year'] and not re.fullmatch(r'\d{2,3}', values['year'])):
                    raise ValueError('publication period')
            elif values and (set(values) != {'co_id', 'year', 'season'} or values['season'] not in ('1', '2', '3', '4') or not re.fullmatch(r'\d{2,3}', values['year'])):
                raise ValueError('publication query')
            source, group, dataset, expected = 'mops', 'mops', 't57sb01', 'mops_research'
        elif host == 'mopsov.twse.com.tw' and path == '/mops/web/ajax_t163sb04' and method == 'POST':
            if set(values) != {'encodeURIComponent', 'step', 'firstin', 'off', 'TYPEK', 'year', 'season'} or values.get('TYPEK') not in ('sii', 'otc') or any(values[k] != '1' for k in ('encodeURIComponent', 'step', 'firstin', 'off')) or not re.fullmatch(r'\d{2,3}', values['year']) or values['season'] not in ('01', '02', '03', '04'):
                raise ValueError('unapproved quarterly form')
            source, group, dataset, expected = 'mops', 'mops', 't163sb04', 'mops_research'
        elif host == 'mopsov.twse.com.tw' and method == 'GET' and re.fullmatch(r'/nas/t21/(sii|otc)/t21sc03_\d{3}_(0?[1-9]|1[0-2])_0\.html', path):
            if values:
                raise ValueError('unexpected monthly-revenue parameters')
            source, group, dataset, expected = 'mops', 'mops', 'monthly_revenue', 'mops_research'
        if dataset is None or (purpose is not None and purpose != expected):
            raise ValueError('unapproved endpoint or research purpose')
        clean_url = f'https://{host}{path}'
        return RequestContext(method, clean_url, tuple(sorted(values.items())), expected, dataset, source, job, group)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AuthorizationDenied('SOURCE_NOT_ALLOWLISTED: endpoint, method, parameters or purpose outside approved public research scope') from exc


def validate_context(value):
    if not isinstance(value, RequestContext):
        raise AuthorizationDenied('PUBLIC_REQUEST_CONTEXT_REQUIRED')
    checked = context(value.url, method=value.method, purpose=value.purpose,
                      **{'params' if value.method == 'GET' else 'data': dict(value.parameters)})
    if checked != value:
        raise AuthorizationDenied('SOURCE_CONTEXT_FORGED')
    return checked


def _cache_paths(ctx):
    folder = Path(budget.STATE) / 'cache'
    return folder / f'{ctx.key}.bin', folder / f'{ctx.key}.json'


def cached(ctx):
    raw_path, meta_path = _cache_paths(ctx)
    if not raw_path.exists() and not meta_path.exists():
        return None
    try:
        raw, meta = raw_path.read_bytes(), json.loads(meta_path.read_text(encoding='utf-8'))
        provenance = meta['context']
        registered = context(provenance['endpoint'], method=provenance['method'], purpose=provenance['purpose'],
                             **{'params' if provenance['method'] == 'GET' else 'data': dict(provenance['parameters'])})
        if meta['key'] != ctx.key or registered.key != ctx.key or provenance != json.loads(registered.audit_json()) or meta['sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError('cache provenance or hash mismatch')
        response = requests.Response()
        response.status_code, response._content, response.url = meta['status'], raw, ctx.url
        response.headers.update(meta['headers'])
        verify_response(ctx, response)
        response.research_cache_hit = True
        return response
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AuthorizationDenied('CACHE_INVALID: verified source, hash and schema required') from exc


def verify_response(ctx, response):
    if response.status_code != 200:
        raise AuthorizationDenied(f'HTTP_STOP: status {response.status_code}; no automatic retry or redirect')
    if ctx.source == 'mops':
        try:
            text = response.content.decode('utf-8')
        except UnicodeDecodeError:
            text = response.content.decode('big5', errors='replace')
        if any(word in text.lower() for word in ('captcha', 'access denied', 'login', '查詢過量', '驗證碼')):
            raise AuthorizationDenied('SOURCE_BLOCKED: no login or rate-limit circumvention')
        expected = ('證券代號', '資料年度', '上傳日期', '電子檔案') if ctx.dataset == 't57sb01' else ('公司代號', '營業收入')
        if '<table' not in text.lower() or not all(field in text for field in expected):
            raise ValueError('MOPS response lacks the expected public data table')
        return
    payload = response.json()
    if ctx.dataset == 'MI_INDEX' and isinstance(payload, dict) and payload.get('stat') in (
            '很抱歉，沒有符合條件的資料!', '查詢日期大於今日，請重新查詢!', '查詢日期小於93年2月11日，請重新查詢!'):
        return  # documented source classifications, never a guessed holiday or fabricated price
    if ctx.dataset in ('TWT49U_DETAIL', 'TWTAUU', 'TWTB8U', 'TWT49U', 'MI_INDEX', 'T86', 'MI_MARGN'):
        if not isinstance(payload, dict) or payload.get('stat') != 'OK' or not isinstance(payload.get('data', payload.get('tables')), list):
            raise ValueError('official payload status or schema invalid')
        requested_date = dict(ctx.parameters).get('date')
        if requested_date and str(payload.get('date', '')).replace('-', '').replace('/', '') != requested_date:
            raise ValueError('SOURCE_DATE_MISMATCH: payload differs from the requested trading date')
        tables = payload.get('tables') if ctx.dataset in ('MI_INDEX', 'MI_MARGN') and payload.get('tables') is not None else [payload]
        if not isinstance(tables, list) or not tables:
            raise ValueError('official data tables missing')
        valid_fields = []
        for table in tables:
            if not isinstance(table, dict):
                raise ValueError('invalid table shape')
            fields, rows = table.get('fields'), table.get('data')
            if not isinstance(fields, list) or not fields or not all(isinstance(f, str) and f.strip() for f in fields) or len(set(fields)) != len(fields):
                raise ValueError('official data fields missing or ambiguous')
            if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(fields) or any(isinstance(v, (dict, list)) for v in row) for row in rows):
                raise ValueError('official data row shape or width differs from fields')
            valid_fields.append(set(fields))
        if ctx.dataset in ('TWTAUU', 'TWTB8U'):
            required = {'恢復買賣日期', '股票代號', '停止買賣前收盤價格', '恢復買賣參考價'}
            if not required <= valid_fields[0]:
                raise ValueError('reference-price schema drift: required fields missing')
            from core.research.sources.twse import parse_twse_date
            date_index = payload['fields'].index('恢復買賣日期')
            window = dict(ctx.parameters)
            for row in payload['data']:
                actual = parse_twse_date(str(row[date_index])).strftime('%Y%m%d')
                if not window['startDate'] <= actual <= window['endDate']:
                    raise ValueError('SOURCE_DATE_MISMATCH: reference-price row outside the registered window')
        if ctx.dataset == 'TWT49U_DETAIL':
            # The repository has only failed/busy detail responses, no authoritative successful schema or parser.
            # Retain the actual raw response in the attempt audit; never certify an arbitrary table as action terms.
            raise ValueError('OD_P6_SCHEMA_UNCONFIRMED: retain raw response for schema review; no verified detail contract yet')
        if ctx.dataset == 'TWT49U':
            from core.research.sources.twse import REQUIRED_ACTION_FIELDS
            if not REQUIRED_ACTION_FIELDS <= valid_fields[0]:
                raise ValueError('corporate-action schema drift')
        if ctx.dataset == 'MI_INDEX':
            from core.research.sources.twse import REQUIRED_CLOSING_FIELDS
            if not any(REQUIRED_CLOSING_FIELDS <= fields for fields in valid_fields):
                raise ValueError('daily closing schema drift')
    elif ctx.dataset == 'holidaySchedule':
        if not isinstance(payload, list) or not payload or not all('2026' in str(row) for row in payload):
            raise ValueError('calendar response does not establish 2026 coverage')
    elif not isinstance(payload, list):
        raise ValueError('OpenAPI array expected')


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    with temp.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def import_verified_t1b_cache():
    """Reuse the two already verified raw responses. Fixed source hashes prevent an import from silently trusting a new file."""
    sources = {
        'TWTAUU': ('TWTAUU_20220101_20261023_2026-10-24T131940+0000.bin', '6237fc3b3b2861f9d742d4a2ffcb1c0a251103c732f882b000220b6f738bedd6', 6),
        'TWTB8U': ('TWTB8U_20220101_20261023_2026-10-24T131954+0000.bin', 'ce4045ddeeaa0df53d7104f079efc7c123ac4b19365f257856aaf0af5aebad4b', 9),
    }
    imported = []
    for name, (filename, digest, row) in sources.items():
        ctx = context('https://www.twse.com.tw' + (T1B_PATHS[0] if name == 'TWTAUU' else T1B_PATHS[1]),
                      params={'startDate': '20220101', 'endDate': '20261023', 'response': 'json'})
        if cached(ctx) is not None:
            continue
        source = budget.ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official' / filename
        if not source.is_file():
            continue  # missing raw evidence is not fabricated; no HTTP is performed by initialization
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise AuthorizationDenied('LEGACY_CACHE_HASH_MISMATCH')
        response = requests.Response()
        response.status_code, response._content = 200, raw
        verify_response(ctx, response)
        raw_path, meta_path = _cache_paths(ctx)
        _atomic(raw_path, raw)
        _atomic(meta_path, json.dumps({'key': ctx.key, 'context': json.loads(ctx.audit_json()), 'sha256': digest,
                                      'status': 200, 'headers': {'Content-Type': 'application/json'},
                                      'attempt': f'legacy:t1b:{row}', 'legacy_file': filename}).encode('utf-8'))
        imported.append(name)
    return imported


def _send(method, url, **kwargs):
    """Private real sender: no .netrc, proxy credentials, cookie jar, arbitrary hooks or caller-controlled retry adapters."""
    with requests.Session() as client:
        client.trust_env = False
        client.auth = None
        client.cookies.clear()
        client.hooks = {'response': []}
        client.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
        client.headers['User-Agent'] = 'StockLinbot-Research/1.0'
        return client.request(method, url, **kwargs)


def _check_source_stop(group):
    path = Path(budget.STATE) / f'{group}.cooldown.json'
    if path.exists():
        try:
            until = float(json.loads(path.read_text(encoding='utf-8'))['until'])
            if until > time.time():
                raise AuthorizationDenied(f'SOURCE_COOLDOWN: {group}; retry after {until:.3f}')
        except AuthorizationDenied:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AuthorizationDenied('SOURCE_COOLDOWN_STATE_INVALID') from exc


def _persist_source_stop(group, response):
    if response is not None:
        until = budget.response_cooldown(response)
        if until is not None:
            _atomic(Path(budget.STATE) / f'{group}.cooldown.json', json.dumps({'until': until, 'status': response.status_code,
                                                                          'reason': 'upstream rate limit or blocking response'}).encode())


def request(method, url, *, params=None, data=None, purpose=None, headers=None, timeout=30, session=None):
    """A single HTTP attempt. Success is cached by canonical identity; failed/unknown requests retain their debit."""
    from core.runtime import operation_policy as policy
    from core.research.sources.twse import RequestGate, _exclusive
    ctx = context(url, method=method, params=params, data=data, purpose=purpose)
    hit = cached(ctx)
    if hit is not None:
        return hit  # verified local data remains usable without any network budget or grant
    with _exclusive(Path(budget.STATE) / f'{ctx.rate_group}.send.lock'):
        hit = cached(ctx)
        if hit is not None:
            return hit
        _check_source_stop(ctx.rate_group)
        policy.enforce(policy.Operation.DOWNLOAD_MARKET_DATA, dataset=ctx.dataset, official=True, request_context=ctx)
        # Don't accept an authenticated/retrying session through the public path.
        effective_headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        if session is not None:
            raise AuthorizationDenied('PUBLIC_SESSION_RESTRICTED: caller sessions, cookies and hooks are not a public transport')
        allowed_headers = {'user-agent', 'accept', 'accept-language', 'accept-encoding', 'content-type', 'origin', 'referer', 'connection'}
        if set(effective_headers) - allowed_headers:
            raise AuthorizationDenied('PUBLIC_CREDENTIALS_FORBIDDEN')
        mode = policy.current_mode()
        if mode is policy.ExecutionMode.RESEARCH:
            attempt = budget.reserve(ctx)
        else:
            from scripts import task_controller as tc
            grant = tc.require_grant('authorize', 'official_fetch', root=tc.STATE, grants_path=tc.GRANTS,
                                     scope=None, uses=1)
            reason = policy.official_scope_reason(ctx, grant)
            if reason:
                raise AuthorizationDenied(reason)  # do not act on an Owner grant changed between precheck and consumption
            attempt = uuid.uuid4().hex
            policy._append({'operation': 'production_official_http', 'attempt': attempt, 'grant_id': tc.grant_id(grant),
                            'context': json.loads(ctx.audit_json()), 'result': 'RESERVED'}, strict=True)

        def finish(outcome, response=None, error=None):
            _persist_source_stop(ctx.rate_group, response)
            if mode is policy.ExecutionMode.RESEARCH:
                budget.finish(attempt, outcome=outcome, released_at=gate.last_release_wall, response=response, error=error)
            else:
                policy._append({'operation': 'production_official_http', 'attempt': attempt, 'result': outcome,
                                'status': response.status_code if response is not None else None, 'error': error,
                                'sha256': hashlib.sha256(response.content).hexdigest() if response is not None else None}, strict=True)
        # A prior sender may have paused after gate release; wait from this lock acquisition as well.
        gate = RequestGate(3.2, state_path=Path(budget.STATE) / f'{ctx.rate_group}.gate', delay_first_request=True)
        response = None
        try:
            gate.wait()
            kwargs = {'params': dict(ctx.parameters) if ctx.method == 'GET' else None, 'timeout': timeout, 'allow_redirects': False, 'verify': True}
            if ctx.method == 'POST':
                kwargs['data'] = dict(ctx.parameters)
            if headers is not None:
                kwargs['headers'] = dict(effective_headers)
            # Build the real request from the validated immutable context, never the mutable caller inputs.
            response = _send(ctx.method, ctx.url, **kwargs)
            verify_response(ctx, response)
        except BaseException as exc:
            finish('UNKNOWN' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'FAILED', response=response,
                   error=f'{type(exc).__name__}: {str(exc)[:250]}')
            raise
        finish('VERIFIED', response=response)
        raw_path, meta_path = _cache_paths(ctx)
        _atomic(raw_path, response.content)
        metadata = {'key': ctx.key, 'context': json.loads(ctx.audit_json()), 'sha256': hashlib.sha256(response.content).hexdigest(),
                    'status': response.status_code, 'headers': {'Content-Type': response.headers.get('Content-Type', '')}, 'attempt': attempt}
        _atomic(meta_path, json.dumps(metadata, ensure_ascii=False).encode('utf-8'))
        response.research_cache_hit = False
        return response


def restricted(source):
    """Independent trusted authorization for paid, authenticated or non-allowlisted sources, never a public grant."""
    from core.runtime.operation_policy import Operation, enforce
    return enforce(Operation.CALL_RESEARCH_API, restricted_source=source)


def restricted_sdk(source):
    """Opaque SDK retries cannot be metered as public HTTP; Research never treats SDK invocation counts as real request counts."""
    from core.runtime import operation_policy as policy
    if policy.current_mode() is policy.ExecutionMode.RESEARCH:
        policy._append({'operation': 'call_research_api', 'source': source, 'mode': 'RESEARCH', 'result': 'DENY',
                        'reason': 'UNMETERED_RESTRICTED_SDK', 'grant_required': True}, strict=True)
        raise AuthorizationDenied('UNMETERED_RESTRICTED_SDK: independent authorization alone cannot establish per-HTTP budget enforcement')
    return restricted(source)


def restricted_http(source, method, url, *, params=None, headers=None, timeout=20):
    """Separate per-HTTP Owner quota for known restricted sources. Never borrows the 396 public attempts."""
    from core.runtime import operation_policy as policy
    from core.research.sources.twse import RequestGate, _exclusive
    parsed = urlsplit(url)
    origins = {'news_google': ('news.google.com', '/rss/search'), 'news_yahoo': ('tw.stock.yahoo.com', '/rss'),
               'news_cnyes': ('news.cnyes.com', '/api/v3/news/category/'), 'tpex': ('www.tpex.org.tw', '/')}
    expected = origins.get(source)
    tpex_paths = ('/www/zh-tw/afterTrading/dailyQuotes', '/web/stock/3insti/daily_trade/3itrade_hedge_result.php',
                  '/web/stock/margin_trading/margin_balance/margin_bal_result.php')
    if not expected or method != 'GET' or parsed.scheme != 'https' or parsed.hostname != expected[0] or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
        raise AuthorizationDenied('RESTRICTED_SOURCE_NOT_REGISTERED')
    if (source == 'tpex' and parsed.path not in tpex_paths) or (source != 'tpex' and not parsed.path.startswith(expected[1])):
        raise AuthorizationDenied('RESTRICTED_ENDPOINT_NOT_REGISTERED')
    with _exclusive(Path(budget.STATE) / f'{source}.send.lock'):
        _check_source_stop(source)
        restricted(source)  # require_grant consumes this actual HTTP's independently authorized quota
        gate = RequestGate(3.2, state_path=Path(budget.STATE) / f'{source}.gate', delay_first_request=True)
        gate.wait()
        response = None
        try:
            response = _send(method, url, params=params, headers=headers, timeout=timeout, allow_redirects=False, verify=True)
            if response.status_code != 200:
                raise AuthorizationDenied(f'RESTRICTED_HTTP_STOP: {response.status_code}')
            return response
        finally:
            _persist_source_stop(source, response)
            policy._append({'operation': 'restricted_http', 'source': source, 'endpoint': f'https://{parsed.hostname}{parsed.path}',
                            'status': response.status_code if response is not None else None,
                            'sha256': hashlib.sha256(response.content).hexdigest() if response is not None else None,
                            'result': 'RECEIVED' if response is not None else 'UNKNOWN'}, strict=True)


def internal_service(base_url, endpoint):
    """Internal RPC is not upstream HTTP quota. The service must enforce its own real upstream requests."""
    from core.runtime import operation_policy as policy
    if policy.current_mode() is policy.ExecutionMode.PRODUCTION:
        return  # existing Production service deployment contract, no Research elevation
    routes = {'/v1/stock-basic-snapshot', '/v1/foreign-investor-flow', '/v1/historical-financial-statements',
              '/v1/tools/get_company_basic_info', '/v1/tools/get_market_statistics', '/v1/tools/get_foreign_investment',
              '/v1/tools/twse_stock_trend', '/v1/tools/investment_screening', '/v1/tools/market_hotspot'}
    if base_url.rstrip('/') not in ('http://127.0.0.1:8080', 'http://localhost:8080', 'http://twse-mcp-server:8080') or endpoint not in routes:
        raise AuthorizationDenied('INTERNAL_SERVICE_NOT_ALLOWLISTED')
    policy._append({'operation': 'internal_research_rpc', 'source': base_url, 'endpoint': endpoint,
                    'mode': 'RESEARCH', 'grant_required': False, 'result': 'PRECHECK', 'upstream_policy': 'requires_health_capability'}, strict=True)
