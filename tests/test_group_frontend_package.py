from scripts.build_desktop import TEMPLATES


def test_installer_bundles_shared_group_selection_and_batch_controls():
    assert {'group_selector.html', 'group_batch_feedback.html', 'group_batch_controls.html'} <= set(TEMPLATES)
