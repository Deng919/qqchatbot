from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
import uvicorn

from .ai.client import AIClient
from .archive import Archive
from .candidates import CandidateService
from .collector.fixture import FixtureCollector
from .config import load_config
from .knowledge import KnowledgeWriter
from .pipeline import DailyPipeline
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


@app.command("run-daily")
def run_daily(
    config_path: Path = typer.Option(Path("config/config.yaml")),
) -> None:
    """执行一次同步、摘要、报告和候选流水线。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.upsert_groups(config.groups)
    pipeline = DailyPipeline(
        archive=archive,
        collector=FixtureCollector(),
        ai_client=AIClient(
            base_url=config.ai.base_url,
            api_key=config.resolve_api_key(),
            model=config.ai.model,
            timeout_seconds=config.ai.timeout_seconds,
        ),
        report_dir=config.report_dir,
        max_context_chars=config.ai.max_context_chars,
        timezone_name=config.summary.timezone,
        window_mode=config.summary.window_mode,
    )
    result = pipeline.run_daily(datetime.now(ZoneInfo(config.summary.timezone)))
    typer.echo(
        f"处理 {result.groups_processed} 个群，新增 {result.messages_inserted} 条消息，"
        f"生成 {len(result.report_paths)} 份报告，候选 {len(result.candidate_ids)} 条。"
    )


def main() -> None:
    app()
