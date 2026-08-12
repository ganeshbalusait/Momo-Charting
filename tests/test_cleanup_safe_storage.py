import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_safe_storage.py"
SPEC = importlib.util.spec_from_file_location("cleanup_safe_storage", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


def make_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


class CleanupSafeStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_plan_only_allowlists_disposable_large_files(self) -> None:
        threshold = cleanup.MIB
        old_database = self.root / "database" / "trades.before-test.db"
        artifact_database = self.root / "artifacts" / "trades-before-test.db"
        oversized_log = self.root / "artifacts" / "old.log"
        live_database = self.root / "database" / "trades.db"
        source_file = self.root / "api_server.py"
        virtualenv_file = self.root / ".venv" / "large.bin"
        virtualenv_cache = self.root / ".venv" / "package" / "__pycache__" / "module.pyc"
        node_module = self.root / "frontend" / "node_modules" / "package.bin"
        node_cache = (
            self.root
            / "frontend"
            / "node_modules"
            / "package"
            / "__pycache__"
            / "module.pyc"
        )

        for path in (
            old_database,
            artifact_database,
            oversized_log,
            live_database,
            source_file,
            virtualenv_file,
            virtualenv_cache,
            node_module,
            node_cache,
        ):
            make_file(path, threshold + 1)

        plan = cleanup.collect_plan(self.root, threshold)
        planned = {action.path for action in plan}

        self.assertIn(old_database.resolve(), planned)
        self.assertIn(artifact_database.resolve(), planned)
        self.assertIn(oversized_log.resolve(), planned)
        self.assertNotIn(live_database.resolve(), planned)
        self.assertNotIn(source_file.resolve(), planned)
        self.assertNotIn(virtualenv_file.resolve(), planned)
        self.assertNotIn(virtualenv_cache.parent.resolve(), planned)
        self.assertNotIn(node_module.resolve(), planned)
        self.assertNotIn(node_cache.parent.resolve(), planned)

    def test_apply_removes_allowlisted_items_and_preserves_live_data(self) -> None:
        threshold = cleanup.MIB
        old_database = self.root / "database" / "trades.before-test.db"
        live_database = self.root / "database" / "trades.db"
        pycache = self.root / "module" / "__pycache__"
        make_file(old_database, threshold + 1)
        make_file(live_database, threshold + 1)
        make_file(pycache / "module.pyc", threshold + 1)

        plan = cleanup.collect_plan(self.root, threshold)
        reclaimed, _ = cleanup.apply_plan(self.root, plan, "1.day.ago")

        self.assertGreater(reclaimed, 2 * threshold)
        self.assertFalse(old_database.exists())
        self.assertFalse(pycache.exists())
        self.assertTrue(live_database.exists())

    def test_files_at_or_below_threshold_are_preserved(self) -> None:
        threshold = cleanup.MIB
        backup = self.root / "database" / "trades.before-small.db"
        make_file(backup, threshold)

        plan = cleanup.collect_plan(self.root, threshold)

        self.assertNotIn(backup.resolve(), {action.path for action in plan})

    def test_symlinked_backup_is_never_deleted(self) -> None:
        threshold = cleanup.MIB
        outside = self.root.parent / f"{self.root.name}-outside.db"
        make_file(outside, threshold + 1)
        backup = self.root / "database" / "trades.before-link.db"
        backup.parent.mkdir(parents=True)
        backup.symlink_to(outside)
        self.addCleanup(lambda: outside.unlink(missing_ok=True))

        plan = cleanup.collect_plan(self.root, threshold)

        self.assertNotIn(backup.resolve(), {action.path for action in plan})
        self.assertTrue(outside.exists())

    def test_large_cache_tree_deduplicates_a_large_log_inside_it(self) -> None:
        threshold = cleanup.MIB
        cache = self.root / ".vite"
        log = cache / "frontend.log"
        make_file(log, threshold + 1)

        plan = cleanup.collect_plan(self.root, threshold)

        self.assertEqual(
            [(action.kind, action.path) for action in plan],
            [("delete-tree", cache.resolve())],
        )


if __name__ == "__main__":
    unittest.main()
