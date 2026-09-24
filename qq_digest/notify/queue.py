from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..archive import Archive


@dataclass(frozen=True)
class QueueProcessResult:
    sent: int = 0
    failed: int = 0


class NotificationQueueProcessor:
    def __init__(
        self,
        *,
        archive: Archive,
        notifier,
        max_attempts: int = 3,
        retry_base_seconds: int = 60,
    ) -> None:
        self.archive = archive
        self.notifier = notifier
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def process_due(self, *, now: datetime | None = None) -> QueueProcessResult:
        now = now or datetime.now(timezone.utc)
        sent = 0
        failed = 0
        for notification in self.archive.claim_due_notifications(now=now):
            payload = notification.payload
            try:
                result = self.notifier.send_report_notification(
                    openid=notification.recipient,
                    group_name=payload.get("group_name", ""),
                    report_date=payload.get("report_date", ""),
                    pending_candidates=int(payload.get("pending_candidates", 0)),
                    report_url=payload.get("report_url", ""),
                )
                success = result.success
                error = result.error
            except Exception as exc:
                success = False
                error = str(exc)
            if success:
                self.archive.mark_notification_success(notification.send_id, now=now)
                sent += 1
                continue
            delay = self.retry_base_seconds * (2 ** notification.retry_count)
            self.archive.mark_notification_retry(
                notification.send_id,
                error=error,
                next_attempt_at=now + timedelta(seconds=delay),
                max_attempts=self.max_attempts,
                now=now,
            )
            failed += 1
        return QueueProcessResult(sent=sent, failed=failed)
