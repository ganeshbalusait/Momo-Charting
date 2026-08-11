from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
import difflib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from typing import Any


DEFAULT_COPY_EXCLUDES = {
    ".git",
    ".pnpm-store",
    ".venv",
    ".vite",
    "__pycache__",
    "artifacts",
    "database",
    "dist",
    "node_modules",
}


@dataclass(slots=True)
class VerificationResult:
    score: float
    gate_pass: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "VerificationResult":
        return cls(
            score=float(payload.get("score") or 0.0),
            gate_pass=bool(payload.get("gate_pass")),
            metrics=dict(payload.get("metrics") or {}),
            summary=str(payload.get("summary") or ""),
        )


@dataclass(slots=True)
class LoopConfig:
    run_id: str
    proposer_command: list[str]
    verifier_command: list[str]
    allowed_paths: list[str]
    program_path: str = "loop_program.md"
    meta_command: list[str] | None = None
    max_iterations: int = 10
    max_minutes: float = 60.0
    stagnation_limit: int = 3
    max_outer_iterations: int = 3
    min_score_delta: float = 0.0
    state_root: str = "artifacts/loop_engineering"
    copy_excludes: list[str] = field(default_factory=lambda: sorted(DEFAULT_COPY_EXCLUDES))

    @classmethod
    def from_json(cls, path: str | Path) -> "LoopConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**payload)


class BilevelLoopRunner:
    """Run isolated inner experiments plus an optional outer search-strategy loop.

    The proposer may edit only ``allowed_paths`` inside a shadow workspace. The
    verifier is treated as immutable. Improvements remain in the shadow workspace
    and are exported as a patch for human review; this runner never deploys them.
    """

    def __init__(self, repo_root: str | Path, config: LoopConfig) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.run_dir = (self.repo_root / config.state_root / config.run_id).resolve()
        self.workspace = self.run_dir / "workspace"
        self.state_path = self.run_dir / "state.json"
        self.history_path = self.run_dir / "experiments.jsonl"
        self.patch_path = self.run_dir / "champion.patch"
        self.program_copy = self.run_dir / "program.md"
        self.initial_snapshot_path = self.run_dir / "initial_snapshot.json"
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.config.run_id.strip():
            raise ValueError("run_id is required")
        if not self.config.verifier_command:
            raise ValueError("verifier_command is required")
        if not self.config.allowed_paths:
            raise ValueError("allowed_paths must contain at least one file or glob")
        if self.config.max_iterations < 1 or self.config.max_minutes <= 0:
            raise ValueError("max_iterations and max_minutes must be positive")
        if self.run_dir == self.repo_root or self.repo_root not in self.run_dir.parents:
            raise ValueError("state_root must resolve inside the repository")

    def initialize(self, reset: bool = False) -> dict[str, Any]:
        if reset and self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if not self.workspace.exists():
            self._copy_repo(self.repo_root, self.workspace)
        if not self.program_copy.exists():
            source = self.repo_root / self.config.program_path
            if not source.exists():
                raise FileNotFoundError(f"program file not found: {source}")
            shutil.copy2(source, self.program_copy)
        if not self.initial_snapshot_path.exists():
            self.initial_snapshot_path.write_text(
                json.dumps(self._snapshot(self.workspace), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        state = {
            "run_id": self.config.run_id,
            "status": "initialized",
            "created_at": self._now(),
            "updated_at": self._now(),
            "baseline": None,
            "champion": None,
            "iterations": 0,
            "accepted": 0,
            "rejected": 0,
            "stagnation": 0,
            "outer_iterations": 0,
            "stop_reason": "",
        }
        self._write_state(state)
        return state

    def run(self, reset: bool = False, baseline_only: bool = False) -> dict[str, Any]:
        state = self.initialize(reset=reset)
        started = time.monotonic()
        if state.get("baseline") is None:
            baseline = self._verify(iteration=0)
            if not baseline.gate_pass:
                state.update(status="blocked", stop_reason="baseline verifier failed")
                state["baseline"] = asdict(baseline)
                self._write_state(state)
                return state
            state["baseline"] = asdict(baseline)
            state["champion"] = asdict(baseline)
            state["status"] = "running"
            self._write_state(state)
        if baseline_only:
            state.update(status="baseline_complete", stop_reason="baseline-only run")
            self._write_state(state)
            return state

        while int(state["iterations"]) < self.config.max_iterations:
            elapsed_minutes = (time.monotonic() - started) / 60.0
            if elapsed_minutes >= self.config.max_minutes:
                state["stop_reason"] = "time limit reached"
                break
            iteration = int(state["iterations"]) + 1
            before = self._snapshot(self.workspace)
            propose = self._run_command(
                self.config.proposer_command,
                cwd=self.workspace,
                iteration=iteration,
                purpose="proposer",
            )
            after = self._snapshot(self.workspace)
            changed = self._changed_paths(before, after)
            unauthorized = [path for path in changed if not self._is_allowed(path)]
            experiment: dict[str, Any] = {
                "iteration": iteration,
                "started_at": self._now(),
                "changed_paths": changed,
                "proposer_exit_code": propose.returncode,
                "proposer_output": self._tail(propose.stdout, propose.stderr),
                "accepted": False,
            }
            if propose.returncode != 0 or not changed or unauthorized:
                self._restore_snapshot(self.workspace, before, after)
                reason = (
                    f"unauthorized changes: {', '.join(unauthorized)}"
                    if unauthorized
                    else "proposer failed" if propose.returncode != 0 else "no files changed"
                )
                experiment["reason"] = reason
                state["rejected"] = int(state["rejected"]) + 1
                state["stagnation"] = int(state["stagnation"]) + 1
            else:
                result = self._verify(iteration=iteration)
                champion_score = float(state["champion"]["score"])
                improved = result.gate_pass and result.score > champion_score + self.config.min_score_delta
                experiment["verification"] = asdict(result)
                experiment["accepted"] = improved
                if improved:
                    state["champion"] = asdict(result)
                    state["accepted"] = int(state["accepted"]) + 1
                    state["stagnation"] = 0
                    experiment["reason"] = "score improved and all gates passed"
                else:
                    self._restore_snapshot(self.workspace, before, after)
                    state["rejected"] = int(state["rejected"]) + 1
                    state["stagnation"] = int(state["stagnation"]) + 1
                    experiment["reason"] = "verifier failed or score did not improve"
            state["iterations"] = iteration
            state["updated_at"] = self._now()
            self._append_history(experiment)

            if (
                int(state["stagnation"]) >= self.config.stagnation_limit
                and self.config.meta_command
                and int(state["outer_iterations"]) < self.config.max_outer_iterations
            ):
                meta = self._run_command(
                    self.config.meta_command,
                    cwd=self.run_dir,
                    iteration=iteration,
                    purpose="meta",
                )
                state["outer_iterations"] = int(state["outer_iterations"]) + 1
                state["stagnation"] = 0
                self._append_history(
                    {
                        "type": "outer_loop",
                        "iteration": iteration,
                        "exit_code": meta.returncode,
                        "output": self._tail(meta.stdout, meta.stderr),
                        "program_sha256": self._sha256(self.program_copy.read_bytes()),
                        "created_at": self._now(),
                    }
                )
            self._write_state(state)

        state["status"] = "complete"
        if not state.get("stop_reason"):
            state["stop_reason"] = "iteration limit reached"
        state["updated_at"] = self._now()
        self._write_patch()
        self._write_state(state)
        return state

    def _verify(self, iteration: int) -> VerificationResult:
        completed = self._run_command(
            self.config.verifier_command,
            cwd=self.workspace,
            iteration=iteration,
            purpose="verifier",
        )
        if completed.returncode != 0:
            return VerificationResult(
                score=0.0,
                gate_pass=False,
                summary=f"verifier exited {completed.returncode}: {self._tail(completed.stdout, completed.stderr)}",
            )
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            return VerificationResult(0.0, False, summary="verifier produced no JSON")
        try:
            return VerificationResult.from_payload(json.loads(lines[-1]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return VerificationResult(0.0, False, summary=f"invalid verifier JSON: {exc}")

    def _run_command(
        self,
        command: list[str],
        cwd: Path,
        iteration: int,
        purpose: str,
    ) -> subprocess.CompletedProcess[str]:
        if not command:
            return subprocess.CompletedProcess([], 1, "", f"{purpose} command is empty")
        env = os.environ.copy()
        env.update(
            {
                "LOOP_RUN_ID": self.config.run_id,
                "LOOP_ITERATION": str(iteration),
                "LOOP_PURPOSE": purpose,
                "LOOP_WORKSPACE": str(self.workspace),
                "LOOP_PROGRAM_PATH": str(self.program_copy),
                "LOOP_STATE_PATH": str(self.state_path),
                "LOOP_HISTORY_PATH": str(self.history_path),
            }
        )
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=max(self.config.max_minutes * 60.0, 1.0),
            check=False,
        )

    def _copy_repo(self, source: Path, target: Path) -> None:
        excludes = set(self.config.copy_excludes)

        def ignore(_directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in excludes or name.endswith((".pyc", ".log"))}

        shutil.copytree(source, target, ignore=ignore)

    def _snapshot(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                snapshot[relative] = path.read_bytes().hex()
        return snapshot

    @staticmethod
    def _changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
        return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))

    def _is_allowed(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        return any(fnmatch(normalized, pattern.replace("\\", "/")) for pattern in self.config.allowed_paths)

    @staticmethod
    def _restore_snapshot(root: Path, before: dict[str, str], after: dict[str, str]) -> None:
        for relative in set(before) | set(after):
            path = root / relative
            if relative not in before:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes.fromhex(before[relative]))

    def _write_patch(self) -> None:
        initial = json.loads(self.initial_snapshot_path.read_text(encoding="utf-8"))
        current = self._snapshot(self.workspace)
        chunks: list[str] = []
        for relative in self._changed_paths(initial, current):
            if not self._is_allowed(relative):
                continue
            before = bytes.fromhex(initial.get(relative, "")).decode("utf-8", errors="replace").splitlines(True)
            after = bytes.fromhex(current.get(relative, "")).decode("utf-8", errors="replace").splitlines(True)
            chunks.extend(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        self.patch_path.write_text("".join(chunks), encoding="utf-8")

    def _append_history(self, payload: dict[str, Any]) -> None:
        with self.history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")

    def _write_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = self._now()
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _tail(stdout: str, stderr: str, limit: int = 4000) -> str:
        combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        return combined[-limit:]

    @staticmethod
    def _sha256(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


def command_display(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)
