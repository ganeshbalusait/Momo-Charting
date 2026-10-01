"""The virtual BOT's trades reach admins only (2026-09-28)."""
import sqlite3

import momx_worker as w


def db(tmp_path):
    path = tmp_path / "users.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE app_users (email TEXT, role TEXT, is_active INTEGER)")
    c.executemany("INSERT INTO app_users VALUES (?, ?, ?)", [
        ("boss@x.com", "admin", 1), ("user@x.com", "user", 1), ("gone@x.com", "admin", 0)])
    c.commit(); c.close()
    return path


def test_only_an_active_admin_passes(tmp_path):
    w._ADMIN_CACHE.clear()
    path = db(tmp_path)
    assert w.viewer_is_admin("Boss@X.com", path) is True
    assert w.viewer_is_admin("user@x.com", path) is False
    assert w.viewer_is_admin("gone@x.com", path) is False
    assert w.viewer_is_admin("", path) is False
    assert w.viewer_is_admin(None, path) is False
    w._ADMIN_CACHE.clear()
    assert w.viewer_is_admin("boss@x.com", tmp_path / "missing.db") is False   # error -> not admin
    w._ADMIN_CACHE.clear()
