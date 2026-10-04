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
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build QQ Digest Desktop for Windows")
    parser.add_argument("--config", type=Path, default=PROJECT / "config" / "config.yaml")
    parser.add_argument("--release-dir", type=Path, default=RELEASE)
    args = parser.parse_args()
    config = args.config.resolve()
    release = args.release_dir.resolve()
    if not config.is_file():
        parser.error(f"config file does not exist: {config}")
    if release.exists():
        parser.error(f"release directory already exists: {release}")
    if not release.is_relative_to(Path(r"D:\Apps")):
        parser.error("release directory must be within D:\\Apps")

    cache = CACHE.resolve()
    built = (cache / "dist" / "QQDigestDesktop").resolve()
    if not built.is_relative_to(cache):
        parser.error("build output must stay within D:\\Cache\\QQDigestDesktop")
    cache.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--onedir", "--windowed", "--noconfirm", "--name", "QQDigestDesktop",
        "--paths", str(PROJECT),
        "--workpath", str(cache / "build"),
        "--distpath", str(cache / "dist"),
        "--specpath", str(cache / "spec"),
    ]
    template_dir = PROJECT / "qq_digest" / "web" / "templates"
    for name in TEMPLATES:
        source = template_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"required template not found: {source}")
        command.extend(["--add-data", f"{source};qq_digest/web/templates"])
    command.append(str(PROJECT / "scripts" / "desktop_entry.py"))
    subprocess.run(command, cwd=PROJECT, check=True)
    if not (built / "QQDigestDesktop.exe").is_file():
        raise RuntimeError(f"built executable not found: {built}")
    release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, release)
    (release / "launcher.json").write_text(
        json.dumps({"config_path": str(config)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(release / "QQDigestDesktop.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
