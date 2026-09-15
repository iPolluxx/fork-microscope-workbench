"""Keep every checked-in page on the same static, no-JavaScript navigation.

Run this after changing the shell. --check is used by builds and tests to reject
page drift instead of shipping different navigation on different routes.
"""
from pathlib import Path
import argparse
import re

ROOT = Path(__file__).resolve().parents[1]
PAGES = {'workspace.html': 'Workspace', 'live.html': 'Configure',
         'observatory.html': 'Explore', 'compare.html': 'Compare',
         'guide.html': 'Guide', 'released-data.html': 'Explore'}
LINKS = [('Workspace', '/workspace.html'), ('Configure', '/live.html'),
         ('Explore', '/observatory.html'), ('Compare', '/compare.html'),
         ('Guide', '/guide.html')]
PATTERN = re.compile(r'<!-- APP_NAVIGATION_START -->.*?<!-- APP_NAVIGATION_END -->', re.S)


def navigation(page):
    links = []
    for label, href in LINKS:
        current = ' aria-current="page"' if PAGES[page] == label else ''
        hook = ' id="observatory-link"' if page == 'live.html' and label == 'Explore' else ''
        links.append(f'<a href="{href}"{current}{hook}>{label}</a>')
    return ('<!-- APP_NAVIGATION_START -->\n'
            '<header class="app-shell"><div class="app-shell-inner">'
            '<a class="app-brand" href="/workspace.html" aria-label="Fork microscope · Workspace">'
            '<span class="app-mark" aria-hidden="true">✳</span>'
            '<span>Fork microscope<small>Research workbench</small></span></a>'
            '<nav class="app-nav" aria-label="Main navigation">' + ''.join(links) + '</nav>'
            '</div></header>\n<!-- APP_NAVIGATION_END -->')


def sync(check=False):
    stale = []
    for name in PAGES:
        path = ROOT / 'public' / 'fork-microscope' / name
        source = path.read_text()
        if len(PATTERN.findall(source)) != 1:
            raise ValueError(f'{name}: expected one navigation placeholder')
        expected = PATTERN.sub(lambda _: navigation(name), source)
        if source != expected:
            stale.append(name)
            if not check: path.write_text(expected)
    if check and stale:
        raise ValueError('Navigation is out of sync; run python3 scripts/sync_navigation.py: ' + ', '.join(stale))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    sync(parser.parse_args().check)
