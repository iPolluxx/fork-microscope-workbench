"""Build only publishable static assets and notices. Reject stale or symlinked output."""
from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ASSETS=('workspace.html workspace.mjs workspace.css live.html live.js live.css '
        'observatory.html observatory.mjs observatory.css observatory-base.css '
        'compare.html compare.mjs compare.css worker-connection.js method-credit.js refinement-panel.mjs '
        'refinement-panel.css investigation-panel.mjs investigation-panel.css lens-panel.mjs lens-panel.css patching-panel.mjs patching-panel.css journey.css walkthrough.mjs evidence-import.mjs graph-evidence.mjs passes.mjs '
        'math.mjs plotly.min.js app.js styles.css job-progress.mjs response-review.mjs download.mjs').split()
ASSETS = [*ASSETS, 'compute-readiness.mjs', 'workflow-panel.mjs', 'guide.html', 'guide.css', 'guide.mjs', 'app-navigation.css']

NOTICES={'LICENSE':ROOT/'LICENSE','THIRD-PARTY.md':ROOT/'THIRD-PARTY.md',
         'plotly-LICENSE.txt':ROOT/'licenses'/'plotly-MIT.txt'}

def build(destination=None):
    # Static markup must match the shared shell before either host receives it.
    try:
        from .sync_navigation import sync
    except ImportError:
        from sync_navigation import sync
    sync(check=True)
    source=ROOT/'public'/'fork-microscope';target=Path(destination or ROOT/'dist'/'dashboard')
    files=ASSETS+['index.html','released-data.html',*NOTICES]
    allowed=set(files+['build-manifest.json'])
    if target.is_symlink():raise ValueError('Refusing a symlinked output directory.')
    if target.exists():
        extras=[p.name for p in target.iterdir() if p.name not in allowed or p.is_symlink() or not p.is_file()]
        if extras:raise ValueError('Output contains unexpected files. Choose an empty output directory before publishing: '+', '.join(extras))
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dashboard-build-',dir=target.parent) as staging:
        stage=Path(staging)
        for name in ASSETS:shutil.copy2(source/name,stage/name)
        shutil.copy2(source/'released-data.html',stage/'released-data.html')
        shutil.copy2(source/'workspace.html',stage/'index.html')
        for name,path in NOTICES.items():shutil.copy2(path,stage/name)
        notice=stage/'THIRD-PARTY.md'
        notice.write_text(notice.read_text().replace('licenses/plotly-MIT.txt','plotly-LICENSE.txt'))
        manifest={name:hashlib.sha256((stage/name).read_bytes()).hexdigest() for name in files}
        (stage/'build-manifest.json').write_text(json.dumps(manifest,indent=2))
        zip_path=stage/'archive.zip'
        with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in files+['build-manifest.json']:archive.write(stage/name,name)
        target.mkdir(exist_ok=True)
        for name in files+['build-manifest.json']:shutil.copy2(stage/name,target/name)
        zip_path.replace(target.parent/'fork-dashboard.zip')
    return target

if __name__=='__main__':print(build())
