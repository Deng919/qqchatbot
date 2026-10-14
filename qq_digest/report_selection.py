"""Validate the shared single- or multi-group reading selection."""
from .group_selection import normalize_group_selection
import re


def selected_report_groups(group_id=None, group_ids=None) -> tuple[int, ...]:
    selected = normalize_group_selection(group_id, group_ids)
    if any(value <= 0 for value in selected):
        raise ValueError('群号超出有效范围')
    return selected


def selected_report_keys(keys=None) -> tuple[str, ...]:
    if keys is None:return ()
    if not keys or len(keys)>1000:raise ValueError('摘要标识需要1至1000个值')
    for key in keys:
        if not isinstance(key,str) or not re.fullmatch(r'(daily|range):[1-9][0-9]{0,18}',key):
            raise ValueError('摘要标识无效')
        if int(key.split(':')[1])>2**63-1:raise ValueError('摘要标识超出范围')
    return tuple(dict.fromkeys(keys))
