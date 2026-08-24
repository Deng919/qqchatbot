from pathlib import Path

from typer.testing import CliRunner

from qq_digest.cli import app


runner = CliRunner()


def test_hash_password():
    result = runner.invoke(app, ["hash-password", "password123"])

    assert result.exit_code == 0
    assert result.stdout.count("$") == 3
