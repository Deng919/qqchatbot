"""Build an isolated Windows release without copying private project data."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CACHE = Path(r"D:\Cache\QQDigestDesktop")
RELEASE = Path(r"D:\Apps\QQDigestDesktop")
TEMPLATES = (
    "ai_settings.html", "base.html", "candidates.html", "collect.html",
    "catchup.html", "dashboard.html", "failures.html", "groups.html", "login.html", "reports.html", "search.html", "tasks.html",
    "settings.html", "summary_reading_controls.html", "summary_reading_style.html",
    "summary_reading_script.html",
    "summary_generation_script.html",
    "summary_progress_script.html",
    "primary_navigation.html", "section_navigation.html", "group_details.html",
    "ai_connection_settings.html", "settings_categories_script.html",
    "find_style.html", "find_helpers.html", "find_script.html",
    "message_context_script.html",
    "knowledge.html", "knowledge_script.html", "knowledge_save.html",
    "topics.html", "topics_script.html",
    "reminders.html", "reminders_script.html",
    "bookmarks.html", "bookmarks_script.html", "bookmark_actions.html",
    "first_use.html", "first_use_script.html",
    "desktop_updates.html", "desktop_updates_script.html",
)


def build_command(cache: Path, *, public=False, version='0.2.0-beta.1') -> list[str]:
    cache.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--onedir", "--windowed", "--noconfirm", "--name", "QQDigestDesktop",
        "--paths", str(PROJECT),
        "--workpath", str(cache / "build"),
        "--distpath", str(cache / "dist"),
        "--specpath", str(cache / "spec"),
    ]
    if public:
        profile = cache / 'distribution.json'
        profile.write_text(json.dumps({'edition': 'deepseek-only', 'version': version}), encoding='utf-8')
        hook = cache / 'public_runtime_hook.py'
        hook.write_text('import sys\nsys._qq_digest_public_build = True\n', encoding='utf-8')
        command.extend(['--exclude-module', 'qq_digest.ai.bridge', '--add-data', f'{profile};qq_digest', '--runtime-hook', str(hook)])
    template_dir = PROJECT / "qq_digest" / "web" / "templates"
    for name in TEMPLATES:
        source = template_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"required template not found: {source}")
        command.extend(["--add-data", f"{source};qq_digest/web/templates"])
    command.append(str(PROJECT / "scripts" / "desktop_entry.py"))
    return command


def finalize_release(release: Path, config: Path, *, public=False, version='0.2.0-beta.1'):
    from qq_digest.desktop import DESKTOP_BACKEND_ID
    from qq_digest.desktop_releases import ReleaseRegistry, write_release
    from qq_digest.desktop_updates import STATE_DIR
    if public:
        for source, name in [(PROJECT / 'LICENSE', 'LICENSE.txt'), (PROJECT / 'docs' / 'PUBLIC_BETA.md', '使用说明.md')]:
            shutil.copyfile(source, release / name)
        write_release(release, version, DESKTOP_BACKEND_ID + '-deepseek-only', compatibility='archive-2026-10-06-deepseek-only')
        return
    (release / "launcher.json").write_text(
        json.dumps({"config_path": str(config)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_release(release, release.name, DESKTOP_BACKEND_ID)
    ReleaseRegistry(Path(r'D:\Apps'), STATE_DIR).register(release)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build QQ Digest Desktop for Windows")
    parser.add_argument("--config", type=Path, default=PROJECT / "config" / "config.yaml")
    parser.add_argument("--release-dir", type=Path, default=RELEASE)
    parser.add_argument('--public', action='store_true')
    parser.add_argument('--version', default='0.2.0-beta.1')
    args = parser.parse_args()
    config = args.config.resolve()
    release = args.release_dir.resolve()
    if not args.public and not config.is_file():
        parser.error(f"config file does not exist: {config}")
    if release.exists():
        parser.error(f"release directory already exists: {release}")
    if not release.is_relative_to(Path(r"D:\Apps")):
        parser.error("release directory must be within D:\\Apps")
    cache = (CACHE.parent / 'QQDigestPublicBeta-2026-10-06' if args.public else CACHE).resolve()
    built = cache / 'dist' / 'QQDigestDesktop'
    command = build_command(cache, public=args.public, version=args.version)
    subprocess.run(command, cwd=PROJECT, check=True)
    if not (built / "QQDigestDesktop.exe").is_file():
        raise RuntimeError(f"built executable not found: {built}")
    release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, release)
    finalize_release(release, config, public=args.public, version=args.version)
    print(release / "QQDigestDesktop.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
