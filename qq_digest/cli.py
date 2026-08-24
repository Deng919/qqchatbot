from __future__ import annotations

import os
from pathlib import Path

import typer
import uvicorn

from .archive import Archive
from .candidates import CandidateService
from .config import load_config
from .knowledge import KnowledgeWriter
from .web.app import create_app
from .web.auth import PasswordHasher


app = typer.Typer(help="QQ 群消息摘要工具")


@app.command("hash-password")
def hash_password(password: str) -> None:
    """生成 Web 登录密码散列。"""
    typer.echo(PasswordHasher.hash(password))


@app.command("doctor")
def doctor(config_path: Path = typer.Option(Path("config/config.yaml"))) -> None:
    """执行基础配置和依赖检查。"""
    try:
        config = load_config(config_path)
    except Exception as exc:
        typer.echo(f"[阻断] 配置检查失败: {exc}")
        raise typer.Exit(1)
    typer.echo("[通过] 配置可加载")
    typer.echo(f"[通过] 归档路径: {config.archive_path}")


@app.command("serve")
def serve(config_path: Path = typer.Option(Path("config/config.yaml"))) -> None:
    """启动 Web 审核界面。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.upsert_groups(config.groups)
    web_app = create_app(
        archive=archive,
        candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(config.knowledge_dir),
        password_hash=config.security.web_password_hash,
        session_secret=os.environ.get(config.security.session_secret_env, ""),
        session_hours=config.security.session_hours,
    )
    if not web_app.state.cookie.secret:
        raise typer.Exit("QQ_DIGEST_SESSION_SECRET 未设置")
    uvicorn.run(web_app, host=config.web.host, port=config.web.port)


def main() -> None:
    app()
