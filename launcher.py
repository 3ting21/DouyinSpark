"""PyInstaller entry point that preserves the douyin_spark package context."""

from douyin_spark.main import main


if __name__ == "__main__":
    raise SystemExit(main())
