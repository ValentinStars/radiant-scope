"""Build a self-contained Python zip application, plus SHA256 checksum."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import zipapp

root = Path(__file__).resolve().parents[1]
out = root / 'dist'
out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    stage = Path(tmp)
    shutil.copytree(root / 'radiant_scope', stage / 'radiant_scope', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy(root / 'LICENSE', stage / 'LICENSE')
    artifact = out / 'radiant-scope-1.0.0.pyz'
    zipapp.create_archive(stage, artifact, interpreter='/usr/bin/env python3', main='radiant_scope.server:main', compressed=True)
    artifact.chmod(0o755)
    artifacts = sorted(p for p in out.iterdir() if p.name.endswith(('.pyz', '.whl', '.tar.gz')))
    (out / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in artifacts))
    print(artifact)
