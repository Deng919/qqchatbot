import json
import subprocess
from pathlib import Path

import pytest

from qq_digest.ai.bridge import BridgeAIClient
from qq_digest.ai.client import AIError


def test_bridge_sends_images_to_existing_pool_entry_without_request_files(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    wrapper=tmp_path/'chat-current-account.ps1';wrapper.write_text('')
    (tmp_path/'invoke-pool-mcp.ps1').write_text('')
    cli=tmp_path/'gpttoimage/chatgpt-bridge/node_modules/chatgpt-bridge/dist/cli.js'
    cli.parent.mkdir(parents=True);cli.write_text('')
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return subprocess.CompletedProcess(command,0,json.dumps({'content':[{'type':'text','text':'{"text":"出售显示器 800元"}'}]}).encode(),b'')
    client=BridgeAIClient(wrapper_path=wrapper,account_directory=tmp_path/'accounts',request_directory=tmp_path,runner=run)
    messages=[{'role':'user','content':[{'type':'text','text':'读取图片'},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]}]
    assert client.chat(messages)=={'text':'出售显示器 800元'}
    command,kwargs=calls[0];assert str(tmp_path/'invoke-pool-mcp.ps1') in command
    request=json.loads(kwargs['input']);assert request['arguments']['attachments']==['data:image/png;base64,YQ==']
    assert request['arguments']['prompt']=='读取图片' and 'model' not in request['arguments']
    assert not list(tmp_path.glob('qq-digest-chat-*'))
    assert kwargs['creationflags']==getattr(subprocess,'CREATE_NO_WINDOW',0)


def test_bridge_image_errors_are_generic_and_do_not_silently_drop_images(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    client=BridgeAIClient(wrapper_path=tmp_path/'old.ps1',account_directory=tmp_path)
    with pytest.raises(AIError,match='多模态'):
        client.chat([{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://bad.example/image.png'}}]}])
