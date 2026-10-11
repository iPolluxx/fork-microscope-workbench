"""Package only approved code/static resources; never ship keys or evidence.

Does not build/push an image, contact GCP, install dependencies or deploy.
"""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
MODULES=('__init__.py','evidence_io.py','investigation_bundle.py','investigation_records.py',
         'investigation_catalog.py','sampling.py','outcome_readout.py','workflow_cli.py')
MCP_MODULES=('pilot_cloud.py','pilot_oauth.py','pilot_evidence.py','pilot_budget.py',
             'hosted_pilot.py','fork_microscope_hosted_mcp.py')
TOOLS=('hosted.py','hosted_evidence.py','evidence.py','viewer.py','viewer_data.py','profile.py','runtime.py')
UI=('compute.html','compute.css','compute-bridge.mjs','compute.mjs',
    'viewer.html','viewer.css','bridge.mjs','measurements.mjs','viewer.mjs')

def prepare(destination):
    target=Path(destination)
    if target.exists():
        raise ValueError('Use a new empty pilot build destination')
    target.mkdir(parents=True)
    files={
        'Dockerfile':ROOT/'deploy/hosted/Dockerfile.pilot',
        'requirements.txt':ROOT/'requirements/hosted-pilot.txt',
        **{'fork_microscope/'+n:ROOT/'src/fork_microscope'/n for n in MODULES},
        **{'fork_microscope/hosted/'+p.name:p for p in (ROOT/'src/fork_microscope/hosted').glob('*.py')},
        **{'mcp/src/'+n:ROOT/'integrations/mcp/src'/n for n in MCP_MODULES},
        **{'mcp/src/tools/'+n:ROOT/'integrations/mcp/src/tools'/n for n in TOOLS},
        **{'mcp/ui/'+n:ROOT/'integrations/mcp/ui'/n for n in UI},
    }
    manifest={}
    for name,source in files.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError('Missing or unsafe build source: '+name)
        out=target/name;out.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,out)
        manifest[name]=hashlib.sha256(out.read_bytes()).hexdigest()
    (target/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (target/'.gcloudignore').write_text('**/__pycache__/\n**/*.pyc\n.git/\n.env\n')
    return target

if __name__=='__main__':
    import sys
    print(prepare(sys.argv[1]))
