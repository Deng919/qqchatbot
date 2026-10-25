import pytest

from tests.test_web import web_client


@pytest.mark.parametrize('path', ['/reports','/search','/groups','/secondhand','/bookmarks','/bookmarks?view=knowledge','/settings','/overview','/login'])
def test_theme_is_initialized_before_paint_and_has_one_control(web_client,path):
    client,_,_=web_client
    if path!='/login':client.post('/login',data={'password':'password123'})
    page=client.get(path)
    assert page.status_code==200
    assert page.text.index('id="appearance-init"')<page.text.index('<body')
    assert page.text.count('<select id="theme-mode"')==1
    assert 'value="system"' in page.text and 'value="dark"' in page.text
    assert 'text-decoration: underline' not in page.text and 'text-decoration:underline' not in page.text


def test_desktop_package_carries_both_shared_appearance_templates():
    from scripts.build_desktop import TEMPLATES
    assert {'appearance.html','appearance_control.html'}<=set(TEMPLATES)
