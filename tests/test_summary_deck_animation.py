"""Exercise the production deck with more than six visible notes in Chromium."""
import json
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize('width', [1800, 2200, 390])
def test_every_visible_note_animates_on_open_and_close(width):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright

    templates = Path(__file__).resolve().parents[1] / 'qq_digest/web/templates'
    style = (templates / 'inbox_workspace_style.html').read_text(encoding='utf-8')
    motion = (templates / 'summary_deck_motion.html').read_text(encoding='utf-8')
    fixture = '''<style>:root{--surface:#252525;--border:#444;--text:#eee;--text-soft:#aaa}body{margin:12px;background:#202020;color:#eee}[hidden]{display:none!important}</style>'''+style+'''
    <div class="summary-dashboard-grid" id="grid"><details class="summary-group-stack"><summary class="summary-group-cover">Test group</summary><div class="summary-group-content"><div class="summary-group-points"></div></div></details></div>
    <script>
    function summaryNode(tag,cls){var node=document.createElement(tag);if(cls)node.className=cls;return node;}
    function summaryButton(text,click,cls){var button=summaryNode('button',cls);button.textContent=text;button.onclick=click;return button;}
    function summarySave(){}
    </script>'''+motion+'''<script>
    var card=document.querySelector('details'),body=card.querySelector('.summary-group-content'),list=body.firstElementChild;
    for(var i=0;i<30;i++){var note=summaryNode('button','inbox-row');note.textContent='Note '+(i+1);list.appendChild(note);}
    var entry={card:card,body:body,heading:card.firstElementChild,name:'Test group',setOpen:function(value){card.open=value;},rememberPosition:function(){},restorePosition:function(){}};
    var deck=createSummaryDeck(document.getElementById('grid'),[entry]);
    card.firstElementChild.onclick=function(event){event.preventDefault();if(deck.active)deck.close(true,false);else deck.open(entry,true);};
    </script>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content(fixture)
            page.locator('summary').click()
            state = page.evaluate('''() => {
                var list=document.querySelector('.summary-deck-drawer .summary-group-points'),box=list.getBoundingClientRect();
                var visible=Array.from(list.children).filter(note=>{var r=note.getBoundingClientRect();return r.bottom>box.top&&r.top<box.bottom;});
                return {visible:visible.length,moving:document.querySelectorAll('.summary-deck-flight').length,hidden:visible.filter(note=>note.style.opacity==='0').length,rows:new Set(visible.map(note=>note.offsetTop)).size};
            }''')
            assert state['rows'] >= 2
            if width > 1000:
                assert state['visible'] > 6
            assert state['moving'] == state['visible'], json.dumps(state)
            assert state['hidden'] == state['visible'], 'No visible note should pop in before its animation'
            if os.environ.get('DECK_QA_DIR'):
                page.screenshot(path=str(Path(os.environ['DECK_QA_DIR']) / f'opening-{width}.png'))
                print(f'width={width}: {json.dumps(state)}')
            page.wait_for_function("document.querySelectorAll('.summary-deck-flight').length===0")
            assert page.locator('.summary-deck-drawer .inbox-row[style*="opacity: 0"]').count() == 0
            page.locator('summary').click()
            assert page.locator('.summary-deck-flight').count() == state['visible'], 'Every visible row should animate on return'
            page.wait_for_function("document.querySelectorAll('.summary-deck-flight').length===0")
            assert page.locator('.summary-deck-drawer').count() == 0
            page.emulate_media(reduced_motion='reduce')
            page.locator('summary').click()
            assert page.locator('.summary-deck-flight').count() == 0
            assert page.locator('.summary-deck-drawer .inbox-row').count() == 30
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            assert errors == []
        finally:
            browser.close()
