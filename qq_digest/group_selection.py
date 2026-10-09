"""Validate a query's explicit group selection without widening empty scopes."""
from collections.abc import Sequence


def normalize_group_selection(group_id=None, group_ids=None, *, required=False) -> tuple[int, ...]:
    if group_id is not None and group_ids is not None:
        raise ValueError('群聊筛选不能同时指定单群和多个群')
    values = [group_id] if group_id is not None else group_ids
    if values is None:
        if required:
            raise ValueError('请至少选择一个群')
        return ()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError('群聊筛选需要群号列表')
    if not values or len(values) > 1000:
        raise ValueError('群聊筛选需要 1 至 100 个不同的群，提交数量不能超过 1000')
    if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in values):
        raise ValueError('群号超出有效范围')
    selected = tuple(dict.fromkeys(values))
    if len(selected) > 100:
        raise ValueError('群聊筛选不能超过 100 个不同的群')
    return selected
