from pathlib import Path


LOG_DIR = Path('/data/log')


def clear_log_files() -> None:
  """Start a fresh manager session, including logs that were not uploaded."""
  for path in LOG_DIR.iterdir():
    if path.is_file() or path.is_symlink():
      path.unlink(missing_ok=True)
