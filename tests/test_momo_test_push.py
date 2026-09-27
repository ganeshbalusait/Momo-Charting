"""The Send-test button's server half.

A user finishes phone-push setup and has no way to know it worked until a real
alert fires - a typo'd topic fails silently forever. The test push exists to
answer "is my phone wired up?" RIGHT NOW, so unlike every other push in this
codebase it must NOT swallow failures: its entire value is an honest verdict.
"""
import json

from momx import momo_alert


def _write_users(tmp_path, users):
    path = tmp_path / "users.json"
    path.write_text(json.dumps(users), encoding="utf-8")
    return path


class RecordingPoster:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, topic, title, body, tags):
        self.calls.append({"topic": topic, "title": title, "body": body, "tags": tags})
        if self.fail:
            raise OSError("ntfy unreachable")


def test_no_topic_reports_the_actual_problem_and_posts_nothing(tmp_path):
    path = _write_users(tmp_path, {"user@x.com": {"ntfyTopic": ""}})
    poster = RecordingPoster()
    out = momo_alert.test_push("user@x.com", path=path, poster=poster)
    assert out["ok"] is False
    assert poster.calls == []
    # The message must tell the user WHAT to do, not just that it failed.
    assert "Generate" in out["error"]


def test_unknown_user_is_the_same_as_no_topic(tmp_path):
    path = _write_users(tmp_path, {})
    out = momo_alert.test_push("nobody@x.com", path=path, poster=RecordingPoster())
    assert out["ok"] is False


def test_success_posts_once_to_that_users_topic_and_names_it(tmp_path):
    path = _write_users(
        tmp_path,
        {
            "user@x.com": {"ntfyTopic": "agx-abc123"},
            "other@x.com": {"ntfyTopic": "agx-SOMEONE-ELSE"},
        },
    )
    poster = RecordingPoster()
    out = momo_alert.test_push("user@x.com", path=path, poster=poster)
    assert out["ok"] is True
    assert out["topic"] == "agx-abc123"
    # Exactly one post, to THIS user's channel - never a broadcast.
    assert [c["topic"] for c in poster.calls] == ["agx-abc123"]
    assert "test" in poster.calls[0]["title"].lower() or "test" in poster.calls[0]["body"].lower()


def test_email_case_and_whitespace_match_the_config_keying(tmp_path):
    # save_user_config keys by stripped lowercase; the test push must find the
    # same record or a user saved as User@X.com tests against the defaults.
    path = _write_users(tmp_path, {"user@x.com": {"ntfyTopic": "agx-abc123"}})
    out = momo_alert.test_push("  User@X.com  ", path=path, poster=RecordingPoster())
    assert out["ok"] is True


def test_a_failed_post_is_reported_not_swallowed(tmp_path):
    path = _write_users(tmp_path, {"user@x.com": {"ntfyTopic": "agx-abc123"}})
    out = momo_alert.test_push("user@x.com", path=path, poster=RecordingPoster(fail=True))
    assert out["ok"] is False
    assert "topic" in out and out["topic"] == "agx-abc123"
    # Still must not RAISE - the endpoint turns this into a JSON verdict.


def test_default_poster_exists_but_tests_never_hit_the_network(tmp_path):
    # Belt and braces: the injectable poster is the seam; make sure the
    # function demands nothing else exotic.
    path = _write_users(tmp_path, {"user@x.com": {"ntfyTopic": "agx-abc123"}})
    poster = RecordingPoster()
    out = momo_alert.test_push("user@x.com", path=path, poster=poster)
    assert out["ok"] is True and len(poster.calls) == 1
