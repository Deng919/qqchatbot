"""Short-lived, single-use desktop launch proof; ordinary web login stays required."""
from threading import Lock
import time

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .auth import SessionCookie


class DesktopLaunchTokens:
    ttl_seconds = 120

    def __init__(self, secret: str):
        self.signer = SessionCookie('desktop-launch:' + secret, self.ttl_seconds)
        self.used: dict[str, int] = {}
        self.lock = Lock()

    def issue(self) -> str:
        return self.signer.issue()

    def consume(self, token: str) -> bool:
        with self.lock:
            now = int(time.time())
            self.used = {key: expiry for key, expiry in self.used.items() if expiry >= now}
            if not self.signer.verify(token) or token in self.used:
                return False
            expiry = int(token.split('.', 1)[0])
            if expiry > now + self.ttl_seconds:
                return False
            self.used[token] = expiry
            return True


class DesktopProof(BaseModel):
    token: str = Field(min_length=1, max_length=256)


def desktop_start_url(url: str, secret: str) -> str:
    return url.rstrip('/') + '/desktop-start#' + DesktopLaunchTokens(secret).issue()


START_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="referrer" content="no-referrer"><title>QQ Digest</title>
<p id="status" role="status">正在打开群聊简报…</p><a id="login" href="/login" hidden>前往登录</a>
<script>
(function(){
  var token=location.hash.slice(1);history.replaceState(null,'','/desktop-start');
  fetch('/api/desktop-session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token})})
    .then(function(response){if(!response.ok)throw new Error('session');location.replace('/desktop-workspace');})
    .catch(function(){document.getElementById('status').textContent='桌面会话未能建立，请重新打开程序。';document.getElementById('login').hidden=false;});
})();
</script></html>"""


def add_desktop_session_routes(app, secret: str):
    proofs = DesktopLaunchTokens(secret)

    @app.get('/desktop-start', include_in_schema=False)
    async def desktop_start():
        return HTMLResponse(START_PAGE, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @app.post('/api/desktop-session', include_in_schema=False)
    async def desktop_session(request: Request, payload: DesktopProof):
        if (request.client is None or request.client.host not in {'127.0.0.1', '::1'}
                or request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or request.headers.get('origin', str(request.base_url).rstrip('/')) != str(request.base_url).rstrip('/')):
            raise HTTPException(403, '仅支持本机桌面启动')
        if not proofs.consume(payload.token):
            raise HTTPException(401, '桌面启动凭据无效或已过期')
        response = JSONResponse({'ready': True}, headers={'Cache-Control': 'no-store'})
        response.set_cookie('qq_digest_session', app.state.cookie.issue(), httponly=True, samesite='strict')
        return response
