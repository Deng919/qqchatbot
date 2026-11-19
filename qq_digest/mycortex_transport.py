"""Submit refined knowledge through MyCortex's local, submit-only MCP server."""
from __future__ import annotations

import json
import os
from queue import Empty, Queue
import subprocess
from contextlib import suppress
from threading import Thread
from time import monotonic

from .config import MyCortexConfig


class MyCortexUnavailable(RuntimeError):
    pass


def send_knowledge(config: MyCortexConfig, arguments: dict, *, timeout: float = 55) -> dict:
    core, store, node = config.core_dir, config.store_dir, config.node_path
    if (core is None or store is None or node is None or not node.is_file()
            or not (core / 'src' / 'entry.ts').is_file() or not store.is_dir()):
        raise MyCortexUnavailable('MyCortex 路径不可用，请检查本机连接配置')
    command = [str(node), '--import', 'tsx', 'src/entry.ts', 'knowledge', '--store', str(store),
               'serve', '--connection', config.connection_id, '--capabilities', 'submit']
    deadline = monotonic() + timeout
    try:
        process = subprocess.Popen(command, cwd=core, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except OSError as exc:
        raise MyCortexUnavailable('无法启动 MyCortex 本机提交服务') from exc

    output = Queue()
    writer = None

    def read_output():
        try:
            while line := process.stdout.readline(1024 * 1024 + 1):
                if len(line) > 1024 * 1024:
                    break
                output.put(line)
        except OSError:
            pass
        finally:
            output.put(None)

    reader = Thread(target=read_output, daemon=True)
    reader.start()

    def send(method, params, request_id=None):
        nonlocal writer
        message = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if request_id is not None:
            message['id'] = request_id
        data = json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\n'
        completed = Queue(maxsize=1)

        def write_input():
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except OSError as exc:
                completed.put(exc)
            else:
                completed.put(None)

        writer = Thread(target=write_input, daemon=True)
        writer.start()
        error = completed.get(timeout=max(0, deadline - monotonic()))
        writer.join()
        if error is not None:
            raise error

    def receive(request_id):
        while monotonic() < deadline:
            line = output.get(timeout=max(0, deadline - monotonic()))
            if line is None:
                break
            response = json.loads(line)
            if isinstance(response, dict) and response.get('id') == request_id:
                if not isinstance(response.get('result'), dict):
                    raise MyCortexUnavailable('MyCortex 拒绝了提交请求')
                return response['result']
        raise MyCortexUnavailable('MyCortex 未返回完整回执')

    try:
        send('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                            'clientInfo': {'name': 'qq-digest', 'version': '1'}}, 1)
        if receive(1).get('capabilities', {}).get('tools') is None:
            raise MyCortexUnavailable('MyCortex 未启用工具接口')
        send('notifications/initialized', {})
        send('tools/call', {'name': 'knowledge_write', 'arguments': arguments}, 2)
        result = receive(2)
        if result.get('isError'):
            raise MyCortexUnavailable('MyCortex 拒绝保存，可能是来源已删除或提交内容发生冲突')
        receipt = result.get('structuredContent')
        if receipt is None:
            receipt = json.loads(result['content'][0]['text'])
        if not isinstance(receipt, dict):
            raise MyCortexUnavailable('MyCortex 返回了无效的保存回执')
        return receipt
    except Empty as exc:
        raise MyCortexUnavailable('等待 MyCortex 回执超时，可以重试同一条消息') from exc
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise MyCortexUnavailable('MyCortex 连接中断或回执无效，可以重试同一条消息') from exc
    finally:
        if writer and writer.is_alive():
            with suppress(ProcessLookupError):
                process.kill()
            writer.join()
        with suppress(OSError):
            process.stdin.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        process.stdout.close()
