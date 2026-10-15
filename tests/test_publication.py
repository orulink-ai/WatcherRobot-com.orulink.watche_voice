"""Keep private workstation evidence out of marketplace source snapshots."""

from pathlib import Path
import shutil

from watcherobot.distribution.source_files import collect_application_source_files


ROOT = Path(__file__).resolve().parents[1]


def test_marketplace_excludes_private_files_but_keeps_runtime(tmp_path):
    ignore = ROOT / '.watcherignore'
    if ignore.exists():
        shutil.copyfile(ignore, tmp_path / ignore.name)
    private = [
        '.local/session.json', '.local/evidence.png', '.local/release.py',
        'node_modules/tool/index.js', 'run.log', 'current.url',
        'documents/dev_log/task/evidence.jpg',
        'documents/dev_log/task/evidence.png', '.env', '.env.production',
        'documents/dev_log/date/task/evidence.jpg',
        'documents/dev_log/date/task/extra/evidence.png',
    ]
    public = ['app.json', 'app.py', 'web/app.js', 'web/icon.svg',
              'README.md', 'documents/dev_log/task/index.md']
    for relative in private + public:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture', encoding='utf-8')
    selected = {path.as_posix() for path in collect_application_source_files(tmp_path)}
    assert not selected.intersection(private)
    assert selected == set(public)
