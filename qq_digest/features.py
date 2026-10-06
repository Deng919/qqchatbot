"""Persistent optional feature choices, independent of user content."""
from __future__ import annotations

import json


FEATURES = {
    'catchup': ('跨群补看', '各群日报与已读记录', '扩展功能'),
    'tasks': ('待办收件箱', '负责人、截止日期和完成状态', '扩展功能'),
    'failures': ('异常处理', '失败记录与重试', '扩展功能'),
    'review': ('审核与知识整理', '确认候选后入库；关闭不影响已有知识', '扩展功能'),
    'report_qa': ('摘要问答', '回答附原消息来源', '扩展功能'),
    'report_revisions': ('版本与纠错', '历史版本、纠错和重新生成', '扩展功能'),
    'topics': ('话题跟踪', '跨天、跨群的讨论与进展', '扩展功能'),
    'reminders': ('提醒规则', '关键词、资源、到期及故障通知', '扩展功能'),
    'bookmarks': ('收藏与稍后处理', '收藏来源、待处理与已完成', '扩展功能'),
    'history_inspection': ('历史缺口巡检', '关闭同时暂停自动巡检', '扩展功能'),
    'auto_collection': ('自动采集', '定时更新消息；摘要生成另行读取消息', '自动任务'),
    'auto_daily': ('每天自动生成摘要', '定时生成和补齐日报', '自动任务'),
}
SIMPLE_VALUES = {key: False for key in FEATURES if not key.startswith('auto_')}


class FeatureConflict(ValueError):
    pass


class FeatureService:
    def __init__(self, archive):
        self.archive = archive

    def snapshot(self):
        row = self.archive.connection.execute('SELECT revision,payload FROM feature_settings WHERE singleton=1').fetchone()
        saved = json.loads(row['payload'])
        values = {key: saved.get(key, key not in {'topics', 'reminders'}) for key in FEATURES}
        return {'revision': row['revision'], 'values': values,
                'catalog': [{'key': key, 'title': value[0], 'description': value[1], 'group': value[2]}
                            for key, value in FEATURES.items() if key != 'catchup']}

    def enabled(self, name):
        if name not in FEATURES:
            raise ValueError('未知功能')
        return self.snapshot()['values'][name]

    def update(self, values, expected_revision):
        if (not isinstance(values, dict) or not values or set(values) - FEATURES.keys()
                or any(type(value) is not bool for value in values.values())):
            raise ValueError('请选择有效功能，并使用开启或关闭状态')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('无效的设置版本')
        with self.archive.transaction():
            state = self.snapshot()
            if state['revision'] != expected_revision:
                raise FeatureConflict('功能设置已在其他窗口更新，请刷新后重试')
            state['values'].update(values)
            result = self.archive.connection.execute(
                'UPDATE feature_settings SET payload=?,revision=revision+1 WHERE singleton=1 AND revision=?',
                (json.dumps(state['values']), expected_revision))
            if result.rowcount != 1:
                raise FeatureConflict('功能设置已更新，请刷新后重试')
        return self.snapshot()
