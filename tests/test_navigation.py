# generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — demo entry and shared area routes.
"""Public routes must retain the same cross-page navigation, even without JS."""
from html.parser import HTMLParser
from pathlib import Path

from scripts.sync_navigation import LINKS, PAGES, sync
from scripts.build_dashboard import build


class ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.links = []
        self.brand = None
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'nav' and attrs.get('aria-label') == 'Main navigation':
            self.inside = True
        if tag == 'a' and attrs.get('class') == 'app-brand':
            self.brand = attrs['href']
        if tag == 'a' and self.inside:
            self.current = [attrs, '']
            self.links.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a': self.current = None
        if tag == 'nav': self.inside = False


def test_source_and_built_routes_share_navigation(tmp_path):
    sync(check=True)
    target = build(tmp_path/'dashboard')
    source = Path(__file__).parents[1]/'public/fork-microscope'
    for name, current in PAGES.items():
        built = name
        for page in (source/name, target/built):
            parser = ShellParser();parser.feed(page.read_text())
            assert [(text, attrs['href']) for attrs, text in parser.links] == LINKS
            assert [text for attrs, text in parser.links if attrs.get('aria-current') == 'page'] == [current]
            assert parser.brand == '/workspace.html'
    assert (target/'app-navigation.css').is_file()
    assert 'observatory.html?demo=attendance' in (target/'index.html').read_text()
    assert (target/'demo-attendance.json').is_file()
