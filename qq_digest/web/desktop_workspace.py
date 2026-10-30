"""Authenticated host for persistent desktop page documents."""
from fastapi import Request
from fastapi.responses import RedirectResponse


def add_desktop_workspace_route(app, *, templates):
    @app.get('/desktop-workspace', include_in_schema=False)
    async def desktop_workspace(request: Request):
        if not app.state.cookie.verify(request.cookies.get('qq_digest_session')):
            return RedirectResponse('/login', status_code=303)
        return templates.TemplateResponse(
            request, 'desktop_workspace.html', {}, headers={'Cache-Control': 'no-store'})
