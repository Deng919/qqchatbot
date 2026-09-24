from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .archive import Archive
from .collector.ntqq import NTQQCollector
from .config import Config


@dataclass
class MessageSyncResult:
    success: bool
    groups_attempted: int = 0
    groups_succeeded: int = 0
    messages_inserted: int = 0
    messages_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_message_sync(
    *,
    config: Config,
    now: datetime | None = None,
    refresh: bool = True,
    collector=None,
) -> MessageSyncResult:
    """Refresh NTQQ and incrementally archive every enabled group."""
    timezone = ZoneInfo(config.summary.timezone)
    current = (now or datetime.now(timezone)).astimezone(timezone)
    archive = Archive.open(config.archive_path)
    job_id = archive.start_job("message_sync")
    result = MessageSyncResult(success=False)
    try:
        if not config.ntqq.enabled or not config.ntqq.db_dir:
            raise RuntimeError("NTQQ 未启用")

        if refresh:
            from .refresh import refresh_database

            refresh_result = refresh_database(
                qq_number=config.ntqq.qq_number,
                output_dir=config.ntqq.db_dir,
                snapshot_root=config.work_dir / "snapshots",
            )
            if not refresh_result.success:
                raise RuntimeError(f"数据库刷新失败: {refresh_result.message}")

        active_collector = collector or NTQQCollector(
            db_dir=config.ntqq.db_dir,
            qq_number=config.ntqq.qq_number,
            timezone_name=config.ntqq.timezone,
        )
        groups = archive.enabled_groups()
        result.groups_attempted = len(groups)
        overlap = timedelta(minutes=config.collection.overlap_minutes)

        for group in groups:
            state = archive.connection.execute(
                "SELECT last_timestamp FROM sync_state WHERE group_id=?",
                (group.group_id,),
            ).fetchone()
            if state and state["last_timestamp"]:
                last_sync = datetime.fromisoformat(state["last_timestamp"]).astimezone(timezone)
                start = min(last_sync, current) - overlap
            else:
                start = current - timedelta(days=group.collection_window_days)

            try:
                messages = list(active_collector.collect(group.group_id, start, current))
                ingest = archive.ingest(messages)
                archive.mark_sync(group_id=group.group_id, last_timestamp=current)
                result.groups_succeeded += 1
                result.messages_inserted += ingest.inserted
                result.messages_skipped += ingest.skipped
            except Exception as exc:
                archive.mark_sync_failure(
                    group_id=group.group_id,
                    status="adapter_incompatible",
                )
                result.errors.append(f"{group.name}: {exc}")

        result.success = not result.errors
        archive.finish_job(
            job_id,
            "success" if result.success else "failed",
            "\n".join(result.errors),
        )
        return result
    except Exception as exc:
        result.errors.append(str(exc))
        archive.finish_job(job_id, "failed", str(exc))
        return result
    finally:
        archive.close()
