"""Persistent optional feature choices, independent of user content."""
from __future__ import annotations

import json


FEATURES = {
    'catchup': ('跨群补看', '汇集各群日报要点，并记录已读状态。', '扩展功能'),
    'tasks': ('待办收件箱', '确认摘要中的任务，跟踪负责人和截止日期。', '扩展功能'),
    'failures': ('异常处理', '集中查看失败原因并重试任务。', '扩展功能'),
    'review': ('审核与知识整理', '人工确认 AI 提取的候选并写入知识库；关闭仍可浏览知识库和手动保存原消息。', '扩展功能'),
    'report_qa': ('摘要问答', '围绕报告向 AI 提问，回答附消息来源。', '扩展功能'),
    'report_revisions': ('版本与纠错', '查看旧版本、保存纠错意见并按原范围重新生成。', '扩展功能'),
    'history_inspection': ('历史缺口巡检', '检查多群历史缺口；关闭同时暂停自动巡检。', '扩展功能'),
    'auto_collection': ('自动采集', '暂停或开启定时采集；每天自动生成摘要仍会按自己的流程读取消息，手动采集可用。', '自动任务'),
    'auto_daily': ('每天自动生成摘要', '按计划生成和补生成各群的单日摘要；关闭后仍可按日期手动生成。', '自动任务'),
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
        values = {key: saved.get(key, True) for key in FEATURES}
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
