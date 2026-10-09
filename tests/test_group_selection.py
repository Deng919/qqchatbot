import importlib

import pytest


def normalize(*args, **kwargs):
    return importlib.import_module('qq_digest.group_selection').normalize_group_selection(*args, **kwargs)


def test_absent_selection_means_all_and_required_needs_selection():
    assert normalize() == ()
    with pytest.raises(ValueError):
        normalize(required=True)


def test_selection_deduplicates_preserving_order_and_signed_query_ids():
    assert normalize(group_ids=[3, 1, 3, -2, 0]) == (3, 1, -2, 0)
    assert normalize(group_id=-2**63) == (-2**63,)
    assert normalize(group_ids=[1] * 101) == (1,)


@pytest.mark.parametrize('kwargs', [
    {'group_ids': []}, {'group_id': 1, 'group_ids': [1]},
    {'group_id': True}, {'group_ids': [False]}, {'group_ids': ['1']},
    {'group_ids': [1.0]}, {'group_id': 2**63}, {'group_id': -2**63-1},
    {'group_ids': list(range(101))}, {'group_ids': [1] * 1001},
    {'group_ids': '12'}, {'group_ids': 1},
])
def test_invalid_selection_is_rejected(kwargs):
    with pytest.raises(ValueError):
        normalize(**kwargs)


def test_report_selection_shares_unique_cap_and_keeps_positive_ids():
    from qq_digest.report_selection import selected_report_groups
    assert selected_report_groups(group_ids=[11] * 101 + [33]) == (11, 33)
    assert selected_report_groups() == ()
    for scope in ({'group_id': 0}, {'group_ids': [-1]}, {'group_ids': []},
                  {'group_id': 11, 'group_ids': [33]}):
        with pytest.raises(ValueError):
            selected_report_groups(**scope)
