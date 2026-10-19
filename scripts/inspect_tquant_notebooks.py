"""Print source selection code for the TQuant field audit (never execute it)."""
import ast
import json
import sys
from pathlib import Path

for path in Path('Strategy_example').glob('*.ipynb'):
    if sys.argv[1] not in path.name:
        continue
    print('\n###', path.name)
    for cell in json.loads(path.read_text(encoding='utf-8'))['cells']:
        if cell['cell_type'] != 'code':
            continue
        source = ''.join(cell['source'])
        if '--all' in sys.argv:
            print(source)
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if '--assignments' in sys.argv:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Subscript) for t in node.targets):
                    print(ast.get_source_segment(source, node))
                continue
            if '--audit' in sys.argv:
                if isinstance(node, ast.FunctionDef) and node.name in ('compute_stock', 'compute_signals', 'select'):
                    print(ast.get_source_segment(source, node))
                continue
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if node.name in ('make_pipeline', 'handle_data', 'rebalance', 'compute_stock',
                                 'before_trading_start', 'initialize', 'daily_trade',
                                 'universe_filters', 'compute_signals', 'select') or isinstance(node, ast.ClassDef):
                    print(ast.get_source_segment(source, node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                segment = ast.get_source_segment(source, node)
                if any(s in segment for s in ('window', 'WINDOW', 'MA_', 'MOM_', 'TOP_', 'VOL_', 'LIQUIDITY',
                                              'signal', 'mad', 'MAD', 'quantile', 'screen', 'filter', 'set_',
                                              'fast_ma', 'slow_ma', 'dip_buy')):
                    print(segment)
