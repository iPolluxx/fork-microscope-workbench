"""Offline readiness inventory; no worker requests and no credential contents read."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent

def tool_inventory():
    tools = []
    for path in sorted((ROOT / 'tools').glob('*.py')):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == 'tool':
                        name = next((kw.value.value for kw in decorator.keywords if kw.arg == 'name' and isinstance(kw.value, ast.Constant)), node.name)
                        tools.append({'name': name, 'description': ast.get_docstring(node) or '',
                                      'parameters': [a.arg for a in node.args.args]})
    return tools

def readiness():
    dependencies = {name: importlib.util.find_spec(name) is not None for name in ('fastmcp', 'fork_microscope')}
    profile = {'valid': False, 'worker_configured': False}
    try:
        from tools.profile import load_profile
        config = load_profile()
        profile.update(valid=True, worker_configured=bool(config['worker_url']))
    except Exception:
        profile['reason'] = 'Profile missing or invalid; use configure.py and check private paths locally.'
    return {
        'mode': 'offline_readiness_inventory', 'transport': 'stdio',
        'dependencies_present': dependencies, 'profile': profile,
        'ready_to_attempt_stdio': all(dependencies.values()) and profile['valid'],
        'protocol_verified': False, 'chatgpt_connected': False,
        'source': str(importlib.util.find_spec('fork_microscope').origin) if dependencies['fork_microscope'] else None,
        'tools': tool_inventory(),
        'gates': ['Real FastMCP protocol test required; discovery is source inspection only.',
                  'Public hosted OAuth and tenant authentication are not implemented; private ChatGPT tunnel acceptance is recorded separately in CHATGPT-VALIDATION.md.',
                  'Evidence URIs are opaque source identities, not hosted download links.'],
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Exit nonzero unless ready to attempt stdio.')
    args = parser.parse_args()
    report = readiness()
    print(json.dumps(report, indent=2))
    return 0 if not args.check or report['ready_to_attempt_stdio'] else 1

if __name__ == '__main__':
    sys.exit(main())
