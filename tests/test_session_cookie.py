from qq_digest.web.auth import SessionCookie


def test_signed_nonce_is_unique_bound_and_expires(monkeypatch):
    monkeypatch.setattr('qq_digest.web.auth.time.time',lambda:1000)
    cookie=SessionCookie('synthetic-secret',60)
    first,second=cookie.issue(),cookie.issue()
    assert first!=second and cookie.verify(first) and cookie.verify(second)
    expires,nonce,signature=first.split('.')
    assert not cookie.verify(f'{expires}.{second.split(".")[1]}.{signature}')
    for malformed in (None,'','bad',f'{expires}.{signature}','nondigit.'+nonce+'.'+signature,'9'*1000+'.'+nonce+'.'+signature,f'{expires}.{nonce}.é'):
        assert not cookie.verify(malformed)
    monkeypatch.setattr('qq_digest.web.auth.time.time',lambda:1061)
    assert not cookie.verify(first)
