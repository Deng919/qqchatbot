import re

import pytest

from tests.test_web import web_client


@pytest.mark.parametrize('path', ['/reports','/search','/groups','/secondhand','/bookmarks','/bookmarks?view=knowledge','/settings','/overview','/login'])
def test_theme_initializes_before_paint_and_controls_live_only_in_settings(web_client,path):
    client,_,_=web_client
    if path!='/login':client.post('/login',data={'password':'password123'})
    page=client.get(path)
    assert page.status_code==200
    assert page.text.index('id="appearance-init"')<page.text.index('<body')
    assert page.text.count('name="theme-mode"')==(3 if path=='/settings' else 0)
    assert '<select id="theme-mode"' not in page.text
    if path=='/settings':
        assert 'data-settings-panel="appearance"' in page.text
        assert 'href="/settings?section=appearance"' in page.text
        assert 'data-theme-choice' not in page.text.split('<header class="global-header">')[1].split('</header>')[0]
    assert 'text-decoration: underline' not in page.text and 'text-decoration:underline' not in page.text


def test_desktop_package_carries_both_shared_appearance_templates():
    from scripts.build_desktop import TEMPLATES
    assert {'appearance.html','appearance_control.html'}<=set(TEMPLATES)


def test_appearance_category_is_available_without_desktop_or_optional_features(web_client):
    client,_,_=web_client
    client.post('/login',data={'password':'password123'})
    page=client.get('/settings?section=appearance')
    assert page.status_code==200
    assert re.search(r'data-settings-section="appearance"\s+aria-current="page"',page.text)
    for value in ('light','dark','system'):
        assert f'type="radio" name="theme-mode" value="{value}" data-theme-choice' in page.text
