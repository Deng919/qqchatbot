from datetime import datetime, timezone

import pytest

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.search import search_archive


@pytest.fixture
def archive(tmp_path):
    result = Archive.open(tmp_path / 'archive.sqlite')
    result.upsert_groups([GroupConfig(group_id=1, name='研发')])
    yield result
    result.close()


def report(archive, tmp_path, body, kind='daily', day=16):
    path = tmp_path / f'{kind}-{day}.md'
    path.write_bytes(body.encode('utf-8'))
    kwargs = dict(group_id=1, markdown_path=path, json_path=path.with_suffix('.json'),
                  candidate_ids=[])
    if kind == 'daily':
        rid = archive.record_report(report_date=f'2026-10-{day}', **kwargs)
    else:
        rid = archive.record_manual_report(start_date='2026-10-01', end_date=f'2026-10-{day}',
            detail_mode='group', effective_template='', input_fingerprint='',
            source_message_count=0, **kwargs)
    return rid, path


@pytest.mark.parametrize('kind', ['daily', 'range'])
def test_report_snippets_are_readable_without_changing_report_bytes(archive, tmp_path, kind):
    body = ('## **接口配置**\n- *中文😀*、__重点__、_提示_，`api_key`。 〔原消息：m1、m2〕\n'
            '1. [官方文档](https://example.com/api_key?q=中文#用法) [原消息: m3]\n'
            '+ 2*3=6，a_b 和 x*y*z。 【原消息：m4】')
    rid, path = report(archive, tmp_path, body, kind)
    result = search_archive(archive, query='接口配置', kind='report')
    assert result['total'] == 1
    item = result['results'][0]
    assert (item['id'], item['report_kind']) == (rid, kind)
    assert item['snippet'] == ('接口配置 中文😀、重点、提示，api_key。 '
        '官方文档 (https://example.com/api_key?q=中文#用法) 2*3=6，a_b 和 x*y*z。')
    assert item['text'] == ''
    assert path.read_bytes() == body.encode('utf-8')


@pytest.mark.parametrize(('body', 'expected'), [
    ('关键词 **粗体** 和 ***强调*** 与 ``a`b``', '关键词 粗体 和 强调 与 a`b'),
    ('关键词 [资料](https://example.com/a_(b)?x=1&y=2)',
     '关键词 资料 (https://example.com/a_(b)?x=1&y=2)'),
    ('关键词 [危险文字](javascript:alert(1)) <img src=x onerror=alert(1)>',
     '关键词 危险文字 (javascript:alert(1)) <img src=x onerror=alert(1)>'),
    ('关键词 `**literal** 〔原消息：code〕`', '关键词 **literal** 〔原消息：code〕'),
    ('关键词\n```api_key```', '关键词 api_key'),
    ('关键词\n```text\n## **literal** [资料](https://example.com) 〔原消息：code〕\n```',
     '关键词 ## **literal** [资料](https://example.com) 〔原消息：code〕'),
    ('关键词\n~~~~\n- `literal` a_b 2*3\n~~~\n~~~~', '关键词 - `literal` a_b 2*3 ~~~'),
    ('关键词 a_b a__b__c 2*3=6 x*y*z https://example.com/a_b?q=x_y',
     '关键词 a_b a__b__c 2*3=6 x*y*z https://example.com/a_b?q=x_y'),
])
def test_report_code_url_and_literal_text(archive, tmp_path, body, expected):
    report(archive, tmp_path, body)
    assert search_archive(archive, query='关键词', kind='report')['results'][0]['snippet'] == expected


@pytest.mark.parametrize(('body', 'expected'), [
    ('关键词 **`api_key`**', '关键词 api_key'),
    ('关键词 **[文档](https://example.com/a_b)**', '关键词 文档 (https://example.com/a_b)'),
    ('关键词 **https://example.com/a_b**', '关键词 https://example.com/a_b'),
    ('关键词 *https://example.com/a_b*', '关键词 https://example.com/a_b'),
    ('关键词 __https://example.com/a_b__', '关键词 https://example.com/a_b'),
    ('关键词 **详情 https://example.com/a_b**', '关键词 详情 https://example.com/a_b'),
    ('关键词 __详情 https://example.com/a_b__', '关键词 详情 https://example.com/a_b'),
    ('关键词 **`**literal**`** 和 `**other**`', '关键词 **literal** 和 **other**'),
    ('关键词 **[文档](https://example.com/a**b**c)**',
     '关键词 文档 (https://example.com/a**b**c)'),
    ('关键词 https://example.com/a**b**c', '关键词 https://example.com/a**b**c'),
    ('关键词 \x00\x000\x00 `**literal**` 和 **`api_key`**',
     '关键词 \x00\x000\x00 **literal** 和 api_key'),
])
def test_report_composite_formatting_preserves_protected_contents(archive, tmp_path, body, expected):
    report(archive, tmp_path, body)
    assert search_archive(archive, query='关键词', kind='report')['results'][0]['snippet'] == expected


@pytest.mark.parametrize('body', [
    '关键词 a_b https://example.com/end_',
    '关键词 a__b https://example.com/end__',
    '关键词 2*3 https://example.com/end*',
])
def test_report_bare_url_suffix_does_not_pair_with_literal_symbols(archive, tmp_path, body):
    report(archive, tmp_path, body)
    assert search_archive(archive, query='关键词', kind='report')['results'][0]['snippet'] == body


@pytest.mark.parametrize('literal_index', ['99', '0'])
def test_citation_removal_cannot_synthesize_protected_span_placeholders(archive, tmp_path, literal_index):
    body = f'关键词 \x00〔原消息：x〕\x00{literal_index}\x00〔原消息：x〕\x00 `code`'
    report(archive, tmp_path, body)
    expected = f'关键词 \x00\x00{literal_index}\x00\x00 code'
    assert search_archive(archive, query='关键词', kind='report')['results'][0]['snippet'] == expected


def test_report_matching_order_and_pagination_use_original_markdown(archive, tmp_path):
    daily, _ = report(archive, tmp_path, '## **接口**\n〔原消息：raw-only〕')
    ranged, _ = report(archive, tmp_path, '- **接口**\n〔原消息：raw-only〕', 'range')
    archive.connection.execute("UPDATE reports SET created_at='2026-10-16T00:00:00+00:00'")
    archive.connection.execute("UPDATE manual_reports SET updated_at='2026-10-17T00:00:00+00:00'")
    archive.connection.commit()
    for query in ('**接口**', 'raw-only'):
        pages = [search_archive(archive, query=query, kind='report', page=page, page_size=1)
                 for page in (1, 2, 3)]
        assert [page['total'] for page in pages] == [2, 2, 2]
        assert [(item['report_kind'], item['id']) for page in pages for item in page['results']] == [
            ('range', ranged), ('daily', daily)]
        assert [item['snippet'] for page in pages for item in page['results']] == ['接口', '接口']
    # Cleaning must not broaden raw matching across Markdown delimiters.
    report(archive, tmp_path, '关**键**词', day=15)
    assert search_archive(archive, query='关键词', kind='report')['total'] == 0


def test_message_and_knowledge_snippets_keep_original_markup(archive, tmp_path):
    body = '**接口配置** 〔原消息：raw〕 [资料](https://example.com)'
    stamp = datetime(2026, 10, 16, tzinfo=timezone.utc)
    archive.ingest([NormalizedMessage(msg_id='source', group_id=1, timestamp=stamp,
        collected_at=stamp, text=body)])
    service = CandidateService(archive)
    cid = service.create(group_id=1, created_date='2026-10-16', candidate_type='experience',
        title=body, content='', reason='', excerpt='', message_ids=['source'])
    service.update_status(cid, 'confirmed')
    archive.connection.execute('INSERT INTO knowledge_items VALUES(?,?,?,?)',
        (str(cid), cid, str(tmp_path / 'knowledge.md'), '2026-10-16'))
    archive.connection.commit()
    report(archive, tmp_path, body)
    results = search_archive(archive, query='接口配置')['results']
    assert len(results) == 3
    for item in results:
        if item['kind'] in {'message', 'knowledge'}:
            assert item['snippet'] == body
            assert item['text'].strip() == body
        else:
            assert item['snippet'] == '接口配置 资料 (https://example.com)'
