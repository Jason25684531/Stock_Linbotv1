"""Read-only independent review of change 2026-11-10-historical-data-foundation-v1 by Codex (design D12 harness rules: read-only sandbox,
repository fingerprint compared before and after, because the Windows containment canary did not pass).

    python -m scripts.historical_review diff      # writes outputs/historical_foundation/review/change.diff (tracked edits + new files)
    python -m scripts.historical_review codex     # Codex read-only review of that diff -> review_codex.md
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/historical_foundation/review'
CHALLENGES = """Challenge specifically, with file:line evidence and a reproducible check for each finding:
1. Can any factor, label or status use information published after the date it is used (future announcements, regime labels, warm-up)?
2. Are delisted stocks missing from the historical universe (survivorship), or is a later listing snapshot used for an earlier date?
3. Are prices and corporate actions handled correctly (raw vs adjusted, event factors, jumps, missing sessions)?
4. Can missing data be mistaken for 'the strategy had no signal' (a backtestable year whose required data is absent)?
5. Is any historical period called out-of-sample, or are results ranked or selectively shown?
6. Can the change rewrite old research evidence (existing reference runs, ledger, registry, H4, raw files)?
7. Can any 2026 or sealed H4 information leak into the research data or runs?
8. Can an unauthorized user start research or data updates from the web, or can the web bypass the research budget?
Also check that VERIFIED can never come from file existence alone and that NOT_REQUIRED never hides price, universe or corporate-action evidence."""


def diff() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    git = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    parts = [git('diff')]
    for path in git('ls-files', '--others', '--exclude-standard').splitlines():
        parts.append(subprocess.run(['git', 'diff', '--no-index', '--', '/dev/null', path], cwd=ROOT, capture_output=True, text=True,
                                    encoding='utf-8').stdout)
    target = OUT / 'change.diff'
    target.write_text(''.join(parts), encoding='utf-8')
    print(json.dumps({'diff': str(target), 'bytes': target.stat().st_size}))
    return target


def codex(target=None) -> dict:
    from scripts import dual_cli_harness as harness
    target = Path(target or OUT / 'change.diff')
    prompt = (f'You are an independent read-only reviewer. Review the unified diff at {target.as_posix()} for the Stock_Linbotv1 repository at '
              f'{ROOT.as_posix()}; read any repository file you need for context (openspec/changes/2026-11-10-historical-data-foundation-v1/design.md '
              'states the intended design). Do not modify any file and do not run network or database commands. First line: "VERDICT: PASS" or '
              '"VERDICT: BLOCK". Then BLOCKING, MAJOR and MINOR findings.\n' + CHALLENGES)
    before = harness.repo_fingerprint()
    result = subprocess.run(harness._exe(harness.codex_argv(ROOT, 'read-only', OUT / 'review_codex.md')), input=prompt, text=True,
                            encoding='utf-8', capture_output=True, cwd=ROOT, timeout=3600)
    record = {'exit': result.returncode, 'changed_repo': harness.repo_fingerprint() != before, 'report': str(OUT / 'review_codex.md'),
              'stderr_tail': (result.stderr or '')[-800:]}
    (OUT / 'review_codex.json').write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: record[k] for k in ('exit', 'changed_repo', 'report')}))
    return record


if __name__ == '__main__':
    {'diff': diff, 'codex': codex}[sys.argv[1]]()
