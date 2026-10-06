"""First-use configuration and local import. No requests to AI on page reads."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import yaml

from .archive import Archive
from .collector.ntqq import NTQQCollector
from .config import load_config
from .distribution import is_public_distribution, validate_public_ai
from .features import FeatureService, FeatureConflict


class SetupConflict(ValueError):
    pass


def setup_in_progress(config) -> bool:
    return bool(config and getattr(config, 'first_use', {}).get('active'))


def detect_accounts(root: Path | None = None) -> list[dict]:
    root = root or Path.home() / 'Documents' / 'Tencent Files'
    if not root.is_dir():
        return []
    accounts = []
    for child in root.iterdir():
        if child.name.isascii() and child.name.isdigit() and 0 < int(child.name) < 2**63:
            if (child / 'nt_qq' / 'nt_db').is_dir():
                accounts.append({'qq_number': int(child.name), 'source_path': str(child / 'nt_qq' / 'nt_db')})
    return sorted(accounts, key=lambda item: item['qq_number'])


def refresh_setup_source(qq_number: int, work_dir: Path) -> Path:
    """Only decrypt to this program's account-specific cache, never a caller's path."""
    from .refresh import refresh_database
    from .runtime_paths import cache_root
    root = cache_root() / 'QQSources' if is_public_distribution() else Path(r'D:\Cache\QQDigestSources')
    destination = root / str(qq_number)
    if destination.resolve() != destination.absolute() or root.resolve() != root.absolute():
        raise ValueError('数据缓存目录存在重定向，请改用已有解密库')
    result = refresh_database(qq_number=qq_number, output_dir=str(destination),
                              snapshot_root=work_dir / 'snapshots')
    if not result.success:
        raise ValueError('无法读取 QQ，请登录所选账号并保持 QQ 运行，再重试；也可使用已有解密库')
    return destination


class ConfigFileStore:
    def __init__(self, config, path: Path | None):
        self.config = config
        self.path = Path(path).resolve() if path else None
        self.revision = sha256(self.path.read_bytes()).hexdigest() if self.path else ''
        self.lock = RLock()

    def check(self, expected: str):
        if not self.path:
            raise ValueError('当前服务未提供配置位置，请从桌面版或 serve 命令启动')
        if expected != self.revision:
            raise SetupConflict('设置已在其他窗口更新，请重新载入后重试')
        if sha256(self.path.read_bytes()).hexdigest() != self.revision:
            raise SetupConflict('配置文件已在程序外修改，请重启程序后重试')

    def save(self, expected: str, patch: dict, state: dict):
        with self.lock:
            self.check(expected)
            raw = yaml.safe_load(self.path.read_text('utf-8')) or {}
            for section, values in patch.items():
                raw.setdefault(section, {}).update(values)
            raw['first_use'] = state
            content = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode('utf-8')
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, suffix='.yaml', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                validated = load_config(temporary, create_dirs=False)
                self.check(expected)
                os.replace(temporary, self.path)
                self.revision = sha256(content).hexdigest()
                # Existing schedulers/routes capture this Config object.
                for section in patch:
                    setattr(self.config, section, getattr(validated, section))
                self.config.first_use = deepcopy(state)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)


class FirstUseService:
    def __init__(self, config, config_path, archive):
        self.config = config
        self.archive = archive
        self.store = ConfigFileStore(config, config_path)

    def _state(self):
        return deepcopy(getattr(self.config, 'first_use', {}))

    def _save(self, revision, state, patch=None):
        self.store.save(revision, patch or {}, state)
        return self.snapshot()

    def _save_with_database(self, revision, state, patch, worker):
        old_content = self.store.path.read_bytes()
        old_state = self._state()
        old_sections = {key: getattr(self.config, key).model_copy(deep=True) for key in patch}
        self.store.save(revision, patch, state)
        try:
            worker.connection.commit()
        except Exception:
            self.store.check(self.store.revision)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.store.path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(old_content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.store.path)
                self.store.revision = revision
                self.config.first_use = old_state
                for key, value in old_sections.items(): setattr(self.config, key, value)
            finally:
                if temporary: temporary.unlink(missing_ok=True)
            raise
        return self.snapshot()

    def snapshot(self):
        cfg, state = self.config, self._state()
        features = FeatureService(self.archive).snapshot()
        today = datetime.now(ZoneInfo(cfg.ntqq.timezone)).date()
        return {'available': bool(self.store.path), 'revision': self.store.revision,
            'active': state.get('active', False), 'completed': state.get('completed', False),
            'step': state.get('step', 1), 'selected_groups': state.get('selected_groups', []),
            'group_names': {str(group.group_id): group.name for group in self.archive.all_groups()
                            if group.group_id in state.get('selected_groups', [])},
            'imports': state.get('imports', {}), 'ai_ready': state.get('ai_ready', False),
            'ai_done': state.get('ai_done', False), 'schedule_done': state.get('schedule_done', False),
            'start_date': state.get('start_date', (today - timedelta(days=6)).isoformat()),
            'end_date': state.get('end_date', today.isoformat()),
            'ntqq': cfg.ntqq.model_dump(),
            'ai': {'base_url': cfg.ai.base_url, 'model': cfg.ai.model,

                'provider': cfg.ai.provider_priority[0],
                'key_configured': bool(cfg.ai.ui_api_key_file and Path(cfg.ai.ui_api_key_file).is_file()),
                **({} if is_public_distribution() else {
                    'bridge_model': cfg.ai.bridge_model,
                    'bridge_available': Path(cfg.ai.bridge_wrapper_path).is_file()})},
            'schedule': {'auto_collection': features['values']['auto_collection'] and cfg.collection.enabled,
                'auto_daily': features['values']['auto_daily'], 'interval_minutes': cfg.collection.interval_minutes,
                'hour': cfg.summary.hour, 'minute': cfg.summary.minute, 'timezone': cfg.summary.timezone},
            'features_revision': features['revision']}

    def source_groups(self, path=None, qq_number=None):
        directory = Path(path or self.config.ntqq.db_dir)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError('请输入已有解密库的完整文件夹路径')
        original_root = (Path.home() / 'Documents' / 'Tencent Files').resolve()
        if directory.resolve().is_relative_to(original_root):
            raise ValueError('请选择读取后的解密库，不要直接使用 QQ 原数据库目录')
        if not (directory / 'group_info.db').is_file() or not any(
            (directory / name).is_file() for name in ('group_msg_fts.db', 'nt_msg.db')):
            raise ValueError('目录需包含 group_info.db 和消息库；请先读取 QQ 或选择已有解密库')
        try:
            groups = NTQQCollector(directory, qq_number if qq_number is not None else self.config.ntqq.qq_number,
                                   self.config.ntqq.timezone).discover_groups()
        except Exception as exc:
            raise ValueError('源库无法读取，可能仍被加密或不完整；请重新读取 QQ 或选择有效解密库') from exc
        for group in groups:
            if group['latest_message_at']:
                group['latest_message_at'] = group['latest_message_at'].isoformat()
        return [group for group in groups if type(group['group_id']) is int and 0 < group['group_id'] < 2**63]

    def set_source(self, revision, path, qq_number):
        self.store.check(revision)
        self.source_groups(path, qq_number)
        state = self._state()
        # Re-reading even the same source invalidates old selections/results.
        state.update(active=True, completed=False, step=2, selected_groups=[], imports={}, schedule_done=False)
        return self._save(revision, state, {'ntqq': {'enabled': True,
            'db_dir': str(Path(path).resolve()), 'qq_number': qq_number}})

    def select_groups(self, revision, group_ids):
        self.store.check(revision)
        if not self.config.ntqq.enabled:
            raise ValueError('请先保存数据源')
        available = {g['group_id']: g for g in self.source_groups()}
        if len(group_ids) != len(set(group_ids)) or any(gid not in available for gid in group_ids):
            raise ValueError('群聊已变化，请重新扫描并选择')
        worker = Archive.open(self.config.archive_path)
        state = self._state()
        if group_ids != state.get('selected_groups'):
            state['imports'] = {}
            state['schedule_done'] = False
        state.update(selected_groups=group_ids, step=3, completed=False, active=True)
        try:
            worker.connection.execute('BEGIN IMMEDIATE')
            for gid in group_ids:
                worker.connection.execute('INSERT INTO groups(group_id,name) VALUES (?,?) ON CONFLICT(group_id) DO UPDATE SET enabled=1',
                    (gid, available[gid]['name']))
            return self._save_with_database(revision, state, {}, worker)
        finally:
            worker.close()

    def import_group(self, revision, group_id, start_date, end_date):
        self.store.check(revision)
        state = self._state()
        if group_id not in state.get('selected_groups', []):
            raise ValueError('请先选择要导入的群聊')
        if not 0 <= (end_date - start_date).days < 31:
            raise ValueError('请选择 1 至 31 天，开始日期不能晚于结束日期')
        if (state.get('start_date'), state.get('end_date')) != (start_date.isoformat(), end_date.isoformat()):
            state['imports'] = {}
        state.update(start_date=start_date.isoformat(), end_date=end_date.isoformat(), schedule_done=False,
                     step=3, completed=False, active=True)
        state.setdefault('imports', {})[str(group_id)] = {'status': 'running'}
        self._save(revision, state)
        worker = Archive.open(self.config.archive_path)
        try:
            if group_id not in {g.group_id for g in worker.enabled_groups()}:
                raise ValueError('群聊未启用')
            tz = ZoneInfo(self.config.ntqq.timezone)
            start = datetime.combine(start_date, time.min, tzinfo=tz)
            end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz) - timedelta(microseconds=1)
            collector = NTQQCollector(self.config.ntqq.db_dir, self.config.ntqq.qq_number, self.config.ntqq.timezone)
            messages = list(collector.collect(group_id, start, end))
            result = worker.ingest(messages)
            worker.mark_manual_collect_success(group_id=group_id)
            state['imports'][str(group_id)] = {'status': 'success', 'total': len(messages),
                'inserted': result.inserted, 'skipped': result.skipped}
        except Exception:
            state['imports'][str(group_id)] = {'status': 'failed',
                'error': '导入失败，请重新读取 QQ 后重试，或检查源库和存储空间'}
        finally:
            worker.close()
        if all(state['imports'].get(str(gid), {}).get('status') == 'success' for gid in state['selected_groups']):
            state['step'] = 4
        return self._save(self.store.revision, state)

    def set_ai(self, revision, provider, base_url, model, api_key=''):
        self.store.check(revision)
        if is_public_distribution():
            validate_public_ai([provider], base_url)
        if provider == 'compatible':
            parsed = urlsplit(base_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('接口地址需为 HTTP(S) 地址，不含账号、密码、查询参数或片段')
        patch = {'provider_priority': [provider], 'base_url': base_url.strip(),
                 'model': model.strip() if provider == 'compatible' else self.config.ai.model}
        if provider == 'chatgpt_bridge': patch['bridge_model'] = model.strip()
        if not patch['model']:
            raise ValueError('请填写模型名称')
        key_path = None
        try:
            if api_key:
                from .ai.key_store import save_ui_api_key
                key_path = self.config.data_dir / 'config' / 'secrets' / f'ai-key-{uuid4().hex}.txt'
                save_ui_api_key(key_path, api_key)
                patch['ui_api_key_file'] = str(key_path)
            state = self._state()
            state.update(active=True, completed=False, ai_done=True, ai_ready=False, schedule_done=False, step=4)
            return self._save(revision, state, {'ai': patch})
        except BaseException:
            if key_path: key_path.unlink(missing_ok=True)
            raise

    def test_ai(self, revision):
        self.store.check(revision)
        from .ai.factory import build_ai_client
        client = None
        try:
            cfg = self.config.model_copy(deep=True)
            cfg.ai.max_retries = 1
            cfg.ai.timeout_seconds = min(cfg.ai.timeout_seconds, 30)
            client = build_ai_client(cfg)
            response = client.chat([{'role': 'user', 'content': 'Connection test. Return JSON {"ok": true}.'}])
            if response.get('ok') is not True:
                raise ValueError('unexpected response')
        except Exception as exc:
            raise ValueError('连接失败，请检查所选渠道、模型、密钥和网络后重试') from exc
        finally:
            if client: client.close()
        state = self._state()
        state.update(active=True, completed=False, ai_done=True, ai_ready=True, schedule_done=False, step=5)
        return self._save(revision, state)

    def skip_ai(self, revision):
        state = self._state()
        state.update(active=True, completed=False, ai_done=True, ai_ready=False, schedule_done=False, step=5)
        return self._save(revision, state)

    def set_schedule(self, revision, auto_collection, auto_daily, interval_minutes, hour, minute, features_revision):
        self.store.check(revision)
        state = self._state()
        if not state.get('ai_done'):
            raise ValueError('请先设置 AI，或选择暂不配置')
        if auto_collection and self.config.ntqq.qq_number <= 0:
            raise ValueError('自动采集需要 QQ 账号；请回到数据源填写账号')
        if auto_daily and not state.get('ai_ready'):
            raise ValueError('请先测试 AI 连接，或关闭自动日报')
        worker = Archive.open(self.config.archive_path)
        try:
            features = FeatureService(worker).snapshot()
            if features['revision'] != features_revision:
                raise FeatureConflict('自动运行设置已在其他窗口更新，请重新载入')
            values = dict(features['values'], auto_collection=auto_collection, auto_daily=auto_daily)
            import json
            # Hold the DB transaction until the config replacement succeeds.
            worker.connection.execute('BEGIN IMMEDIATE')
            result = worker.connection.execute('UPDATE feature_settings SET payload=?,revision=revision+1 WHERE singleton=1 AND revision=?',
                (json.dumps(values), features_revision))
            if result.rowcount != 1:
                raise FeatureConflict('自动运行设置已在其他窗口更新，请重新载入')
            state.update(active=True, completed=False, schedule_done=True, step=6)
            return self._save_with_database(revision, state, {'collection': {'enabled': True, 'interval_minutes': interval_minutes},
                'summary': {'hour': hour, 'minute': minute}}, worker)
        finally:
            worker.close()

    def preview(self):
        state = self._state()
        ids = state.get('selected_groups', [])
        if not ids or not state.get('start_date'):
            raise ValueError('请先选择群聊并导入')
        tz = ZoneInfo(self.config.ntqq.timezone)
        start = datetime.combine(date.fromisoformat(state['start_date']), time.min, tzinfo=tz).astimezone(ZoneInfo('UTC')).isoformat()
        end = datetime.combine(date.fromisoformat(state['end_date']) + timedelta(days=1), time.min, tzinfo=tz).astimezone(ZoneInfo('UTC')).isoformat()
        names = {g.group_id: g.name for g in self.archive.all_groups()}
        groups, samples = [], []
        for gid in ids:
            count = self.archive.connection.execute('SELECT COUNT(*) FROM messages WHERE group_id=? AND timestamp>=? AND timestamp<?', (gid, start, end)).fetchone()[0]
            groups.append({'group_id': gid, 'name': names.get(gid, str(gid)), 'message_count': count})
            rows = self.archive.connection.execute('SELECT msg_id,group_id,timestamp,text FROM messages WHERE group_id=? AND timestamp>=? AND timestamp<? ORDER BY timestamp DESC,msg_id DESC LIMIT 2', (gid, start, end)).fetchall()
            samples.extend(dict(row, text=row['text'][:1000], group_name=names.get(gid, str(gid))) for row in rows)
        return {'start_date': state['start_date'], 'end_date': state['end_date'], 'timezone': self.config.ntqq.timezone,
            'total_messages': sum(g['message_count'] for g in groups), 'groups': groups, 'samples': samples[:20]}

    def finish(self, revision):
        self.store.check(revision)
        state = self._state()
        selected = state.get('selected_groups', [])
        enabled = {g.group_id for g in self.archive.enabled_groups()}
        if not selected or not set(selected) <= enabled or any(state.get('imports', {}).get(str(gid), {}).get('status') != 'success' for gid in selected):
            raise ValueError('请先完成所选群的导入，失败的群可以重试')
        if not state.get('ai_done') or not state.get('schedule_done'):
            raise ValueError('请先完成 AI 和计划设置')
        state.update(active=False, completed=True, step=6)
        return self._save(revision, state)
