"""Thin saved-evidence render/data tools and a self-contained MCP Apps resource."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
import re
from typing import Annotated

from tools.runtime import FastMCP
from tools.viewer_data import get_investigation_view

UI = Path(__file__).resolve().parents[2] / 'ui'
MIME = 'text/html;profile=mcp-app'
READ_ONLY = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
RESOURCE_META = {
    'ui': {'prefersBorder': True, 'csp': {'connectDomains': [], 'resourceDomains': [], 'frameDomains': []}},
    'openai/ui': {'availableDisplayModes': ['inline', 'fullscreen']},
    'openai/widgetDescription': 'Explore sampled saved outcomes, exact continuation pages, provenance and verified original-byte export.',
}


def build_viewer_html() -> str:
    """Bundle only our fixed local source allowlist; never inject saved evidence."""
    html = (UI / 'viewer.html').read_text()
    css = (UI / 'viewer.css').read_text()
    bridge = (UI / 'bridge.mjs').read_text()
    measurements = (UI / 'measurements.mjs').read_text()
    viewer = (UI / 'viewer.mjs').read_text()
    viewer = re.sub(r"^import\s+\{createBridge\}\s+from\s+['\"]\./bridge\.mjs['\"];?\s*$", '', viewer, flags=re.M)
    # Permit formatting whitespace around the single approved bridge import.
    viewer = re.sub(r"^import\s+\{\s*createBridge\s*\}\s+from\s+['\"]\./bridge\.mjs['\"];?\s*$", '', viewer, flags=re.M)
    # This one additional fixed module contains saved-distribution validation.
    # Any other dependency still fails closed below; no path comes from evidence.
    viewer = re.sub(r"^import\s+\{[A-Za-z_\s,]+\}\s+from\s+['\"]\./measurements\.mjs['\"];?\s*$", '', viewer, flags=re.M)
    js = re.sub(r'^export\s+(?=(?:async\s+)?function\b|const\b|class\b)', '', bridge + '\n' + measurements + '\n' + viewer, flags=re.M)
    if re.search(r'^\s*(?:import|export)\b', js, flags=re.M):
        raise ValueError('Viewer contains an unbundled module dependency.')
    if '</style' in css.lower():
        raise ValueError('Unexpected closing style tag in viewer stylesheet.')
    html, styles = re.subn(r'<link\s+rel="stylesheet"\s+href="(?:\./)?viewer\.css"\s*/?>', lambda _: '<style>' + css + '</style>', html)
    js = js.replace('</script', '<\\/script')
    html, scripts = re.subn(r'<script\s+type="module"\s+src="(?:\./)?viewer\.mjs"\s*>\s*</script>', lambda _: '<script type="module">' + js + '</script>', html)
    if styles != 1 or scripts != 1:
        raise ValueError('Viewer entrypoint must contain exactly one approved stylesheet and script.')
    return html


VIEWER_HTML = build_viewer_html()
RESOURCE_URI = 'ui://fork-microscope/saved-evidence-' + hashlib.sha256(VIEWER_HTML.encode()).hexdigest()[:16] + '.html'
viewer_mcp = FastMCP(name='saved-evidence-viewer')


@viewer_mcp.resource(RESOURCE_URI, name='fork-microscope-saved-evidence', mime_type=MIME,
    description='Graph-led saved-evidence viewer. No model execution or external network access.',
    meta=deepcopy(RESOURCE_META))
def saved_evidence_resource() -> str:
    return VIEWER_HTML


def _pin(expected_file_sha256: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{64}', expected_file_sha256):
        raise ValueError('Supply the exact lowercase file SHA-256 returned by list_investigations.')


@viewer_mcp.tool(annotations=READ_ONLY, meta={'ui': {'resourceUri': RESOURCE_URI, 'visibility': ['model']}})
def fork_microscope_render_investigation(
    bundle_id: Annotated[str, 'Saved bundle identifier returned by list_investigations.'],
    expected_file_sha256: Annotated[str, 'Exact file SHA-256 from listing or inspection.'],
    run_index: int = 0, pass_index: int = 0, token_position: int | None = None,
) -> dict:
    """Show a graph-led saved investigation after listing/inspection has identified its bundle and file hash.

    Renders a compact card with expandable outcome points, actual contrasting text,
    provenance and original-byte export. No inference. Clients without UI receive
    the same bounded structured evidence; use inspection for omitted text/details.
    """
    _pin(expected_file_sha256)
    return get_investigation_view(bundle_id, run_index, pass_index, token_position, 0, 5, expected_file_sha256)


@viewer_mcp.tool(annotations=READ_ONLY, meta={'ui': {'visibility': ['model', 'app']}, 'openai/widgetAccessible': True})
def fork_microscope_get_investigation_view(
    bundle_id: str, expected_file_sha256: str,
    run_index: int = 0, pass_index: int = 0, token_position: int | None = None,
    offset: int = 0, limit: int = 5,
) -> dict:
    """Read a hash-pinned saved-evidence checkpoint/page without remounting the viewer or running a model.

    Returned pointers and truncation flags preserve access to full stored values.
    Requires a file hash; changed source data is rejected rather than mixed.
    """
    _pin(expected_file_sha256)
    return get_investigation_view(bundle_id, run_index, pass_index, token_position, offset, limit, expected_file_sha256)
