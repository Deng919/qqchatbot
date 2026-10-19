import re
import subprocess
from pathlib import Path
from html.parser import HTMLParser

from tests.test_web import web_client


def test_inbox_home_has_real_list_and_detail_regions(web_client):
    client, _, _ = web_client
    client.post('/login', data={'password': 'password123'})
    page = client.get('/reports').text
    assert 'class="workspace-app workspace-reading"' in page
    assert 'id="inbox-workspace"' in page
    assert 'id="inbox-group-list"' in page
    assert 'id="inbox-point-detail"' in page
    assert 'id="inbox-back"' in page
    assert '筛选本页讨论' in page
    assert 'id="summary-points"' in page
    assert 'id="range-submit-btn"' in page
    assert 'id="report-revisions"' in page
    assert '示例' not in re.search(r'<main.*?</main>', page, re.S).group(0)
    class OverlayPlacement(HTMLParser):
        def __init__(self):
            super().__init__()
            self.divs = []
            self.overlays = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'div':
                self.divs.append(attributes.get('id'))
            if attributes.get('id') in ('summary-source-panel', 'summary-source-backdrop'):
                assert 'inbox-workspace' not in self.divs, 'source overlays must survive mobile list hiding'
                self.overlays.append(attributes['id'])

        def handle_endtag(self, tag):
            if tag == 'div' and self.divs:
                self.divs.pop()

    placement = OverlayPlacement()
    placement.feed(page)
    assert len(placement.overlays) == 2


def test_inbox_templates_are_packaged():
    from scripts.build_desktop import TEMPLATES
    for name in ['inbox_workspace_style.html', 'inbox_reading_script.html']:
        assert name in TEMPLATES


def test_inbox_behavior_with_existing_script_harness():
    result = subprocess.run(['node', str(Path(__file__).with_name('inbox_reading.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
