"""Validate the shared single- or multi-group reading selection."""


def selected_report_groups(group_id=None, group_ids=None) -> tuple[int, ...]:
    if group_id is not None and group_ids is not None:
        raise ValueError('群聊筛选不能同时指定单群和多个群')
    values = [group_id] if group_id is not None else group_ids
    if values is None:
        return ()
    if not values or len(values) > 100:
        raise ValueError('群聊筛选需要 1 至 100 个群')
    if any(type(value) is not int or not 0 < value < 2**63 for value in values):
        raise ValueError('群号超出有效范围')
    return tuple(dict.fromkeys(values))
