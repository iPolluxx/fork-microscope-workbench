"""Tests-only direct adapter harness. NOT an MCP client or protocol server."""
from contextlib import contextmanager
import sys
import types

class AdapterRegistry:
    def __init__(self, name=None, **kwargs):
        self.name = name
        self.tools = {}
        self.resources = {}
    def tool(self, function=None, **kwargs):
        def register(fn):
            self.tools[kwargs.get('name', fn.__name__)] = fn
            return fn
        return register(function) if function else register
    def mount(self, other, **kwargs):
        self.tools.update(other.tools)
        self.resources.update(other.resources)
    def resource(self, uri, **kwargs):
        def register(fn):
            self.resources[uri] = fn
            return fn
        return register
    def run(self, **kwargs):
        raise RuntimeError('Tests-only adapter registry cannot serve MCP.')

@contextmanager
def adapter_context():
    """Import fresh adapters with direct functions; restore modules on exit."""
    names = ['tools.runtime', 'tools.evidence', 'tools.workflow', 'tools.viewer_data', 'tools.viewer', 'fork_microscope_workbench_mcp']
    previous = {name: sys.modules.get(name) for name in names}
    package = sys.modules.get('tools')
    attributes = {name: getattr(package, name, None) for name in ('runtime', 'evidence', 'workflow', 'viewer_data', 'viewer')}
    if package:
        for name in attributes:
            if hasattr(package, name):
                delattr(package, name)
    runtime = types.ModuleType('tools.runtime')
    runtime.FastMCP = AdapterRegistry
    for name in names:
        sys.modules.pop(name, None)
    sys.modules['tools.runtime'] = runtime
    try:
        yield
    finally:
        for name in names:
            sys.modules.pop(name, None)
            if previous[name] is not None:
                sys.modules[name] = previous[name]

        package = sys.modules.get('tools')
        if package:
            for name, value in attributes.items():
                if value is None:
                    if hasattr(package, name):
                        delattr(package, name)
                else:
                    setattr(package, name, value)
