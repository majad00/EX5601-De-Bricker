from pathlib import Path
import sys


def app_dir() -> Path:
    """
    Directory containing the running script, or the compiled EXE.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """
    PyInstaller onefile extraction directory, if running frozen.
    Used only for embedded fallback files.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()

    return app_dir()


def find_payload_file(filename: str) -> Path:
    """
    Search order:
      1. Same folder as EXE/script
      2. payloads/ beside EXE/script
      3. embedded PyInstaller bundle folder
      4. embedded payloads/ folder
    """

    candidates = [
        app_dir() / filename,
        app_dir() / "payloads" / filename,
        bundle_dir() / filename,
        bundle_dir() / "payloads" / filename,
    ]

    for path in candidates:
        if path.exists():
            return path

    searched = "\n".join(str(p) for p in candidates)

    raise FileNotFoundError(
        f"Could not find required file: {filename}\n\n"
        f"Searched:\n{searched}"
    )


def read_payload_file(filename: str) -> bytes:
    path = find_payload_file(filename)

    with open(path, "rb") as f:
        return f.read()