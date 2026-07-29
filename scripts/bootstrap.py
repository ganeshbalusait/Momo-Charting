from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_FILE = REPO_ROOT / ".env"


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def ensure_env_file(repo_root: Path | None = None) -> Path:
    root = repo_root or REPO_ROOT
    env_example = root / ".env.example"
    env_path = root / ".env"

    if env_path.exists():
        return env_path

    if env_example.exists():
        env_path.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        env_path.write_text("", encoding="utf-8")

    return env_path


def ensure_python_venv(repo_root: Path | None = None) -> Path:
    root = repo_root or REPO_ROOT
    venv_dir = root / ".venv"
    if venv_dir.exists():
        return venv_dir

    python_exe = shutil.which("python") or shutil.which("python3")
    if not python_exe:
        raise RuntimeError("Python interpreter not found on this machine")

    import subprocess

    subprocess.run([python_exe, "-m", "venv", str(venv_dir)], check=True)
    return venv_dir


def ensure_python_dependencies(repo_root: Path | None = None, *, python_exe: str | None = None) -> None:
    root = repo_root or REPO_ROOT
    if python_exe is None:
        if sys.platform.startswith("win"):
            python_exe = str(root / ".venv" / "Scripts" / "python.exe")
        else:
            python_exe = str(root / ".venv" / "bin" / "python")

    import subprocess

    subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([python_exe, "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)


def ensure_node_dependencies(repo_root: Path | None = None) -> None:
    root = repo_root or REPO_ROOT
    frontend_dir = root / "frontend"
    if not frontend_dir.exists():
        return

    package_manager = None
    if shutil.which("pnpm"):
        package_manager = [shutil.which("pnpm"), "install", "--frozen-lockfile"]
    elif shutil.which("npm"):
        package_manager = [shutil.which("npm"), "install"]
    else:
        print("Skipping frontend dependency install: neither pnpm nor npm is available on this machine.")
        return

    import subprocess

    try:
        subprocess.run(package_manager, cwd=frontend_dir, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Skipping frontend dependency install: {exc}")


def bootstrap_repo(repo_root: Path | None = None) -> dict[str, Path | None]:
    root = repo_root or REPO_ROOT
    env_path = ensure_env_file(root)
    venv_dir = ensure_python_venv(root)
    ensure_python_dependencies(root)
    ensure_node_dependencies(root)
    return {"env": env_path, "venv": venv_dir}


if __name__ == "__main__":
    bootstrap_repo()
