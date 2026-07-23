from pathlib import Path

from jobs.run_backtest import build_arg_parser


def test_backtest_launchers_keep_the_legacy_parameter_surface():
    required_options = {
        '--strategy', '--strategies', '--portfolio', '--weights', '--start-date',
        '--end-date', '--days', '--initial-capital', '--no-persist', '--dry-run',
        '--v31', '--v33', '--v34', '--v35', '--v36', '--v37', '--v38',
    }
    assert required_options <= set(build_arg_parser().format_help().split())
    wrapper = Path('4_run_backtest.py').read_text(encoding='utf-8')
    assert "_CANONICAL_MODULE = 'jobs.run_backtest'" in wrapper
    assert "import_module(_CANONICAL_MODULE).main()" in wrapper
