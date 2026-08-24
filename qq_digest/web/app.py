from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..archive import Archive
from ..candidates import CandidateService
from ..knowledge import KnowledgeItem, KnowledgeWriter
from .auth import PasswordHasher, SessionCookie


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def require_login(request: Request):
    cookie = request.app.state.cookie
    if not cookie.verify(request.cookies.get("qq_digest_session")):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def create_app(
    *,
    archive: Archive,
    candidates: CandidateService,
    knowledge: KnowledgeWriter,
    password_hash: str,
    session_secret: str,
    session_hours: int = 12,
):
    app = FastAPI(title="QQ Digest")
    cookie = SessionCookie(session_secret, session_hours * 3600)
    app.state.archive = archive
    app.state.candidates = candidates
    app.state.knowledge = knowledge
    app.state.password_hash = password_hash
    app.state.cookie = cookie
    group_names = {
        group.group_id: group.name for group in archive.enabled_groups()
    }

    @app.get("/login")
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {})

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if not PasswordHasher.verify(password, app.state.password_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "密码错误"}, status_code=401
            )
        response = RedirectResponse("/candidates", status_code=303)
        response.set_cookie("qq_digest_session", cookie.issue(), httponly=True)
        return response

    @app.get("/candidates")
    async def candidate_list(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {"candidates": candidates.pending()},
        )

    @app.post("/candidates/{candidate_id}/confirm")
    async def confirm(request: Request, candidate_id: int):
        require_login(request)
        candidate = candidates.get(candidate_id)
        knowledge.write(
            KnowledgeItem(
                item_id=str(candidate_id),
                date=candidate.created_date,
                category="资源" if candidate.candidate_type == "resource" else "经验",
                source_group=group_names.get(candidate.group_id, str(candidate.group_id)),
                title=candidate.title,
                link=candidate.link,
                value=candidate.reason,
                excerpt=candidate.excerpt,
            ),
            candidate.candidate_type,
        )
        archive.record_knowledge_item(
            item_id=str(candidate_id),
            candidate_id=candidate_id,
            markdown_path=str(knowledge.directory / knowledge.filenames[candidate.candidate_type]),
        )
        candidates.confirm(candidate_id)
        archive.connection.commit()
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/ignore")
    async def ignore(request: Request, candidate_id: int, reason: str = Form("")):
        require_login(request)
        candidates.ignore(candidate_id, reason)
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/later")
    async def later(request: Request, candidate_id: int):
        require_login(request)
        candidates.update_status(candidate_id, "later")
        return RedirectResponse("/candidates", status_code=303)

    return app
