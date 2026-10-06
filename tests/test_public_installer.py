import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import shutil
import hashlib
import zipfile
import pytest
from qq_digest.web.auth import PasswordHasher

spec = importlib.util.spec_from_file_location('build_installer', Path(__file__).parents[1] / 'scripts/build_installer.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

@pytest.fixture(scope='module')
def installer():
    if not builder.COMPILER.exists():
        pytest.skip('Windows .NET compiler required')
    root = Path(r'D:\Cache\QQDigestPublicBeta-2026-10-06\installer-tests')
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='synthetic-', dir=root))
    payload = work / 'payload'
    payload.mkdir()
    (payload / 'QQDigestDesktop.exe').write_bytes(b'synthetic test payload')
    (payload / '_internal/qq_digest').mkdir(parents=True)
    (payload / '_internal/qq_digest/distribution.json').write_text(json.dumps({'edition': 'deepseek-only', 'version': builder.VERSION}), encoding='utf-8')
    from qq_digest.desktop_releases import write_release
    write_release(payload, builder.VERSION, 'test-backend', compatibility='archive-2026-10-06-deepseek-only')
    executable = builder.build(payload, work / 'Setup.exe', work / 'build')
    yield executable, work
    shutil.rmtree(work)

def run_install(exe, program, data, password='synthetic-password-123'):
    return subprocess.run([str(exe), '--test-install', str(program), str(data)],
                          input=password+'\n', text=True, capture_output=True).returncode

def test_synthetic_install_and_password_contract(installer):
    exe, work = installer
    program, data = work / 'installed', work / 'userdata'
    assert run_install(exe, program, data) == 0
    config = json.loads((data / 'config/config.yaml').read_text(encoding='utf-8'))
    assert PasswordHasher.verify('synthetic-password-123', config['security']['web_password_hash'])
    assert len((data / 'session-secret.txt').read_text()) == 64
    acl = subprocess.run(['powershell', '-NoProfile', '-Command',
                          "$a = [IO.Directory]::GetAccessControl('" + str(data).replace("'", "''") + "'); if (-not $a.AreAccessRulesProtected -or $a.Access.Count -ne 3) { exit 1 }"],
                         capture_output=True, text=True)
    assert acl.returncode == 0
    assert config['ai']['provider_priority'] == ['compatible']
    assert config['ai']['base_url'] == 'https://api.deepseek.com'
    assert not any('bridge' in key for key in config['ai'])
    assert config['ai']['api_key_file'] == ''
    assert config['ai']['ui_api_key_file'] == str(data / 'secrets/deepseek-api-key.txt')
    assert not Path(config['ai']['ui_api_key_file']).exists()
    assert json.loads((program / 'launcher.json').read_text())['config_path'] == str(data / 'config/config.yaml')
    assert run_install(exe, program, data) != 0
    assert (program / 'QQDigestDesktop.exe').read_bytes() == b'synthetic test payload'

def test_rejects_weak_password_and_nested_paths(installer):
    exe, work = installer
    assert run_install(exe, work / 'weak', work / 'weakdata', 'short') != 0
    assert not (work / 'weak').exists()
    assert run_install(exe, work / 'nested', work / 'nested/data') != 0
    assert not (work / 'nested').exists()

def test_data_folder_named_config_stays_inside_selected_root(installer):
    exe, work = installer
    program, data = work / 'named-program', work / 'config'
    assert run_install(exe, program, data) == 0
    from qq_digest.config import load_config
    loaded = load_config(data / 'config/config.yaml')
    assert loaded.data_dir == data.resolve()
    for name in ['archive_path', 'report_dir', 'knowledge_dir', 'work_dir', 'log_dir']:
        assert getattr(loaded, name).is_relative_to(data.resolve())

def test_parallel_invocation_fails_without_creating_or_deleting_dirs(installer):
    exe, work = installer
    holder = subprocess.Popen(['powershell', '-NoProfile', '-Command',
        r"$m = New-Object System.Threading.Mutex($false, 'Global\QQDigestPublicBetaInstaller'); $null = $m.WaitOne(); [Console]::WriteLine('ready'); [Console]::Out.Flush(); $null = [Console]::ReadLine(); $m.ReleaseMutex()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'ready'
        program, data = work / 'parallel-program', work / 'parallel-data'
        assert run_install(exe, program, data) != 0
        assert not program.exists() and not data.exists()
    finally:
        holder.communicate('\n', timeout=10)
    assert run_install(exe, program, data) == 0

def test_builder_refuses_mutable_config(installer):
    _, work = installer
    (work / 'payload/launcher.json').write_text('{}')
    with pytest.raises(ValueError, match='mutable'):
        builder.build(work / 'payload', work / 'unsafe.exe', work / 'unsafe-build')

@pytest.mark.parametrize('tampered', [False, True])
def test_rejects_traversal_or_failed_integrity(installer, tampered):
    _, work = installer
    suffix = 'tampered' if tampered else 'traversal'
    archive = work / (suffix + '.zip')
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escaped.txt', 'must never escape')
    digest = '0' * 64 if tampered else hashlib.sha256(archive.read_bytes()).hexdigest()
    source = work / (suffix + '.cs')
    source.write_text(Path(__file__).parents[1].joinpath('scripts/windows_installer.cs').read_text(encoding='utf-8').replace('__PAYLOAD_SHA256__', digest), encoding='utf-8')
    exe = work / (suffix + '.exe')
    subprocess.run([str(builder.COMPILER), '/nologo', '/target:winexe', '/platform:x64',
                    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                    '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
                    '/reference:System.Web.Extensions.dll', '/resource:' + str(archive) + ',payload.zip',
                    '/out:' + str(exe), str(source)], check=True, capture_output=True)
    assert run_install(exe, work / (suffix + '-program'), work / (suffix + '-data')) != 0
    assert not (work / (suffix + '-program')).exists()
    assert not (work / (suffix + '-data')).exists()
    assert not (work / 'escaped.txt').exists()

@pytest.mark.parametrize('change', ['marker', 'compatibility', 'bytes'])
def test_builder_refuses_nonpublic_or_changed_release(installer, change):
    _, work = installer
    payload = work / ('policy-' + change)
    payload.mkdir()
    (payload / 'QQDigestDesktop.exe').write_bytes(b'synthetic')
    marker = payload / '_internal/qq_digest/distribution.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'edition': 'deepseek-only', 'version': builder.VERSION}), encoding='utf-8')
    from qq_digest.desktop_releases import write_release
    write_release(payload, builder.VERSION, 'test', compatibility='archive-2026-10-06-deepseek-only')
    if change == 'marker':
        marker.write_text('{}')
    elif change == 'compatibility':
        manifest = json.loads((payload / 'release.json').read_text(encoding='utf-8'))
        manifest['compatibility'] = 'archive-2026-10-06'
        (payload / 'release.json').write_text(json.dumps(manifest))
    else:
        (payload / 'QQDigestDesktop.exe').write_bytes(b'changed')
    with pytest.raises(ValueError):
        builder.build(payload, work / (change + '-unsafe.exe'), work / (change + '-build'))
