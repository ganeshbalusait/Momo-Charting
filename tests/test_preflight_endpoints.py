from __future__ import annotations

"""The preflight checklist endpoints, scheduler and phone push.

WHY THIS FILE EXISTS, stated plainly so nobody later "simplifies" it into a
smoke test: overnight on 2026-08-31 every real fault was invisible from the
app's own badges. A premarket banner asked for a Tradier token while all nine
scanner symbols already had their bars. A chart served candles 151 minutes old
while the broker had 40-second bars. Nothing was red anywhere.

So the contract these tests defend is not "the endpoint returns 200". It is:

  * A checklist that could not run reports UNKNOWN, never a pass. Both the
    payload and the phone push must say so in words.
  * The failing push carries the MEASURED NUMBER for each check, because a
    push that says "something is wrong" is the flag we are replacing.
  * The GET never runs anything. If opening the admin page re-ran the
    checklist, "did it pass at 08:45?" would be permanently unanswerable.
  * Both routes are admin-only, gate BEFORE work.
  * The 08:45 run fires once per weekday and pushes once per day.

Two halves. The wiring half reads api_server.py as SOURCE, because importing
it boots every scheduler and opens the live database (tests/test_gateway.py).
The behaviour half LIFTS the module-level preflight functions out of that
source by name with ast and executes them in an isolated namespace with fake
clocks and a fake preflight module - real execution, no live server, no I/O.
"""

import ast
import sys
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from handler_source import code_only, route_handler

API_PATH = Path(__file__).resolve().parent.parent / "api_server.py"
API_SOURCE = API_PATH.read_text(encoding="utf-8")

LIFTED_NAMES = (
    "PREFLIGHT_SCHEDULE_HOUR_ET",
    "PREFLIGHT_SCHEDULE_MINUTE_ET",
    "PREFLIGHT_SCHEDULE_WINDOW_MINUTES",
    "PREFLIGHT_HISTORY_DAYS",
    "PREFLIGHT_RUN_LOCK",
    "PREFLIGHT_NOT_PASSING",
    "PREFLIGHT_EXPECTED_WARN_IDS",
    "PREFLIGHT_STATUS_RANK",
    "_preflight_heal_worked",
    "_PREFLIGHT_LAST_RESULT",
    "_PREFLIGHT_DAILY",
    "_PREFLIGHT_THREAD",
    "_preflight_heal_sentence",
    "_preflight_admin_emails",
    "_preflight_already_ran_today",
    "_preflight_seed_day_claim",
    "_preflight_module",
    "_preflight_unavailable",
    "_preflight_day_of",
    "_preflight_run_now",
    "_preflight_history",
    "_preflight_payload",
    "_preflight_push_summary",
    "_preflight_daily_loop",
    "_start_preflight_scheduler",
)

EASTERN = ZoneInfo("America/New_York")
MONDAY = datetime(2026, 9, 7, 8, 45, tzinfo=EASTERN)  # a Monday
SATURDAY = datetime(2026, 9, 5, 8, 45, tzinfo=EASTERN)


def _lift(source: str, names) -> str:
    """The named top-level statements, in file order, as executable source.

    By NAME rather than by a byte window: a fixed window silently slides when
    someone adds a comment, and a test that fails for that reason trains
    everyone to ignore it (see tests/handler_source.py for the three times
    that already happened here).
    """
    tree = ast.parse(source)
    wanted = set(names)
    chunks: list[str] = []
    found: set[str] = set()
    for node in tree.body:
        bound: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound = [node.name]
        elif isinstance(node, ast.Assign):
            bound = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound = [node.target.id]
        if not (set(bound) & wanted):
            continue
        segment = ast.get_source_segment(source, node)
        assert segment is not None, f"could not slice {bound}"
        chunks.append(segment)
        found.update(set(bound) & wanted)
    missing = wanted - found
    assert not missing, f"api_server.py no longer defines: {sorted(missing)}"
    return "from __future__ import annotations\n\n" + "\n\n\n".join(chunks) + "\n"


LIFTED_SOURCE = _lift(API_SOURCE, LIFTED_NAMES)


class _Clock:
    """Stands in for `datetime`; hands out preset ET instants in order."""

    def __init__(self, moments):
        self._moments = list(moments)
        self.calls = 0

    def now(self, _tz=None):
        self.calls += 1
        if len(self._moments) > 1:
            return self._moments.pop(0)
        return self._moments[0]


class _StopLoop(Exception):
    pass


def _namespace(preflight_module=..., moments=(MONDAY,), sleeps_before_stop=99):
    """A fresh executed copy of the lifted preflight code.

    `preflight_module` is injected into sys.modules, so `import preflight`
    inside _preflight_module resolves to it regardless of what is on disk -
    preflight.py is owned by another agent and may or may not exist yet.
    """
    original = sys.modules.get("preflight", ...)
    if preflight_module is not ...:
        sys.modules["preflight"] = preflight_module
    # The names api_server itself imports at module scope. Seeded BEFORE the
    # exec because PREFLIGHT_RUN_LOCK is built at module level.
    namespace: dict = {"threading": threading, "time": time, "datetime": datetime}
    exec(compile(LIFTED_SOURCE, "<lifted-api_server>", "exec"), namespace)
    pushes: list[tuple] = []

    def _push(title, body, tags="bell", only_emails=None):
        # Recorded WITH the recipient set, so a test can assert the admin-only
        # boundary rather than merely that something was pushed.
        pushes.append((title, body, tags, only_emails))

    namespace["_push_phone_notification"] = _push
    # AuthService stand-in: two accounts, one admin one not, mirroring the live
    # box (ganeshbalusait@gmail.com admin, gpm_in@yahoo.com role user).
    namespace["auth_service_instance"] = lambda: SimpleNamespace(
        list_users=lambda actor=None: [
            {"email": "admin@example.com", "role": "admin", "isActive": True},
            {"email": "user@example.com", "role": "user", "isActive": True},
            {"email": "old-admin@example.com", "role": "admin", "isActive": False},
        ]
    )
    namespace["ZoneInfo"] = lambda name: EASTERN
    namespace["EASTERN_TZ"] = "America/New_York"
    clock = _Clock(moments)
    namespace["datetime"] = clock

    remaining = {"n": sleeps_before_stop}

    def _sleep(_seconds):
        remaining["n"] -= 1
        if remaining["n"] < 0:
            raise _StopLoop

    namespace["time"] = SimpleNamespace(monotonic=lambda: 0.0, sleep=_sleep)
    namespace["_pushes"] = pushes
    namespace["_clock"] = clock
    namespace["_previous_preflight_module"] = original
    return namespace


@pytest.fixture
def restore_preflight():
    original = sys.modules.get("preflight", ...)
    yield
    if original is ...:
        sys.modules.pop("preflight", None)
    else:
        sys.modules["preflight"] = original


def _fake_module(result=None, history=None, raises=None, history_raises=None):
    calls: list[dict] = []

    def run_preflight(heal: bool = False, now_et=None, directory=None):
        calls.append({"heal": heal, "now_et": now_et})
        if raises is not None:
            raise raises
        return result

    def load_preflight_history(days: int = 30, directory=None):
        if history_raises is not None:
            raise history_raises
        return list(history or [])

    module = SimpleNamespace(
        run_preflight=run_preflight,
        load_preflight_history=load_preflight_history,
    )
    module.calls = calls
    return module


def _result(passed=14, failed=0, unknown=0, warned=0, checks=None, at="2026-09-07T08:45:00-04:00"):
    if checks is None:
        checks = [
            {"id": f"c{i}", "label": f"Check {i}", "status": "pass", "measured": f"{i} bars"}
            for i in range(passed)
        ]
    return {
        "at": at,
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "unknown": unknown,
        "checks": checks,
        "healed": [],
    }


# ---------------------------------------------------------------------------
# Wiring: both routes exist and are admin-gated BEFORE they do anything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/api/preflight", "/api/preflight/run"])
def test_route_is_admin_only(route: str) -> None:
    """The trader: "add in admin settings not for user"."""
    handler = route_handler(API_SOURCE, route)
    assert "_require_admin_user" in handler, (
        f"{route} exposes broker/token diagnostics and must reject non-admins"
    )


@pytest.mark.parametrize(
    "route,work",
    [("/api/preflight", "_preflight_payload"), ("/api/preflight/run", "_preflight_run_now")],
)
def test_the_gate_comes_before_the_work_static(route: str, work: str) -> None:
    """A check after the side effect is not a check."""
    handler = route_handler(API_SOURCE, route)
    gate_at = handler.find("_require_admin_user")
    work_at = handler.find(work)
    assert gate_at != -1, f"{route} has no admin gate"
    assert work_at != -1, f"{route} never calls {work}"
    assert gate_at < work_at, f"{route} does work before checking who is asking"


# --- the denial, actually EXECUTED -------------------------------------------
# A local curl can never prove this: every request from 127.0.0.1
# authenticates as LOCAL_AUTO_LOGIN_EMAIL, which is the admin. The live half
# of the proof (that X-AGX-Access-Email really does de-escalate to a non-admin
# and that _require_admin_user answers 403 for them) is in the handover notes.
# This half RUNS the two new handlers: the route source and the real
# _require_admin_user are lifted from api_server.py, and the rule they call is
# the real AuthService.require_admin. Only the session lookup and the JSON
# writer are stubbed.

from auth_service import AuthorizationError, AuthService  # noqa: E402
from handler_source import function_body  # noqa: E402

# The exact user row the live server returned for X-AGX-Access-Email:
# gpm_in@yahoo.com against :3002 - a real account, role "user".
NON_ADMIN = {
    "id": "4cec6d37-56fe-45d5-ac7b-6822c64ebc29",
    "email": "gpm_in@yahoo.com",
    "displayName": "Guru",
    "role": "user",
    "isAdmin": False,
    "isActive": True,
    "signedInVia": "cloudflare",
}


class _Sent(Exception):
    """Raised by the stub writer so `return` semantics are unambiguous."""


def _executed_handler(route: str, user: dict):
    """Run the real route body for `route` as `user`; return (status, payload).

    Also returns whether any preflight work was reached, so "403 but it ran
    the checklist anyway" cannot pass.
    """
    # route_handler starts AT the `if`, so its first line has lost the 12
    # spaces every other line still carries. Put them back, then dedent the
    # block as a whole - never re-indent line by line, which would silently
    # flatten the handler's own nesting.
    handler_src = textwrap.dedent(" " * 12 + route_handler(API_SOURCE, route))
    gate_src = textwrap.dedent(
        function_body(API_SOURCE, "_require_admin_user", indent="    ")
    )
    worked: list[str] = []

    class _Stub:
        def __init__(self):
            self.sent: list = []

        def _require_session_user(self):
            return user

        def _send_json(self, status, payload):
            self.sent.append((status, payload))

    scope: dict = {
        "AuthorizationError": AuthorizationError,
        "HTTPStatus": __import__("http").HTTPStatus,
        "auth_service_instance": lambda: SimpleNamespace(
            require_admin=AuthService.require_admin
        ),
        "parse_qs": __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs,
        "PREFLIGHT_HISTORY_DAYS": 30,
        "_preflight_payload": lambda *a, **k: worked.append("payload") or {},
        "_preflight_run_now": lambda *a, **k: worked.append("run") or {},
    }
    # Bind the real gate onto the stub, then run the real route body.
    exec(compile(gate_src, "<lifted-gate>", "exec"), scope)
    _Stub._require_admin_user = scope["_require_admin_user"]
    exec(
        compile(
            "def _route(self, parsed, body):\n"
            + textwrap.indent(handler_src, "    ")
            + "\n",
            "<lifted-route>",
            "exec",
        ),
        scope,
    )
    stub = _Stub()
    scope["_route"](stub, SimpleNamespace(path=route, query=""), {})
    return stub.sent, worked


@pytest.mark.parametrize("route", ["/api/preflight", "/api/preflight/run"])
def test_a_real_non_admin_is_refused_by_the_real_handler(route: str) -> None:
    sent, worked = _executed_handler(route, NON_ADMIN)
    assert len(sent) == 1, f"{route} answered {len(sent)} times"
    status, payload = sent[0]
    assert int(status) == 403, f"{route} returned {int(status)} to a non-admin"
    assert payload == {"error": "Administrator access is required."}
    assert worked == [], f"{route} did preflight work before refusing: {worked}"


@pytest.mark.parametrize("route", ["/api/preflight", "/api/preflight/run"])
def test_an_admin_gets_through_the_same_handler(route: str) -> None:
    """The other half: a gate that refuses everyone is not a gate either."""
    admin = dict(NON_ADMIN, role="admin", isAdmin=True, email="ganeshbalusait@gmail.com")
    sent, worked = _executed_handler(route, admin)
    assert len(sent) == 1
    status, _payload = sent[0]
    assert int(status) == 200
    assert worked, f"{route} answered 200 without doing any preflight work"


def test_preflight_routes_are_not_public() -> None:
    """/api/health is deliberately unauthenticated; these must not join it."""
    tree = ast.parse(API_SOURCE)
    public: set[str] = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "AUTH_PUBLIC_API_PATHS":
                public = {
                    element.value
                    for element in ast.walk(node)
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    assert public, "AUTH_PUBLIC_API_PATHS was not found"
    assert "/api/preflight" not in public
    assert "/api/preflight/run" not in public


def test_get_never_runs_the_checklist() -> None:
    """Opening the admin page must not fire broker probes or heal anything.

    If the GET re-ran the checklist, the strip would always show a result from
    a second ago and "did it pass at 08:45 this morning?" could never be
    answered.
    """
    handler = code_only(route_handler(API_SOURCE, "/api/preflight"))
    assert "_preflight_run_now" not in handler
    assert "run_preflight" not in handler


def test_scheduler_starts_from_main_not_at_import() -> None:
    """A pytest run or tooling script must never schedule heals or push."""
    tree = ast.parse(API_SOURCE)
    module_level_calls = [
        node
        for statement in tree.body
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_start_preflight_scheduler"
    ]
    assert not module_level_calls, "the scheduler must not start at import time"
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    called = {
        node.func.id
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_start_preflight_scheduler" in called, "main() never starts the daily run"


def test_daily_thread_is_a_daemon_that_cannot_kill_the_process() -> None:
    tree = ast.parse(API_SOURCE)
    starter = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_start_preflight_scheduler"
    )
    segment = ast.get_source_segment(API_SOURCE, starter) or ""
    assert "daemon=True" in segment
    loop = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_preflight_daily_loop"
    )
    assert any(isinstance(node, ast.Try) for node in ast.walk(loop)), (
        "the daily loop must swallow its own errors; a scheduler that takes "
        "the backend down at 08:45 is worse than a missed checklist"
    )


# ---------------------------------------------------------------------------
# Behaviour: a checklist that could not run says so
# ---------------------------------------------------------------------------


def test_missing_preflight_module_reports_unknown_not_ok(restore_preflight) -> None:
    namespace = _namespace(preflight_module=None)  # None in sys.modules -> ImportError
    result = namespace["_preflight_run_now"](heal=True)
    assert result["available"] is False
    assert result["passed"] == 0
    assert result["unknown"] == 1
    assert result["checks"][0]["status"] == "unknown"
    assert result["unavailableReason"]
    title, body = namespace["_preflight_push_summary"](result)
    assert "could not run" in title
    assert "nothing is known" in body


def test_a_half_written_preflight_module_is_unavailable(restore_preflight) -> None:
    """It imports but has no run_preflight yet. That is not "available".

    preflight.py is written by another agent and was, at the moment this test
    was added, importable with only helpers defined. The endpoint must name the
    missing function rather than surfacing an AttributeError from inside a run.
    """
    half = SimpleNamespace(worst_status=lambda: "pass")
    namespace = _namespace(preflight_module=half)
    result = namespace["_preflight_run_now"](heal=True)
    assert result["available"] is False
    assert "run_preflight" in result["unavailableReason"]
    payload = namespace["_preflight_payload"]()
    assert payload["available"] is False
    assert "run_preflight" in payload["unavailableReason"]


def test_a_raising_checklist_is_unknown_not_a_pass(restore_preflight) -> None:
    namespace = _namespace(preflight_module=_fake_module(raises=RuntimeError("schwab timeout")))
    result = namespace["_preflight_run_now"](heal=True)
    assert result["available"] is False
    assert result["unknown"] == 1
    assert "RuntimeError" in result["unavailableReason"]
    assert "schwab timeout" in result["unavailableReason"]


def test_a_nonsense_return_is_unknown_not_a_pass(restore_preflight) -> None:
    namespace = _namespace(preflight_module=_fake_module(result=["not", "a", "dict"]))
    result = namespace["_preflight_run_now"](heal=True)
    assert result["available"] is False
    assert "not a result dict" in result["unavailableReason"]


def test_run_measures_its_own_duration_and_records_heal(restore_preflight) -> None:
    module = _fake_module(result=_result())
    namespace = _namespace(preflight_module=module)
    result = namespace["_preflight_run_now"](heal=True, now_et=MONDAY)
    assert result["available"] is True
    assert isinstance(result["elapsedMs"], int)
    assert result["healEnabled"] is True
    assert module.calls == [{"heal": True, "now_et": MONDAY}]


def test_heal_false_is_passed_through(restore_preflight) -> None:
    module = _fake_module(result=_result())
    namespace = _namespace(preflight_module=module)
    namespace["_preflight_run_now"](heal=False)
    assert module.calls[0]["heal"] is False


# ---------------------------------------------------------------------------
# Behaviour: the push carries numbers
# ---------------------------------------------------------------------------


def test_all_green_push_is_the_fraction() -> None:
    namespace = _namespace()
    title, body = namespace["_preflight_push_summary"](
        dict(_result(passed=14), available=True, elapsedMs=1234)
    )
    assert title == "AGX ready - 14/14"
    # NOT "All 14 checks passed" - that exact sentence was being printed on
    # runs where 12 of 14 passed and two warned.
    assert "14 of 14 checks passed" in body


def test_failing_push_names_each_check_and_its_measured_number() -> None:
    checks = [
        {"id": "ok", "label": "Scanner symbols", "status": "pass", "measured": "9/9 symbols"},
        {
            "id": "chart-age",
            "label": "Chart tape freshness",
            "status": "fail",
            "measured": "151 min behind broker",
        },
        {
            "id": "schwab",
            "label": "Schwab token",
            "status": "unknown",
            "measured": "probe timed out after 6s",
        },
    ]
    namespace = _namespace()
    title, body = namespace["_preflight_push_summary"](
        dict(_result(passed=1, failed=1, unknown=1, checks=checks), available=True)
    )
    assert title == "AGX preflight: 2 of 3 not passing"
    # The number is the point. A push that only said "something is wrong" is
    # the flag this whole feature replaces.
    assert "151 min behind broker" in body
    assert "probe timed out after 6s" in body
    assert "Scanner symbols" not in body


def test_a_warning_is_not_a_failure() -> None:
    """Tradier is dead ON PURPOSE (unfunded account, API access revoked).

    Its 04:00-07:00 job is done by the Alpaca SIP fallback, so it must read as
    an expected degradation. A checklist that cries wolf every morning is a
    checklist nobody reads by Thursday.
    """
    checks = [
        {"id": "tradier", "label": "Tradier", "status": "warn", "measured": "401 revoked (expected)"},
        {"id": "alpaca", "label": "Premarket 04:00-07:00", "status": "pass", "measured": "167 bars"},
    ]
    namespace = _namespace()
    title, body = namespace["_preflight_push_summary"](
        dict(_result(passed=1, warned=1, checks=checks), available=True)
    )
    assert title.startswith("AGX ready - ")
    assert "1 known degradation" in body
    # Named, so "expected" cannot quietly cover an unexpected one.
    assert "Tradier" in body


def test_healed_items_are_named_in_the_push() -> None:
    namespace = _namespace()
    payload = dict(_result(passed=2), available=True)
    payload["healed"] = ["restarted watchlist warmer", "dropped GOOGLE from grid 3"]
    _title, body = namespace["_preflight_push_summary"](payload)
    assert "restarted watchlist warmer" in body
    assert "dropped GOOGLE from grid 3" in body


# ---------------------------------------------------------------------------
# Behaviour: the GET payload
# ---------------------------------------------------------------------------


def test_payload_reports_today_and_the_strip(restore_preflight) -> None:
    today = _result(at="2026-09-07T08:45:10-04:00")
    older = _result(at="2026-09-04T08:45:00-04:00")
    namespace = _namespace(preflight_module=_fake_module(history=[today, older]))
    payload = namespace["_preflight_payload"]()
    assert payload["available"] is True
    assert payload["date"] == "2026-09-07"
    assert payload["ranToday"] is True
    assert payload["result"]["at"] == "2026-09-07T08:45:10-04:00"
    assert payload["historyDays"] == 2
    assert payload["schedule"]["at"] == "08:45 ET"
    assert payload["schedule"]["weekdaysOnly"] is True


def test_payload_says_it_did_not_run_today(restore_preflight) -> None:
    """Silence must not read as green."""
    namespace = _namespace(preflight_module=_fake_module(history=[_result(at="2026-09-04T08:45:00-04:00")]))
    payload = namespace["_preflight_payload"]()
    assert payload["ranToday"] is False
    assert payload["result"] is None
    assert payload["historyDays"] == 1


def test_payload_prefers_the_fresher_in_memory_run(restore_preflight) -> None:
    """A run whose archive write lost the race must still be visible."""
    archived = _result(at="2026-09-07T08:45:00-04:00")
    namespace = _namespace(preflight_module=_fake_module(result=_result(at="2026-09-07T09:10:00-04:00"), history=[archived]))
    namespace["_preflight_run_now"](heal=True)
    payload = namespace["_preflight_payload"]()
    assert payload["result"]["at"] == "2026-09-07T09:10:00-04:00"


def test_unreadable_archive_is_reported_not_swallowed(restore_preflight) -> None:
    """[] with no error would claim thirty clean days that were never read."""
    namespace = _namespace(
        preflight_module=_fake_module(history_raises=OSError("archive is locked"))
    )
    payload = namespace["_preflight_payload"]()
    assert payload["history"] == []
    assert "archive is locked" in payload["historyError"]
    assert payload["ranToday"] is False


def test_payload_when_the_module_is_missing(restore_preflight) -> None:
    namespace = _namespace(preflight_module=None)
    payload = namespace["_preflight_payload"]()
    assert payload["available"] is False
    assert payload["unavailableReason"]
    assert payload["history"] == []
    assert payload["ranToday"] is False


# ---------------------------------------------------------------------------
# Behaviour: the 08:45 ET scheduler
# ---------------------------------------------------------------------------


def _run_loop(namespace) -> None:
    with pytest.raises(_StopLoop):
        namespace["_preflight_daily_loop"]()


def test_the_daily_run_fires_at_0845_and_pushes_exactly_once(restore_preflight) -> None:
    module = _fake_module(result=_result(passed=14))
    minutes = [
        MONDAY.replace(hour=8, minute=44),
        MONDAY.replace(hour=8, minute=45),
        MONDAY.replace(hour=8, minute=46),
        MONDAY.replace(hour=9, minute=0),
    ]
    namespace = _namespace(preflight_module=module, moments=minutes, sleeps_before_stop=3)
    _run_loop(namespace)
    assert len(module.calls) == 1, "the checklist must not re-run all morning"
    assert len(namespace["_pushes"]) == 1, "the phone must buzz once a day, not once a minute"
    title, _body, _tags, _to = namespace["_pushes"][0]
    assert title == "AGX ready - 14/14"
    assert namespace["_PREFLIGHT_DAILY"]["ranDay"] == "2026-09-07"
    assert namespace["_PREFLIGHT_DAILY"]["pushedDay"] == "2026-09-07"


def test_nothing_runs_at_the_weekend(restore_preflight) -> None:
    module = _fake_module(result=_result())
    namespace = _namespace(
        preflight_module=module,
        moments=[SATURDAY, SATURDAY.replace(minute=50)],
        sleeps_before_stop=1,
    )
    _run_loop(namespace)
    assert module.calls == []
    assert namespace["_pushes"] == []


def test_a_late_restart_inside_the_window_still_produces_the_day(restore_preflight) -> None:
    """A backend restarted at 09:10 must still publish the morning checklist."""
    module = _fake_module(result=_result())
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY.replace(hour=9, minute=10)],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert len(module.calls) == 1


def test_a_restart_after_the_window_does_not_push_a_stale_verdict(restore_preflight) -> None:
    """11:00 is not the morning. The answer would be about an open market."""
    module = _fake_module(result=_result())
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY.replace(hour=11, minute=0)],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert module.calls == []
    assert namespace["_pushes"] == []


def test_the_scheduled_run_heals(restore_preflight) -> None:
    """The trader asked: "if it failed it will fix automatically?"."""
    module = _fake_module(result=_result())
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert module.calls[0]["heal"] is True


def test_an_unavailable_checklist_still_pushes_once(restore_preflight) -> None:
    """Silence at 08:45 is indistinguishable from health. It must not be."""
    namespace = _namespace(
        preflight_module=None,
        moments=[MONDAY],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert len(namespace["_pushes"]) == 1
    title, body, _tags, _to = namespace["_pushes"][0]
    assert "could not run" in title
    assert body


# ---------------------------------------------------------------------------
# REVIEW FIXES (2026-09-01)
# ---------------------------------------------------------------------------


def test_the_window_closes_well_before_the_bell(restore_preflight) -> None:
    """09:29 used to be eligible: a full run + heals sixty seconds pre-open."""
    module = _fake_module(result=_result())
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY.replace(hour=9, minute=20)],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert module.calls == [], "a run at 09:20 lands on top of the open"
    assert namespace["_pushes"] == []


def test_a_restart_inside_the_window_does_not_re_run_an_archived_day(restore_preflight) -> None:
    """N restarts used to mean N full runs and N phone pushes."""
    module = _fake_module(
        result=_result(),
        # the archive already carries today's run
        history=[{"date": "2026-09-07", "at": "2026-09-07T08:45:00-04:00", "runs": 1}],
    )
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY.replace(hour=8, minute=52)],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert module.calls == [], "the archive says today already ran"
    assert namespace["_pushes"] == [], "and the phone must not buzz twice"
    assert namespace["_PREFLIGHT_DAILY"]["seededFromArchive"] is True


def test_an_empty_archive_does_not_suppress_the_morning_run(restore_preflight) -> None:
    """The seeding must not become a reason to never run."""
    module = _fake_module(result=_result(), history=[])
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY.replace(hour=8, minute=52)],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert len(module.calls) == 1
    assert namespace["_PREFLIGHT_DAILY"]["seededFromArchive"] is False


def test_yesterdays_archive_does_not_suppress_today(restore_preflight) -> None:
    module = _fake_module(
        result=_result(),
        history=[{"date": "2026-09-04", "at": "2026-09-04T08:45:00-04:00", "runs": 1}],
    )
    namespace = _namespace(
        preflight_module=module,
        moments=[MONDAY.replace(hour=8, minute=52)],
        sleeps_before_stop=0,
    )
    _run_loop(namespace)
    assert len(module.calls) == 1


# ---- warn is a finding unless it is on the allowlist ------------------


def test_an_unexpected_warn_is_pushed_with_its_number() -> None:
    """The 9.4-minute chart drift used to arrive as "AGX ready"."""
    checks = [
        {"id": "tradier", "label": "Tradier", "status": "warn", "measured": "HTTP 401, revoked"},
        {"id": "chart_freshness", "label": "Chart candles vs the broker", "status": "warn",
         "measured": "worst 9.4 min behind the broker - AAPL 9.4 min"},
        {"id": "ok", "label": "Schwab", "status": "pass", "measured": "2/2 quotes"},
    ]
    namespace = _namespace()
    title, body = namespace["_preflight_push_summary"](
        dict(_result(passed=1, warned=2, checks=checks), available=True)
    )
    assert title == "AGX preflight: 1 of 3 not passing"
    assert "9.4 min behind the broker" in body
    # the KNOWN degradation stays out of the failure count
    assert "Tradier" not in body


def test_the_expected_warn_allowlist_is_ids_not_the_whole_tier() -> None:
    namespace = _namespace()
    assert namespace["PREFLIGHT_EXPECTED_WARN_IDS"] == {"tradier"}
    assert "warn" not in namespace["PREFLIGHT_NOT_PASSING"]


def test_a_typo_ticker_reaches_the_phone() -> None:
    """GOOGLE is live on his board right now; it may not read as ready."""
    checks = [
        {"id": "invalid_grid_symbols", "label": "Saved layouts point at real tickers",
         "status": "fail",
         "measured": "9 symbols across 12 saved layouts, 1 the broker does not know: GOOGLE (Mags panel 3)"},
    ]
    namespace = _namespace()
    title, body = namespace["_preflight_push_summary"](
        dict(_result(passed=0, failed=1, checks=checks), available=True)
    )
    assert "not passing" in title
    assert "GOOGLE (Mags panel 3)" in body


# ---- the healed list is objects, not sentences ------------------------


def test_healed_dicts_render_as_readable_sentences() -> None:
    namespace = _namespace()
    entry = {
        "id": "chart_freshness",
        "label": "Chart candles vs the broker",
        "action": "asked the server to re-pull AAPL from the broker",
        "at": "2026-09-01T08:45:00-04:00",
        "statusBefore": "fail",
        "statusAfter": "pass",
        "daysRunning": 1,
    }
    line = namespace["_preflight_heal_sentence"](entry)
    assert line == (
        "Chart candles vs the broker: asked the server to re-pull AAPL from the broker "
        "(fail -> pass)"
    )
    assert "{" not in line and "'id'" not in line
    # a chronic one says so
    assert "4 days running" in namespace["_preflight_heal_sentence"](dict(entry, daysRunning=4))
    # and a plain string still works
    assert namespace["_preflight_heal_sentence"]("did a thing") == "did a thing"


def test_a_healed_dict_never_reaches_the_push_as_a_repr() -> None:
    namespace = _namespace()
    payload = dict(_result(passed=2), available=True)
    payload["healed"] = [{
        "id": "chart_freshness", "label": "Chart candles vs the broker",
        "action": "re-pulled AAPL", "statusBefore": "fail", "statusAfter": "pass",
        "daysRunning": 1,
    }]
    _title, body = namespace["_preflight_push_summary"](payload)
    assert "re-pulled AAPL" in body
    assert "statusBefore" not in body


# ---- the push is admin-only ------------------------------------------


def test_the_daily_push_goes_only_to_active_admins(restore_preflight) -> None:
    """The verdict names credential states and dead ports. Admin only."""
    module = _fake_module(result=_result())
    namespace = _namespace(preflight_module=module, moments=[MONDAY], sleeps_before_stop=0)
    _run_loop(namespace)
    assert len(namespace["_pushes"]) == 1
    _title, _body, _tags, recipients = namespace["_pushes"][0]
    assert recipients == {"admin@example.com"}
    assert "user@example.com" not in recipients
    assert "old-admin@example.com" not in recipients, "a deactivated admin is not an admin"
    assert namespace["_PREFLIGHT_DAILY"]["lastPushRecipients"] == 1


def test_admin_emails_are_empty_when_auth_cannot_answer() -> None:
    """Empty means NOBODY, which is the safe direction for a diagnostic."""
    namespace = _namespace()

    def boom():
        raise RuntimeError("database is locked")

    namespace["auth_service_instance"] = boom
    assert namespace["_preflight_admin_emails"]() == set()


# ---- elapsedMs survives the archive -----------------------------------


def test_the_same_run_is_merged_not_chosen_between(restore_preflight) -> None:
    """The archive is written BEFORE elapsedMs is stamped; merge, don't pick."""
    stamp = "2026-09-07T08:45:00-04:00"
    archived = dict(_result(at=stamp), worst="fail", worstAt=stamp,
                    checkWorst={"c0": "fail"}, healedIds=["c0"], runs=2, date="2026-09-07")
    module = _fake_module(result=None, history=[archived])
    namespace = _namespace(preflight_module=module, moments=[MONDAY], sleeps_before_stop=0)
    namespace["_PREFLIGHT_LAST_RESULT"]["result"] = dict(
        _result(at=stamp), available=True, elapsedMs=14210, healEnabled=True
    )
    payload = namespace["_preflight_payload"](30)
    result = payload["result"]
    assert result["elapsedMs"] == 14210, "the in-process copy owns how long it took"
    assert result["worst"] == "fail", "the archive owns the day's memory"
    assert result["healedIds"] == ["c0"]
    assert result["runs"] == 2


# ---- threadStarted is an observation ----------------------------------


def test_thread_started_is_read_from_the_thread_not_a_flag() -> None:
    namespace = _namespace()
    # nothing started: the flag would have been False anyway
    assert namespace["_preflight_payload"](30)["schedule"]["threadStarted"] is False
    # now claim "started" the way the old boolean did, without a live thread
    namespace["_PREFLIGHT_DAILY"]["started"] = True
    assert namespace["_preflight_payload"](30)["schedule"]["threadStarted"] is False, (
        "a dead scheduler thread must not keep reporting True"
    )


def test_a_fix_that_changed_nothing_is_not_reported_as_a_fix() -> None:
    """heal_momx_scanner can never report a post-heal pass by construction."""
    namespace = _namespace()
    worked = {"id": "chart_freshness", "label": "Chart candles vs the broker",
              "action": "re-pulled AAPL", "statusBefore": "fail", "statusAfter": "pass"}
    failed = {"id": "momx_scanner", "label": "MomX scanner board",
              "action": "queued a rebuild of the Mag7 board",
              "statusBefore": "fail", "statusAfter": "fail"}
    crashed = dict(worked, statusAfter="unknown")
    assert namespace["_preflight_heal_worked"](worked) is True
    assert namespace["_preflight_heal_worked"](failed) is False
    assert namespace["_preflight_heal_worked"](crashed) is False

    payload = dict(_result(passed=1, failed=1, checks=[
        {"id": "momx_scanner", "label": "MomX scanner board", "status": "fail",
         "measured": "board 41 min old"},
        {"id": "ok", "label": "Schwab", "status": "pass", "measured": "2/2"},
    ]), available=True)
    payload["healed"] = [worked, failed]
    _title, body = namespace["_preflight_push_summary"](payload)
    assert "Auto-fixed: Chart candles vs the broker: re-pulled AAPL (fail -> pass)" in body
    assert "Tried and did NOT fix: MomX scanner board" in body
    # the failed one must NOT be listed as fixed
    assert "Auto-fixed" not in body.split("Tried and did NOT fix")[1]
