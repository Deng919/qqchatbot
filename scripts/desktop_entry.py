"""PyInstaller entry point with an absolute package import."""

from qq_digest.desktop import main


if __name__ == "__main__":
    raise SystemExit(main())
