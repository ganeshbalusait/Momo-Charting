import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from loop_engineering import BilevelLoopRunner, LoopConfig


class BilevelLoopRunnerTests(unittest.TestCase):
    def _repo(self, root: Path) -> None:
        (root / "candidate.txt").write_text("1", encoding="utf-8")
        (root / "protected.txt").write_text("fixed", encoding="utf-8")
        (root / "loop_program.md").write_text("search safely", encoding="utf-8")
        (root / "proposer.py").write_text(textwrap.dedent("""
            from pathlib import Path
            path = Path('candidate.txt')
            path.write_text(str(int(path.read_text()) + 1))
        """), encoding="utf-8")
        (root / "verifier.py").write_text(textwrap.dedent("""
            import json
            from pathlib import Path
            score = float(Path('candidate.txt').read_text())
            print(json.dumps({'score': score, 'gate_pass': True, 'metrics': {'value': score}}))
        """), encoding="utf-8")

    def test_inner_loop_keeps_only_objective_improvements_and_exports_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repo(root)
            config = LoopConfig(
                run_id="keep",
                proposer_command=[sys.executable, "proposer.py"],
                verifier_command=[sys.executable, "verifier.py"],
                allowed_paths=["candidate.txt"],
                max_iterations=2,
                max_minutes=1,
            )

            state = BilevelLoopRunner(root, config).run(reset=True)

            self.assertEqual(state["accepted"], 2)
            self.assertEqual(state["champion"]["score"], 3.0)
            run_dir = root / "artifacts" / "loop_engineering" / "keep"
            self.assertEqual((run_dir / "workspace" / "candidate.txt").read_text(), "3")
            self.assertIn("candidate.txt", (run_dir / "champion.patch").read_text())
            self.assertEqual((root / "candidate.txt").read_text(), "1")

    def test_unauthorized_change_is_rejected_and_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repo(root)
            (root / "proposer.py").write_text(
                "from pathlib import Path\nPath('protected.txt').write_text('hacked')\n",
                encoding="utf-8",
            )
            config = LoopConfig(
                run_id="reject",
                proposer_command=[sys.executable, "proposer.py"],
                verifier_command=[sys.executable, "verifier.py"],
                allowed_paths=["candidate.txt"],
                max_iterations=1,
                max_minutes=1,
            )

            state = BilevelLoopRunner(root, config).run(reset=True)

            workspace = root / "artifacts" / "loop_engineering" / "reject" / "workspace"
            self.assertEqual(state["accepted"], 0)
            self.assertEqual(state["rejected"], 1)
            self.assertEqual((workspace / "protected.txt").read_text(), "fixed")

    def test_outer_loop_runs_after_stagnation_and_updates_persistent_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repo(root)
            (root / "proposer.py").write_text("pass\n", encoding="utf-8")
            (root / "meta.py").write_text(textwrap.dedent("""
                import os
                from pathlib import Path
                path = Path(os.environ['LOOP_PROGRAM_PATH'])
                path.write_text(path.read_text() + '\\nnew direction')
            """), encoding="utf-8")
            config = LoopConfig(
                run_id="outer",
                proposer_command=[sys.executable, "proposer.py"],
                verifier_command=[sys.executable, "verifier.py"],
                meta_command=[sys.executable, str(root / "meta.py")],
                allowed_paths=["candidate.txt"],
                max_iterations=2,
                max_minutes=1,
                stagnation_limit=1,
                max_outer_iterations=1,
            )

            state = BilevelLoopRunner(root, config).run(reset=True)

            run_dir = root / "artifacts" / "loop_engineering" / "outer"
            self.assertEqual(state["outer_iterations"], 1)
            self.assertIn("new direction", (run_dir / "program.md").read_text())
            history = [json.loads(line) for line in (run_dir / "experiments.jsonl").read_text().splitlines()]
            self.assertTrue(any(row.get("type") == "outer_loop" for row in history))


if __name__ == "__main__":
    unittest.main()
