"""Secret-filtered split ZIPs, member hashes, isolated extraction and restored import."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

from scripts.system_acceptance import ROOT, OUT, digest, read, write, now

BACKUP = OUT / 'backup'
EXCLUDED_PARTS = {'.git', 'myenv', '__pycache__', '.pytest_cache', '.tokensave', '.serena', 'node_modules',
                  '.claude', '.codex', '.agents', '.amazonq', '.agent', '.vscode'}


def secret_values():
    from dotenv import dotenv_values
    sources = [dict(os.environ)]
    for path in ROOT.glob('.env*'):
        if path.is_file() and 'example' not in path.name and 'sample' not in path.name:
            sources.append(dotenv_values(path))
    return list({str(v).encode() for values in sources for k, v in values.items() if v and k != 'HF_HUB_DISABLE_IMPLICIT_TOKEN'
                 and re.search(r'PASS(?:WORD)?|SECRET|TOKEN|(?:^|_)KEY(?:$|_)|DB_URL|DATABASE_URL|DSN', k, re.I)})


def has_secret(data, secrets):
    # Short low-entropy values can also be ordinary identifiers or market numbers.
    # Treat them as credentials in assignments/URLs; scan longer values verbatim.
    for value in secrets:
        if len(value) >= 8:
            if value in data:
                return True
        elif (b':' + value + b'@' in data or re.search(
                rb"(?i)(?:password|passwd|secret|token|api[_-]?key)[^\r\n]{0,40}[=:]\s*[\"']?"
                + re.escape(value) + rb"(?:[\"'\s,;}]|$)", data)):
            return True
    return b'-----BEGIN ' + b'PRIVATE KEY-----' in data


def contains_secret(path, secrets):
    overlap = max([512, *(len(s) for s in secrets)])
    tail = b''
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            chunk = tail + chunk
            if has_secret(chunk, secrets):
                return True
            tail = chunk[-overlap:] if overlap else b''
    return False


def allowed(path, secrets):
    parts = path.relative_to(ROOT).parts
    if (set(parts) & EXCLUDED_PARTS or path.is_symlink() or path.suffix.lower() in ('.pem', '.key', '.pfx', '.p12', '.sqlite', '.db')
            or (path.name.startswith('.env') and not any(x in path.name for x in ('example', 'sample', 'template')))
            or (not path.name.startswith('test_') and re.search(r'owner_grants|credentials|private_key', path.name, re.I))):
        return False, 'sensitive_or_local_path'
    if path.suffix.lower() == '.zip':
        return False, 'unreviewed_archive_use_verified_evidence_package'
    if contains_secret(path, secrets):
        return False, 'secret_value_detected'
    return True, None


def package(name, paths, secrets, excluded):
    paths = sorted({p.resolve() for p in paths if p.is_file()})
    members, selected = {}, {}
    archive = BACKUP / f'{name}.zip'
    for path in paths:
        accepted, reason = allowed(path, secrets)
        rel = path.relative_to(ROOT).as_posix()
        if not accepted:
            excluded[rel] = reason
            continue
        members[rel] = digest(path)
        selected[rel] = path
    manifest_path = BACKUP / f'{name}.manifest.json'
    if archive.is_file() and manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        if previous['members'] == members and digest(archive) == previous['sha256']:
            return previous  # full current scan/hash above; reuse identical archive, still restore below
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for rel, path in selected.items():
            bundle.write(path, rel)
    manifest = {'created_at': now(), 'archive': archive.name, 'sha256': digest(archive), 'members': members, 'files': len(members)}
    write(f'backup/{name}.manifest.json', manifest)
    archive.with_suffix('.zip.sha256').write_text(f"{manifest['sha256']}  {archive.name}\n", encoding='ascii')
    return manifest


def restore(manifests):
    import hashlib
    restored = {}
    with tempfile.TemporaryDirectory(prefix='linbot-final-restore-') as temp:
        target = Path(temp)
        for manifest in manifests:
            archive = BACKUP / manifest['archive']
            assert digest(archive) == manifest['sha256']
            with zipfile.ZipFile(archive) as bundle:
                assert len(bundle.namelist()) == manifest['files']
                assert set(bundle.namelist()) == set(manifest['members'])
                for name, sha in manifest['members'].items():
                    destination = (target / name).resolve()
                    assert destination.is_relative_to(target.resolve()), 'unsafe ZIP member'
                    with bundle.open(name) as stream:
                        assert hashlib.file_digest(stream, 'sha256').hexdigest() == sha
                    bundle.extract(name, target)
                    assert digest(destination) == sha
                    if name in restored:
                        assert restored[name] == sha, 'conflicting packages'
                    restored[name] = sha
        specs = list((target / 'openspec/changes').glob('**/*system-operations-final-acceptance/**/*.md'))  # live or archived
        assert len(specs) >= 4 and all(p.read_text(encoding='utf-8') for p in specs)
        code = "import sys;sys.path.insert(0,sys.argv[1]);from jobs import scheduler,run_research_platform;from scripts import task_controller;from services import web_dashboard;from pathlib import Path;assert all(Path(m.__file__).is_relative_to(Path(sys.argv[1])) for m in (scheduler,run_research_platform,task_controller,web_dashboard));print('RESTORED_IMPORT_PASS')"
        proc = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', code, str(target)], cwd=target,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, 'Restored import failed: ' + proc.stderr
        return {'status': 'PASS', 'at': now(), 'packages': len(manifests), 'unique_files': len(restored),
                'readback': 'ALL_MEMBERS_SHA256_MATCH', 'extraction': 'ALL_RESTORED_FILES_SHA256_MATCH',
                'openspec_readable': len(specs), 'import': proc.stdout.strip(), 'isolated_staging': 'TemporaryDirectory, removed after verification',
                'environment': 'current installed dependencies; cold-machine dependency installation not tested',
                'storage': 'SAME_DISK_ONLY; OFFSITE_OFFLINE_NOT_VERIFIED'}


def main():
    BACKUP.mkdir(parents=True, exist_ok=True)
    secrets, excluded = secret_values(), {}
    listed = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    source = [ROOT / p for p in set(listed) if not p.startswith(('outputs/', 'outputs\\', 'openspec/', 'openspec\\'))]
    # Ignored implementation assets are included explicitly as well.
    for folder in ('core', 'app', 'services', 'scripts', 'jobs', 'templates', 'static', 'config', 'test', 'execution'):
        source.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and not set(p.parts) & EXCLUDED_PARTS)
    research = [ROOT / p for p in read('baseline.json')['protected'] if p.startswith('outputs/')]
    old = json.loads((ROOT / 'outputs/strategy_v4/acceptance/package_verification.json').read_text(encoding='utf-8'))
    old_path = Path(old['archive'])
    assert digest(old_path) == old['sha256'], 'Existing V4 package changed'
    # Preserve frozen historical sources separately when current source has changed.
    portable = BACKUP / 'v4_snapshot_evidence.zip'
    entries, relocated = {}, []
    with zipfile.ZipFile(old_path) as original, zipfile.ZipFile(portable, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for info in original.infolist():
            if info.is_dir():
                continue
            name = info.filename
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            assert not any(part.startswith('.env') or part in EXCLUDED_PARTS for part in Path(name).parts)
            data = original.read(name)
            assert not has_secret(data, secrets), 'Secret in existing evidence archive'
            sha = __import__('hashlib').sha256(data).hexdigest()
            if (ROOT / name).is_file() and digest(ROOT / name) != sha:
                relocated.append(name)
                name = 'historical_v4_package/' + name
            entries[name] = sha
            bundle.writestr(name, data)
    snapshot = {'created_at': now(), 'archive': portable.name, 'sha256': digest(portable), 'members': entries, 'files': len(entries),
                'original_archive_sha256': old['sha256'], 'historical_source_relocated': relocated}
    write('backup/v4_snapshot_evidence.manifest.json', snapshot)
    portable.with_suffix('.zip.sha256').write_text(snapshot['sha256'] + '  ' + portable.name + '\n', encoding='ascii')
    manifests = [package('source_config', source, secrets, excluded),
                 package('research_evidence', research, secrets, excluded), snapshot]
    docs = list((ROOT / 'openspec').rglob('*')) + [p for p in OUT.rglob('*') if p.is_file()
            and 'backup' not in p.relative_to(OUT).parts and 'prechange' not in p.relative_to(OUT).parts
            and not p.name.startswith(('web-restart', 'preview'))]
    manifests.append(package('openspec_reports', docs, secrets, excluded))
    write('backup/exclusions.json', excluded)
    proof = restore(manifests)
    proof['excluded_files'] = len(excluded)
    proof['archives'] = [{k: m[k] for k in ('archive', 'sha256', 'files')} for m in manifests]
    write('backup/restore_verification.json', proof)
    instructions = """# Restore (2026-11-15)

Verify each ZIP against its .sha256 and .manifest.json. Extract source_config.zip, research_evidence.zip,
v4_snapshot_evidence.zip and openspec_reports.zip into ONE NEW EMPTY DIRECTORY, preserving member paths.
Do not overwrite a live checkout. Duplicate members must have identical manifest hashes.
Rebuild a Python 3.11 environment using requirements files (installed versions also recorded in dependencies.txt).
Supply secrets via environment or an operator-managed .env; no live secrets or complete MySQL dump is included.
Inspect backup/exclusions.json; excluded sensitive/local files are never silently represented as backed up.
Run isolated imports and pytest before activation. OS task settings are audit/templates only: no task is auto-registered.
Pinned external snapshot inputs named by snapshot manifests may be needed for full cold-engine replay.
No production activation, deployment, formal notifications or Windows boot test is implied.
This backup is on the SAME DISK. Copy packages/manifests to an authorized offline/offsite destination for disaster recovery.
"""
    (BACKUP / 'RESTORE.md').write_text(instructions, encoding='utf-8')
    print(json.dumps(proof))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
