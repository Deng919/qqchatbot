from __future__ import annotations

import base64
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


WRAPPER = Path(r"D:\CodexTools\gpttoimage-session\chat-current-account.ps1")
NODE = Path(r"C:\Users\xun\AppData\Local\Programs\nodejs\node.exe")


def _jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


def _write_fake_account(directory: Path) -> None:
    account_id = "fake-account-id"
    account = {
        "id_token": _jwt({"sub": "fake-user"}),
        "access_token": _jwt(
            {
                "exp": 4_102_444_800,
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": account_id,
                },
            }
        ),
        "refresh_token": "fake-refresh-token",
        "account_id": account_id,
        "last_refresh": datetime.now(timezone.utc).isoformat(),
    }
    (directory / "accounts.json").write_text(
        json.dumps([account]), encoding="utf-8"
    )


def _run_wrapper(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_chat_wrapper_dry_run_discovers_cockpit_accounts(tmp_path: Path):
    accounts = tmp_path / "accounts"
    accounts.mkdir()
    _write_fake_account(accounts)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}))

    result = _run_wrapper(
        "-RequestPath",
        str(request),
        "-AccountDirectory",
        str(accounts),
        "-DryRun",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["valid_accounts"] == 1
    assert "fake-refresh-token" not in result.stdout + result.stderr


def test_chat_wrapper_passes_restricted_auth_to_bridge_and_cleans_it(
    tmp_path: Path,
):
    accounts = tmp_path / "accounts"
    accounts.mkdir()
    _write_fake_account(accounts)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "Return JSON."},
                    {"role": "user", "content": "Summarize."},
                ],
                "model": "fake-gpt",
            }
        ),
        encoding="utf-8",
    )
    fake_bridge = tmp_path / "fake-bridge.js"
    fake_bridge.write_text(
        """
import fs from 'node:fs';
const authPath = process.env.CHATGPT_BRIDGE_AUTH_FILE;
const auth = JSON.parse(fs.readFileSync(authPath, 'utf8'));
if (auth.tokens.account_id !== 'fake-account-id') process.exit(9);
process.stdout.write(JSON.stringify({ok: true, text: '{\"ok\":true}', model: 'fake-gpt'}));
""".strip(),
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"

    result = _run_wrapper(
        "-RequestPath",
        str(request),
        "-AccountDirectory",
        str(accounts),
        "-RuntimeDirectory",
        str(runtime),
        "-NodePath",
        str(NODE),
        "-BridgeCliPath",
        str(fake_bridge),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["content"] == '{"ok":true}'
    assert payload["provider"] == "chatgpt_bridge"
    assert list(runtime.rglob("auth-*.json")) == []
    assert "fake-refresh-token" not in result.stdout + result.stderr


def test_chat_wrapper_preserves_sanitized_bridge_failure(tmp_path: Path):
    accounts = tmp_path / "accounts"
    accounts.mkdir()
    _write_fake_account(accounts)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
        encoding="utf-8",
    )
    fake_bridge = tmp_path / "fake-bridge.js"
    fake_bridge.write_text(
        "process.stderr.write('forced failure fake-refresh-token'); process.exit(2);",
        encoding="utf-8",
    )

    result = _run_wrapper(
        "-RequestPath",
        str(request),
        "-AccountDirectory",
        str(accounts),
        "-RuntimeDirectory",
        str(tmp_path / "runtime"),
        "-NodePath",
        str(NODE),
        "-BridgeCliPath",
        str(fake_bridge),
    )

    assert result.returncode == 1
    assert "forced failure" in result.stderr
    assert "fake-refresh-token" not in result.stderr
