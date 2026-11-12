import json

import pytest

from core.research.intelligence import definition, profile


def _module(tmp_path, body):
    path = tmp_path / 'pkg' / 'strat.py'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    return path


SOURCE = '''LIMIT = 3


class S:
    def pick(self, df):
        return df.head(LIMIT)
'''


def _card(text='buy the top rows'):
    fields = {name: {'text': f'{name} text', 'sources': [{'ref': 'pkg/strat.py::S.pick'}]} for name in definition.REQUIRED}
    fields['selection'] = {'text': text, 'sources': [{'ref': 'pkg/strat.py::S.pick'}, {'ref': 'pkg/strat.py::LIMIT'}]}
    fields['pit_dependencies'] = {'text': 'should be dropped', 'sources': []}
    return {'strategy_id': 's', 'fields': fields}


def test_traced_stale_and_unverified(tmp_path):
    path = _module(tmp_path, SOURCE)
    card = definition.stamp(_card(), root=tmp_path)
    built = definition.build(card, root=tmp_path)
    sel = built['fields']['selection']
    assert sel['status'] == 'TRACED'
    assert sel['sources'][1]['lines'] == [1, 1]
    assert built['fields']['pit_dependencies'] == {'status': 'UNVERIFIED_DEFINITION', 'text': None, 'sources': []}
    assert built['definition_id'].startswith('SD1-')

    path.write_text(SOURCE.replace('head(LIMIT)', 'tail(LIMIT)'), encoding='utf-8')
    rebuilt = definition.build(card, root=tmp_path)
    assert rebuilt['fields']['selection']['status'] == 'STALE_DEFINITION'
    assert rebuilt['definition_id'] != built['definition_id']


def test_missing_symbol_fails(tmp_path):
    _module(tmp_path, SOURCE)
    card = _card()
    card['fields']['entry']['sources'] = [{'ref': 'pkg/strat.py::S.nope'}]
    with pytest.raises(KeyError, match='SOURCE_SYMBOL_NOT_FOUND'):
        definition.stamp(card, root=tmp_path)


def test_stamp_never_overwrites_a_recorded_hash(tmp_path):
    _module(tmp_path, SOURCE)
    card = _card()
    card['fields']['entry']['sources'][0]['sha256'] = 'old'
    assert definition.stamp(card, root=tmp_path)['fields']['entry']['sources'][0]['sha256'] == 'old'


def _profile():
    return {'version': 1, 'targets': {'cagr_basic': 0.05, 'cagr_priority': 0.10},
            'sample_rules': {'id': 'SR1', 'version': 1, 'complete_trusted_years': 3},
            'types': {'trend_momentum': {'label': 't', 'questions': [], 'primary_metrics': [], 'auxiliary_metrics': [], 'main_risks': [],
                                         'suitable_regimes': [], 'unsuitable_regimes': [],
                                         'edge_checks': [{'metric': 'profit_factor', 'op': '>', 'value': 1, 'source': 'DEFINITIONAL'}]}},
            'strategies': {'s': {'primary_type': 'trend_momentum', 'secondary_types': [], 'evidence': 'category trend'}}}


def test_profile_rejects_an_unsourced_threshold():
    data = _profile()
    profile.validate(data)
    data['types']['trend_momentum']['edge_checks'].append({'metric': 'max_drawdown', 'op': '>', 'value': -0.3, 'source': 'DEFINITIONAL'})
    with pytest.raises(ValueError, match='THRESHOLD_SOURCE'):
        profile.validate(data)


def test_profile_one_primary_type():
    data = _profile()
    data['strategies']['s']['primary_type'] = 'unknown'
    with pytest.raises(ValueError, match='PRIMARY_TYPE'):
        profile.validate(data)


def test_freeze_blocks_a_changed_profile(tmp_path):
    data = _profile()
    path = tmp_path / 'freeze.json'
    first = profile.freeze(data, path, runs=['r1'])
    assert profile.check_frozen(data, path) == first['profile_id']
    assert profile.freeze(data, path, runs=['r2']) == first
    with pytest.raises(ValueError, match='PROFILE_NOT_FROZEN'):
        profile.check_frozen(dict(data, version=2), path)
    second = profile.freeze(dict(data, version=2), path, runs=['r1'], reason='review fix')
    versions = json.loads(path.read_text())['versions']
    assert [v['profile_id'] for v in versions] == [first['profile_id'], second['profile_id']] and versions[1]['reason'] == 'review fix'
    with pytest.raises(ValueError, match='PROFILE_NOT_FROZEN'):
        profile.check_frozen(data, path)
