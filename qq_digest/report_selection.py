"""Validate the shared single- or multi-group reading selection."""
from .group_selection import normalize_group_selection


def selected_report_groups(group_id=None, group_ids=None) -> tuple[int, ...]:
    selected = normalize_group_selection(group_id, group_ids)
    if any(value <= 0 for value in selected):
        raise ValueError('群号超出有效范围')
    return selected
