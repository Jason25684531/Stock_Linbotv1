import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'outputs/research_mode/legacy_validation_v1_baseline/baseline.json'
NAMES = {'hybrid_trend_rank': '趨勢混合選股', 'defensive_low_volatility': '低波動選股', 'growth_momentum_breakout': '雙渦輪成長',
         'quality_growth': '經營效益', 'institutional_flow_confirmation': '籌碼動能', 'mean_reversion': '均值回歸',
         'quality_value_low_volatility': '品質價值低波動'}


@pytest.fixture
def catalog():
    from app import web_server
    return {item['strategy_id']: item for item in web_server._build_strategy_catalog()}


def test_primary_labels_have_no_version(catalog):
    for sid, name in NAMES.items():
        item = catalog[sid]
        assert item['display_name'] == item['display_name_zh'] == name
        assert not re.search(r'V\d+', item['display_name'])
        assert item['factor_summary'] and item['gate_result']
    assert 'AI 模型不可用' in catalog['hybrid_trend_rank']['factor_summary']
    assert '殖利率' not in catalog['quality_value_low_volatility']['display_name']


def test_templates_do_not_use_version_alias_as_label():
    for name in ('strategy_center.html', 'dashboard.html'):
        text = (ROOT / 'templates' / name).read_text(encoding='utf-8')
        assert 'aliases[0]' not in text and '...(item.aliases' not in text


def test_legacy_keys_still_resolve():
    from core.strategy_manager import StrategyManager
    manager = StrategyManager()
    assert manager.resolve('v31') == manager.resolve('v31_hybrid') == manager.resolve('hybrid_trend_rank') == 'hybrid_trend_rank'
    assert manager.resolve('v38_value_dividend') == 'quality_value_low_volatility'


@pytest.mark.skipif(not BASELINE.is_file(), reason='baseline snapshot of this change is local evidence')
def test_api_ids_aliases_and_line_names_unchanged():
    import app as app_module
    from core.strategy_manager import StrategyManager
    before = json.loads(BASELINE.read_text(encoding='utf-8'))
    body = app_module.app.test_client().get('/api/strategies').get_json()
    now = [[item.get('strategy_id'), item.get('aliases')] for item in body['strategies']]
    assert now == before['api_strategies']['ids_aliases']
    manager = StrategyManager()
    for sid, display in before['class_display_names'].items():
        assert manager.get_strategy(sid).display_name == display  # LINE builds its text from these
