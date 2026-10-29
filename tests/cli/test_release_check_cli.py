"""`scripts/release_check.py --fast`: the demo freeze in throwaway git repositories."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import release_check

ROOT = Path(__file__).resolve().parents[2]
FILES = {
    "vs/catalog/catalog.json": "{}\n",
    "sql/core/x.sql": "CREATE TABLE X (A INT);\n",
    "sql/meta/load_log.sql": "-- load log\n",
    "sql/meta/fetch_log.sql": "-- fetch log\n",
    "loader/load-plan.yaml": "steps: []\n",
    "README.md": "not frozen\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for name, text in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def run(repo: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, "-m", "scripts.release_check", "--root", str(repo), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return done.returncode, done.stdout + done.stderr


def freeze(repo: Path) -> None:
    code, out = run(repo, "freeze", "--from", "2026-11-01", "--until", "2026-11-21")
    assert code == 0, out


def test_change_fails_during_the_freeze_and_passes_after_or_reverted(repo: Path) -> None:
    freeze(repo)
    (repo / "sql/core/x.sql").write_text("CREATE TABLE X (A INT, B INT);\n")  # uncommitted
    code, out = run(repo, "--fast", "--now", "2026-11-10")
    assert code == 2
    assert "FAIL freeze sql/core:" in out and "pinned " in out and "owner" in out
    assert "vs/catalog" not in out
    code, out = run(repo, "--fast", "--now", "2026-11-25")
    assert code == 0 and "WARN freeze demo/freeze.yaml" in out
    (repo / "sql/core/x.sql").write_text(FILES["sql/core/x.sql"])
    code, out = run(repo, "--fast", "--now", "2026-11-10")
    assert code == 0 and "5 paths unchanged" in out


def test_new_untracked_file_and_unfrozen_change(repo: Path) -> None:
    freeze(repo)
    (repo / "README.md").write_text("changed\n")
    assert run(repo, "--fast", "--now", "2026-11-01")[0] == 0
    (repo / "sql/core/y.sql").write_text("CREATE TABLE Y (A INT);\n")
    code, out = run(repo, "--fast", "--now", "2026-11-21")
    assert code == 2 and "FAIL freeze sql/core:" in out


def test_no_file_before_start_and_refused_overwrite(repo: Path) -> None:
    assert run(repo, "--fast")[0] == 0
    freeze(repo)
    (repo / "loader/load-plan.yaml").write_text("steps: [1]\n")
    assert run(repo, "--fast", "--now", "2026-10-31")[0] == 0
    code, out = run(repo, "freeze", "--from", "2026-11-01", "--until", "2026-11-21")
    assert code == 1 and "--force" in out
    assert run(repo, "freeze", "--from", "2026-11-01", "--until", "2026-11-21", "--force")[0] == 0
    assert run(repo, "--fast", "--now", "2026-11-10")[0] == 0


def test_digest_ignores_ignored_files(repo: Path) -> None:
    before = release_check.digest(repo, "sql/core")
    (repo / ".gitignore").write_text("*.tmp\n")
    (repo / "sql/core/scratch.tmp").write_text("x\n")
    assert release_check.digest(repo, "sql/core") == before
