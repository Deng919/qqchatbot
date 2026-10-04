"""Bounded, thread-safe progress snapshots for local summary generation."""
from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from time import monotonic


class ProgressExists(ValueError):
    pass


class SummaryProgress:
    def __init__(self):
        self._lock = Lock()
        self._runs = OrderedDict()

    def start(self, identifier, scope):
        with self._lock:
            if identifier in self._runs:
                raise ProgressExists('本次生成已提交，请查看已有进度；重新生成请使用新的请求')
            self._runs[identifier] = dict(
                generation_id=identifier, scope=deepcopy(scope), status='running',
                stage='preparing', current_group=None, total=len(scope['group_ids']),
                completed=0, counts=dict(created=0, reused=0, skipped=0, failed=0),
                groups=[], started=monotonic(), result=None, error='',
            )
            while len(self._runs) > 20:
                self._runs.popitem(last=False)

    def update(self, identifier, *, group_id, group_name, stage):
        with self._lock:
            run = self._runs[identifier]
            run['stage'] = stage
            run['current_group'] = dict(group_id=group_id, group_name=group_name)
            if stage in run['counts'] and not any(g['group_id'] == group_id for g in run['groups']):
                run['counts'][stage] += 1
                run['completed'] += 1
                run['groups'].append(dict(group_id=group_id, group_name=group_name, outcome=stage))

    def finish(self, identifier, *, result=None, error=''):
        with self._lock:
            run = self._runs[identifier]
            run['status'] = result['status'] if result is not None else 'failed'
            run['stage'] = 'finished'
            run['result'] = deepcopy(result)
            run['error'] = error
            run['finished'] = monotonic()

    def snapshot(self, identifier=None):
        with self._lock:
            if not identifier:
                identifier = next(reversed(self._runs), None)
            if identifier not in self._runs:
                return None
            run = deepcopy(self._runs[identifier])
            run['elapsed_seconds'] = int(run.pop('finished', monotonic()) - run.pop('started'))
            return run
