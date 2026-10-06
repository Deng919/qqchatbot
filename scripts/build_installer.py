"""Compile the public WinForms installer; never embeds local configuration."""
from pathlib import Path
import argparse
import hashlib
import subprocess
import zipfile
import json

VERSION = '0.2.0-beta.1'
COMPILER = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')

def build(payload: Path, output: Path, cache: Path):
    if output.exists():
        raise FileExistsError(output)
    if not (payload / 'QQDigestDesktop.exe').is_file():
        raise ValueError('Missing desktop executable')
    marker = json.loads((payload / '_internal/qq_digest/distribution.json').read_text(encoding='utf-8'))
    manifest = json.loads((payload / 'release.json').read_text(encoding='utf-8'))
    if marker != {'edition': 'deepseek-only', 'version': VERSION} or manifest.get('product') != 'qq-digest-desktop' or manifest.get('version') != VERSION or manifest.get('compatibility') != 'archive-2026-10-06-deepseek-only':
        raise ValueError('Only a matching public DeepSeek release can be packaged')
    files = sorted(p for p in payload.rglob('*') if p.is_file())
    for p in payload.rglob('*'):
        if p.is_symlink() or p.is_junction():
            raise ValueError('Payload links are forbidden')
    forbidden = {'launcher.json', 'config.yaml', 'session-secret.txt', 'deepseek-api-key.txt'}
    if any(p.name.lower() in forbidden for p in files):
        raise ValueError('Payload contains mutable configuration or credentials')
    actual = {p.relative_to(payload).as_posix(): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size_bytes': p.stat().st_size} for p in files if p.name != 'release.json'}
    if manifest.get('files') != actual:
        raise ValueError('Release file integrity check failed')
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / 'public-payload.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(payload).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    source = Path(__file__).with_name('windows_installer.cs').read_text(encoding='utf-8')
    generated = cache / 'windows_installer.generated.cs'
    generated.write_text(source.replace('__PAYLOAD_SHA256__', digest), encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(COMPILER), '/nologo', '/target:winexe', '/platform:x64',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
        '/reference:System.Web.Extensions.dll', '/resource:' + str(archive) + ',payload.zip',
        '/out:' + str(output), str(generated)], check=True)
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(sha + '  ' + output.name + '\n', encoding='ascii')
    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload', type=Path, default=Path(r'D:\Apps\QQDigestDesktop-' + VERSION))
    parser.add_argument('--output', type=Path, default=Path(r'D:\Downloads\QQDigest-' + VERSION + '-Windows-x64-Setup.exe'))
    parser.add_argument('--cache', type=Path, default=Path(r'D:\Cache\QQDigestPublicBeta-2026-10-06\installer'))
    args = parser.parse_args()
    print(build(args.payload, args.output, args.cache))
