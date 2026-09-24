"""QQ Digest 命令行入口。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
import uvicorn

from .ai.factory import build_ai_client
from .archive import Archive
from .candidates import CandidateService
from .collector.fixture import FixtureCollector
from .collector.ntqq import NTQQCollector
from .config import ConfigError, load_config
from .knowledge import KnowledgeWriter
from .notify import QQBotNotifier, BotConfig, NotificationQueueProcessor
from .pipeline import DailyPipeline
from .web.app import create_app
from .web.auth import PasswordHasher

app = typer.Typer(help="QQ 群消息摘要工具")


def _console_safe(value: str, *, encoding: str | None = None) -> str:
    encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding)


@app.command("hash-password")
def hash_password(password: str) -> None:
    """生成 Web 登录密码散列。"""
    typer.echo(PasswordHasher.hash(password))


@app.command("doctor")
def doctor(config_path: Path = typer.Option(Path("config/config.yaml"))) -> None:
    """执行完整配置和依赖检查。"""
    checks_passed = 0
    checks_warning = 0
    checks_blocked = 0

    def ok(msg: str):
        nonlocal checks_passed
        checks_passed += 1
        typer.echo(f"  [通过] {msg}")

    def warn(msg: str):
        nonlocal checks_warning
        checks_warning += 1
        typer.echo(f"  [警告] {msg}")

    def block(msg: str):
        nonlocal checks_blocked
        checks_blocked += 1
        typer.echo(f"  [阻断] {msg}")

    # 1. 配置文件
    typer.echo("=== 配置检查 ===")
    try:
        config = load_config(config_path)
        ok(f"配置加载成功")
        ok(f"数据目录: {config.data_dir}")
    except Exception as exc:
        block(f"配置加载失败: {exc}")
        raise typer.Exit(1)

    # 2. 归档数据库
    typer.echo("=== 归档数据库 ===")
    try:
        archive = Archive.open(config.archive_path)
        ok(f"归档数据库: {config.archive_path}")
        cnt = archive.connection.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        ok(f"已配置群组: {cnt}")
    except Exception as exc:
        block(f"归档数据库失败: {exc}")

    # 3. NTQQ 数据库
    typer.echo("=== NTQQ 数据库 ===")
    if config.ntqq.enabled:
        db_dir = Path(config.ntqq.db_dir)
        if db_dir.exists():
            msg_db = db_dir / "nt_msg.db"
            if msg_db.exists():
                size_mb = msg_db.stat().st_size / 1024 / 1024
                ok(f"nt_msg.db 存在 ({size_mb:.1f} MB)")
            else:
                warn("nt_msg.db 不存在")
        else:
            block(f"数据库目录不存在: {db_dir}")

        try:
            collector = NTQQCollector(
                db_dir=config.ntqq.db_dir,
                qq_number=config.ntqq.qq_number,
                timezone_name=config.ntqq.timezone,
            )
            groups = collector.discover_groups()
            ok(f"发现 {len(groups)} 个群")
        except Exception as exc:
            warn(f"NTQQ 采集器初始化失败: {exc}")
    else:
        warn("NTQQ 采集未启用")

    # 4. 磁盘空间
    typer.echo("=== 磁盘空间 ===")
    try:
        import shutil
        usage = shutil.disk_usage(str(config.data_dir))
        free_gb = usage.free / 1024 / 1024 / 1024
        if free_gb > 1:
            ok(f"可用磁盘: {free_gb:.1f} GB")
        else:
            warn(f"磁盘空间不足: {free_gb:.1f} GB")
    except Exception as exc:
        warn(f"磁盘检查失败: {exc}")

    # 5. AI providers
    typer.echo("=== AI 服务 ===")
    ok(f"调用顺序: {' -> '.join(config.ai.provider_priority)}")
    if "chatgpt_bridge" in config.ai.provider_priority:
        wrapper = Path(config.ai.bridge_wrapper_path)
        accounts = Path(config.ai.bridge_account_directory)
        if wrapper.is_file() and accounts.is_dir():
            ok("本地 GPT bridge 包装器和 codexID 目录可用")
        else:
            warn("本地 GPT bridge 不完整，将在调用失败时使用后备服务")
    if "compatible" in config.ai.provider_priority:
        try:
            config.resolve_api_key()
            ok("兼容接口 API key 已设置")
            ok(f"兼容接口 base_url: {config.resolve_base_url()}")
            ok(f"兼容接口 model: {config.ai.model}")
        except Exception as exc:
            if config.ai.provider_priority == ["compatible"]:
                block(f"兼容接口不可用: {exc}")
            else:
                warn(f"后备兼容接口不可用: {exc}")

    # 6. QQ Bot
    typer.echo("=== QQ Bot ===")
    if config.qq_bot.enabled:
        if config.qq_bot.app_id:
            ok(f"Bot AppID: {config.qq_bot.app_id}")
        else:
            warn("Bot 启用但 AppID 为空")
        secret = os.environ.get(config.qq_bot.app_secret_env, "")
        if secret:
            ok("Bot AppSecret 已设置")
        else:
            warn(f"Bot AppSecret 未设置 (env: {config.qq_bot.app_secret_env})")
        if config.qq_bot.allowed_openids:
            ok(f"白名单用户: {len(config.qq_bot.allowed_openids)} 个")
        else:
            warn("白名单为空")
    else:
        typer.echo("  (QQ Bot 未启用)")

    # 7. Web 密码
    typer.echo("=== Web 安全 ===")
    if config.security.web_password_hash:
        ok("Web 密码散列已配置")
    else:
        block("Web 密码散列为空")
    try:
        secret = config.resolve_session_secret()
    except ConfigError as exc:
        secret = ""
        block(str(exc))
    if secret:
        ok("Session secret 已设置")
    elif not checks_blocked:
        warn(
            "Session secret 未设置 "
            f"(env: {config.security.session_secret_env} 或 security.session_secret_file)"
        )

    # 总结
    typer.echo(f"\n=== 检查结果: {checks_passed} 通过, {checks_warning} 警告, {checks_blocked} 阻断 ===")
    if checks_blocked:
        raise typer.Exit(1)


@app.command("serve")
def serve(config_path: Path = typer.Option(Path("config/config.yaml"))) -> None:
    """启动 Web 审核界面。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.seed_groups(config.groups)
    web_app = create_app(
        archive=archive,
        candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(config.resolve_knowledge_paths()),
        password_hash=config.security.web_password_hash,
        session_secret=config.resolve_session_secret(),
        session_hours=config.security.session_hours,
        config=config,
    )
    if not web_app.state.cookie.secret:
        raise typer.Exit("Session secret 未设置")
    uvicorn.run(web_app, host=config.web.host, port=config.web.port)


@app.command("discover-groups")
def discover_groups(
    config_path: Path = typer.Option(Path("config/config.yaml")),
) -> None:
    """列出本地 NTQQ 数据库中可采集的群。"""
    config = load_config(config_path)
    if not config.ntqq.enabled or not config.ntqq.db_dir:
        typer.echo("NTQQ 未启用！请在 config.yaml 中设置 ntqq.enabled=true")
        raise typer.Exit(1)
    collector = NTQQCollector(
        db_dir=config.ntqq.db_dir,
        qq_number=config.ntqq.qq_number,
        timezone_name=config.ntqq.timezone,
    )
    groups = collector.discover_groups()
    typer.echo(f"共发现 {len(groups)} 个群：")
    for g in sorted(groups, key=lambda x: x["message_count_30d"], reverse=True):
        latest = g["latest_message_at"].strftime("%Y-%m-%d") if g["latest_message_at"] else "无"
        typer.echo(_console_safe(
            f"  {g['group_id']:>12d}  {g['name'][:30]:<30s}  "
            f"消息数:{g['message_count_30d']:<6d}  最近:{latest}"
        ))


@app.command("sync")
def sync(
    config_path: Path = typer.Option(Path("config/config.yaml")),
    group_id: int = typer.Option(0, help="只同步指定群（0=全部启用群）"),
) -> None:
    """执行一次消息采集（增量同步）。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.seed_groups(config.groups)
    if not config.ntqq.enabled or not config.ntqq.db_dir:
        typer.echo("NTQQ 未启用")
        raise typer.Exit(1)
    collector = NTQQCollector(
        db_dir=config.ntqq.db_dir,
        qq_number=config.ntqq.qq_number,
        timezone_name=config.ntqq.timezone,
    )
    tz = ZoneInfo(config.summary.timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    groups = archive.enabled_groups()
    if group_id:
        groups = [g for g in groups if g.group_id == group_id]
    total = 0
    for g in groups:
        msgs = list(collector.collect(g.group_id, start, now))
        result = archive.ingest(msgs)
        archive.mark_sync(group_id=g.group_id, last_timestamp=now)
        archive.connection.commit()
        total += result.inserted
        typer.echo(_console_safe(
            f"  {g.name}: 新增 {result.inserted} 条, 跳过 {result.skipped} 条"
        ))
    typer.echo(f"采集完成: 共新增 {total} 条消息")


@app.command("backfill")
def backfill(
    config_path: Path = typer.Option(Path("config/config.yaml")),
    days: int = typer.Option(30, help="回填天数"),
) -> None:
    """为选定群执行初始回填。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.seed_groups(config.groups)
    if not config.ntqq.enabled or not config.ntqq.db_dir:
        typer.echo("NTQQ 未启用")
        raise typer.Exit(1)
    collector = NTQQCollector(
        db_dir=config.ntqq.db_dir,
        qq_number=config.ntqq.qq_number,
        timezone_name=config.ntqq.timezone,
    )
    tz = ZoneInfo(config.summary.timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    start = start - timedelta(days=days)
    total = 0
    for g in archive.enabled_groups():
        typer.echo(_console_safe(f"回填 {g.name}..."))
        msgs = list(collector.collect(g.group_id, start, now))
        result = archive.ingest(msgs)
        archive.mark_sync(group_id=g.group_id, last_timestamp=now)
        archive.connection.commit()
        total += result.inserted
        typer.echo(f"  新增 {result.inserted} 条, 跳过 {result.skipped} 条")
    typer.echo(f"回填完成: 共新增 {total} 条消息")


@app.command("summarize")
def summarize(
    config_path: Path = typer.Option(Path("config/config.yaml")),
) -> None:
    """为今天的时间窗生成摘要报告（不采集，只用已归档消息）。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.seed_groups(config.groups)
    from .scheduler import summary_window
    from .summary import Summarizer
    from .group_summary import GroupSummaryBuilder, summary_input_fingerprint
    from .reports import ReportWriter, published_report_is_valid
    from .candidates import CandidateService

    tz = ZoneInfo(config.summary.timezone)
    now = datetime.now(tz)
    start, end = summary_window(now, config.summary.window_mode, tz)
    report_date = start.date().isoformat()
    ai_client = build_ai_client(config)
    summarizer = Summarizer(ai=ai_client, max_context_chars=config.ai.max_context_chars)
    report_writer = ReportWriter(config.report_dir)
    candidates = CandidateService(archive)
    knowledge_parts = []
    for path in config.resolve_knowledge_paths().values():
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if content.strip():
                knowledge_parts.append(content)
    knowledge_base = "\n\n".join(knowledge_parts)

    try:
        for group in archive.enabled_groups(daily_summary=True):
            messages = archive.messages_between(group.group_id, start, end)
            if not messages:
                typer.echo(_console_safe(f"  {group.name}: 无消息"))
                continue
            fingerprint = summary_input_fingerprint(
                group=group,
                report_kind="daily",
                messages=messages,
                timezone=tz,
                knowledge_base=knowledge_base,
                max_context_chars=config.ai.max_context_chars,
            )
            existing = archive.report_for(group.group_id, report_date)
            if (
                existing is not None
                and existing["input_fingerprint"] == fingerprint
                and published_report_is_valid(
                    existing["markdown_path"], existing["json_path"]
                )
            ):
                typer.echo(_console_safe(f"  {group.name}: 报告未变化，跳过"))
                continue

            artifact = GroupSummaryBuilder(summarizer).build(
                group=group,
                window_start=start,
                window_end=end,
                report_date=report_date,
                candidate_date=report_date,
                messages=messages,
                timezone=tz,
                knowledge_base=knowledge_base,
                report_kind="daily",
            )
            prepared = report_writer.prepare_named(
                f"{report_date}__{group.group_id}",
                artifact.markdown,
                artifact.payload,
            )
            old_candidate_ids = (
                archive.candidates_for_report(existing["candidate_ids"])
                if existing is not None
                else []
            )
            try:
                with archive.transaction():
                    candidate_ids = [
                        candidates.create_in_transaction(**kwargs)
                        for kwargs in artifact.candidate_kwargs
                    ]
                    archive.record_report_in_transaction(
                        group_id=group.group_id,
                        report_date=report_date,
                        effective_template="adaptive",
                        markdown_path=prepared.paths.markdown,
                        json_path=prepared.paths.json,
                        candidate_ids=candidate_ids,
                        input_fingerprint=fingerprint,
                        source_message_count=artifact.source_message_count,
                    )
                    archive.delete_unreferenced_pending_candidates_in_transaction(
                        old_candidate_ids
                    )
                    prepared.install()
            except Exception:
                prepared.rollback()
                raise
            prepared.finalize()
            typer.echo(_console_safe(
                f"  {group.name}: 报告已生成, {len(candidate_ids)} 条候选"
            ))
    finally:
        ai_client.close()


@app.command("notify")
def notify(
    config_path: Path = typer.Option(Path("config/config.yaml")),
    report_id: int = typer.Option(0, help="指定报告ID（0=最新）"),
) -> None:
    """发送通知给已配置的 QQ Bot 用户。"""
    config = load_config(config_path)
    if not config.qq_bot.enabled:
        typer.echo("QQ Bot 未启用")
        raise typer.Exit(1)
    archive = Archive.open(config.archive_path)
    bot_config = BotConfig(
        app_id=config.qq_bot.app_id,
        app_secret=os.environ.get(config.qq_bot.app_secret_env, ""),
        allowed_openids=config.qq_bot.allowed_openids,
        api_base=config.qq_bot.api_base,
        websocket_url=config.qq_bot.websocket_url,
        max_retries=config.qq_bot.max_retries,
        retry_base_seconds=config.qq_bot.retry_base_seconds,
    )
    notifier = QQBotNotifier(bot_config)
    if report_id:
        row = archive.connection.execute("SELECT * FROM reports WHERE report_id=?", (report_id,)).fetchone()
    else:
        row = archive.connection.execute("SELECT * FROM reports ORDER BY report_id DESC LIMIT 1").fetchone()
    if not row:
        typer.echo("无报告可通知")
        raise typer.Exit(1)
    import json
    cand_count = len(json.loads(row["candidate_ids"]))
    group_row = archive.connection.execute("SELECT name FROM groups WHERE group_id=?", (row["group_id"],)).fetchone()
    group_name = group_row["name"] if group_row else str(row["group_id"])
    for openid in bot_config.allowed_openids:
        archive.enqueue_notification(
            report_id=int(row["report_id"]),
            channel="qq_bot_private",
            recipient=openid,
            payload={
                "group_name": group_name,
                "report_date": row["report_date"],
                "pending_candidates": cand_count,
            },
        )
    result = NotificationQueueProcessor(
        archive=archive,
        notifier=notifier,
        max_attempts=config.qq_bot.max_retries,
        retry_base_seconds=config.qq_bot.retry_base_seconds,
    ).process_due()
    typer.echo(f"通知发送成功 {result.sent} 条，待重试或失败 {result.failed} 条")


@app.command("refresh")
def refresh(
    config_path: Path = typer.Option(Path("config/config.yaml")),
) -> None:
    """刷新 NTQQ 数据库：从 QQ 进程提取密钥并解密最新数据。"""
    config = load_config(config_path)
    if not config.ntqq.enabled:
        typer.echo("NTQQ 未启用！请在 config.yaml 中设置 ntqq.enabled=true")
        raise typer.Exit(1)

    from .refresh import refresh_database
    result = refresh_database(
        qq_number=config.ntqq.qq_number,
        output_dir=config.ntqq.db_dir,
        snapshot_root=config.work_dir / "snapshots",
    )
    typer.echo(result.message)
    if result.errors:
        for err in result.errors:
            typer.echo(f"  {err}")
    if not result.success:
        raise typer.Exit(1)


@app.command("run-daily")
def run_daily(
    config_path: Path = typer.Option(Path("config/config.yaml")),
) -> None:
    """执行一次同步、摘要、报告和候选流水线。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.seed_groups(config.groups)
    if config.ntqq.enabled and config.ntqq.db_dir:
        collector = NTQQCollector(
            db_dir=config.ntqq.db_dir,
            qq_number=config.ntqq.qq_number,
            timezone_name=config.ntqq.timezone,
        )
    else:
        collector = FixtureCollector()

    bot_config = None
    if config.qq_bot.enabled and config.qq_bot.app_id:
        bot_config = BotConfig(
            app_id=config.qq_bot.app_id,
            app_secret=os.environ.get(config.qq_bot.app_secret_env, ""),
            allowed_openids=config.qq_bot.allowed_openids,
            api_base=config.qq_bot.api_base,
            websocket_url=config.qq_bot.websocket_url,
            max_retries=config.qq_bot.max_retries,
            retry_base_seconds=config.qq_bot.retry_base_seconds,
        )

    pipeline = DailyPipeline(
        archive=archive,
        collector=collector,
        ai_client=build_ai_client(config),
        report_dir=config.report_dir,
        max_context_chars=config.ai.max_context_chars,
        timezone_name=config.summary.timezone,
        window_mode=config.summary.window_mode,
        bot_config=bot_config,
        knowledge_paths=config.resolve_knowledge_paths(),
    )
    result = pipeline.run_daily(datetime.now(ZoneInfo(config.summary.timezone)))
    typer.echo(
        f"处理 {result.groups_processed} 个群，新增 {result.messages_inserted} 条消息，"
        f"生成 {len(result.report_paths)} 份报告，候选 {len(result.candidate_ids)} 条。"
    )
    if result.failed_groups:
        typer.echo(
            "部分群处理失败: "
            + "; ".join(
                f"{item.group_name}({item.stage})" for item in result.failed_groups
            ),
            err=True,
        )


def main() -> None:
    app()
