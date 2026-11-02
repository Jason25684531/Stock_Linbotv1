"""Resolve research reads through the existing public-data authorization boundary."""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from core.research.sources.twse import _exclusive, find_closing_table
from core.runtime import public_data, research_budget
from core.runtime.operation_policy import AuthorizationDenied

DETAIL_FIELDS = ('cash_dividend', 'stock_dividend', 'rights_terms')


@dataclass(frozen=True)
class Provenance:
    source: str
    fetched_at: str | None
    sha256: str
    grant_scope: str
    tier: str


def request_approval(path, need):
    required = ('source', 'purpose', 'max_requests', 'cost', 'licence_limits', 'alternatives')
    if any(key not in need for key in required):
        raise ValueError('incomplete approval request')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive(path.with_suffix(path.suffix + '.lock')):
        requests = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if not isinstance(requests, list):
            raise ValueError('approval requests must be a list')
        # The Owner needs explicit values; an unknown budget or cost is written as UNKNOWN, never left blank.
        row = {key: ('UNKNOWN' if need[key] is None else need[key]) for key in (*required, 'field', 'stock_id', 'date') if key in need}
        if not isinstance(row['max_requests'], int) or isinstance(row['max_requests'], bool):
            row['flag'] = 'NOT_APPROVABLE'  # the Owner cannot approve an entry without a numeric request cap
        if row not in requests:
            requests.append(row)
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(requests, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)


def _supported(ctx, need):
    try:
        day = date.fromisoformat(need['date'])
        return (getattr(ctx, 'dataset', None) == 'MI_INDEX' and need.get('field') in ('close_price', 'volume')
                and isinstance(need.get('stock_id'), str) and bool(need['stock_id']) and need['stock_id'] == need['stock_id'].strip()
                and need['date'] == day.isoformat() and dict(ctx.parameters).get('date') == day.strftime('%Y%m%d'))
    except (KeyError, TypeError, ValueError):
        return False


def _source_value(response, need):
    """A verified source is not proof that a requested stock/field exists."""
    table = find_closing_table(response.json())
    fields = table['fields']
    stock_index = fields.index('證券代號')
    value_index = fields.index('收盤價' if need['field'] == 'close_price' else '成交股數')
    rows = [row for row in table['data'] if str(row[stock_index]).strip() == need['stock_id']]
    if len(rows) > 1:
        raise AuthorizationDenied('SOURCE_DUPLICATE_STOCK')
    if not rows:
        return None
    try:
        value = Decimal(str(rows[0][value_index]).replace(',', ''))
        if not value.is_finite() or value < 0 or (need['field'] == 'close_price' and value == 0):
            return None
        if need['field'] == 'volume':
            return int(value) if value == value.to_integral_value() else None
        price = float(value)
        return price if math.isfinite(price) else None
    except (InvalidOperation, ValueError, IndexError):
        return None


def resolve(candidates, approval_path, need, diagnostic=None):
    """Candidates are existing public-data request arguments in priority order.

    Verified cache precedes any authorized network attempt. The public-data boundary
    decides authorization and budget; no caller flag can grant an HTTP request.
    """
    contexts = []
    for candidate in candidates:
        try:
            ctx = public_data.context(candidate['url'], method=candidate.get('method', 'GET'),
                                      params=candidate.get('params'), data=candidate.get('data'),
                                      purpose=candidate.get('purpose'))
        except AuthorizationDenied:
            continue
        if getattr(ctx, 'dataset', None) == 'TWT49U_DETAIL' and need.get('field') in DETAIL_FIELDS:
            # Approved source, unconfirmed schema: public_data rejects every detail payload, so never read, send or certify.
            return {'status': 'DATA_BLOCKED', 'reason': 'OD_P6_SCHEMA_UNCONFIRMED', 'provenance': None}
        if not _supported(ctx, need):
            continue
        # A corrupt cache is a safety failure, not permission to try another source.
        response = public_data.cached(ctx)
        if response is None:
            contexts.append((candidate, ctx))
            continue
        value = _source_value(response, need)
        if value is None:
            continue
        provenance = Provenance(ctx.source, None, hashlib.sha256(response.content).hexdigest(), ctx.purpose, 'LOCAL_OFFICIAL_CACHE')
        return {'status': 'AVAILABLE', 'value': value, 'certification': 'SOURCE_VALUE_ONLY',
                'content': response.content, 'provenance': asdict(provenance)}
    for candidate, ctx in contexts:
        if ctx.job not in research_budget.CAPS:
            continue
        try:
            response = public_data.request(candidate.get('method', 'GET'), candidate['url'],
                                           params=candidate.get('params'), data=candidate.get('data'),
                                           purpose=candidate.get('purpose'))
            value = _source_value(response, need)
            if value is None:
                break
            cached = getattr(response, 'research_cache_hit', False)
            provenance = Provenance(ctx.source, None if cached else datetime.now(timezone.utc).isoformat(),
                                    hashlib.sha256(response.content).hexdigest(), ctx.purpose,
                                    'LOCAL_OFFICIAL_CACHE' if cached else 'APPROVED_OFFICIAL')
            return {'status': 'AVAILABLE', 'value': value, 'certification': 'SOURCE_VALUE_ONLY',
                    'content': response.content, 'provenance': asdict(provenance)}
        except (AuthorizationDenied, research_budget.BudgetDenied):
            # In particular, never work around a source stop by trying another endpoint.
            break
    result = {'status': 'DATA_BLOCKED', 'reason': 'no certifying source available', 'provenance': None}
    try:
        request_approval(approval_path, need)
    except ValueError as exc:
        result['approval_request'] = f'NOT_WRITTEN: {exc}'
    if diagnostic is not None:
        result['content'] = diagnostic
        result['provenance'] = asdict(Provenance(need['source'], None, hashlib.sha256(diagnostic).hexdigest(),
                                                 'diagnostic only', 'THIRD_PARTY_DIAGNOSTIC'))
    return result
