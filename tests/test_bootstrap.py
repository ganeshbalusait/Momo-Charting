from pathlib import Path

from scripts.bootstrap import ensure_env_file


def test_ensure_env_file_creates_from_example(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".env.example").write_text("FOO=bar\n", encoding="utf-8")

    env_path = ensure_env_file(repo_root)

    assert env_path.exists()
    assert env_path.read_text(encoding="utf-8") == "FOO=bar\n"
