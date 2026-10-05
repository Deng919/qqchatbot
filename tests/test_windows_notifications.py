import threading


class Native:
    def __init__(self, accepted=True):
        self.calls = []
        self.accepted = accepted
    def create(self):
        self.calls.append('create')
        return object()
    def add(self, handle):
        self.calls.append('add')
        return True
    def version(self, handle):
        self.calls.append('version')
        return True
    def show(self, handle, title, body):
        self.calls.append(('show', title, body))
        return self.accepted
    def pump(self):
        pass
    def delete(self, handle):
        self.calls.append('delete')
    def destroy(self, handle):
        self.calls.append('destroy')


def test_native_request_keeps_owner_until_close_and_cleans_up():
    from qq_digest.windows_notifications import WindowsNotificationSink
    native = Native()
    sink = WindowsNotificationSink(native_factory=lambda: native)
    assert sink('提醒', '共有 2 条提醒') is True
    assert native.calls[:3] == ['create', 'add', 'version']
    assert 'delete' not in native.calls
    sink.close()
    assert native.calls[-2:] == ['delete', 'destroy']
    assert sink('提醒', '关闭后') is False


def test_native_failure_and_utf16_limits():
    from qq_digest.windows_notifications import WindowsNotificationSink
    native = Native(accepted=False)
    sink = WindowsNotificationSink(native_factory=lambda: native)
    assert sink('😀'*100, '😀'*300) is False
    sink.close()
    show = next(call for call in native.calls if isinstance(call, tuple))
    assert len(show[1].encode('utf-16-le')) // 2 <= 63
    assert len(show[2].encode('utf-16-le')) // 2 <= 255
    assert native.calls[-2:] == ['delete', 'destroy']


def test_native_start_timeout_is_unknown_not_false_acceptance():
    import time
    from qq_digest.windows_notifications import WindowsNotificationSink
    native=Native()
    original=native.create
    def slow_create():
        time.sleep(.05)
        return original()
    native.create=slow_create
    sink=WindowsNotificationSink(native_factory=lambda: native, startup_timeout=.01)
    assert sink('提醒','未知请求') is None
    sink.close()
    assert native.calls[-2:] == ['delete','destroy']
