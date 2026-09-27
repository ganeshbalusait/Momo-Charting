"""One process-wide gate in front of every Schwab REST request.

WHY THIS EXISTS - two IP bans in two days, from opposite causes.

2026-09-03: the MomX worker hammered token-refresh with an EXPIRED token, 39
connections per 20s, and by morning Akamai answered every request from this
address with "Access Denied" - including the OAuth exchange needed to recover.
The fix for that one was `_refuse_if_refresh_token_dead`
(data/schwab_client.py:421): a known-dead token must cost nothing on the wire.

2026-09-04, 01:05-02:15 ET: the same total block, but the token was VALID the
whole time (7.0 days left, and it had refreshed successfully at 00:51). So that
guard never engaged. Nothing was wrong except VOLUME. Measured on the way out,
the recovery order was:

    no key -> 401 (normal)    with key -> 429 Too Many Requests    10s later -> 200

A 429 is the warning shot; the hour-long Akamai ban is what comes next. Schwab
documents 120 requests/minute and says to back off 60 seconds on a 429. Nothing
in this codebase limited the combined rate: /api/live-chart-quotes alone polls
about once a second for as long as any chart is open, with only a 0.75s cache in
front of it, and chart builds, chains and the premarket warmer all add to it.

WHY IT IS PROCESS-WIDE AND NOT PER CLIENT. Akamai bans the IP, not the app key.
The two Schwab apps (market_data and trading) plus every per-user client share
one address, so a limiter scoped to a SchwabClient instance would count a
fraction of the traffic that actually gets us banned. The gate below is a module
singleton for exactly that reason.

WHAT IT DELIBERATELY DOES NOT DO: block a web-server thread for long. A request
waits a short bounded time for a slot and then raises SchwabUnavailable, so the
caller falls back to its cache immediately instead of piling up threads behind a
provider that is already refusing us. Stalling is how a rate limit becomes an
outage.
"""
from __future__ import annotations

import threading
import time
from collections import deque

#: A RUNAWAY BACKSTOP, deliberately far above normal traffic - not the real
#: ceiling, and this needs explaining because the number looks wrong.
#:
#: Schwab documents 120 requests/minute. This app measurably exceeds that all
#: day: the MomX worker issues one pricehistory call per symbol per timeframe
#: on every board build - 358 symbols x 2 timeframes = 716 requests every 93.6s,
#: which is 459/minute, sustained, around the clock (measured 2026-09-04). That
#: is the traffic that earned the Akamai ban, and the real fix is to stop making
#: those calls, not to queue them.
#:
#: But setting this to 120 tonight would silently starve that worker of the
#: volume it corrects RVOL with, and RVOL is the number the trader actually
#: trades on. Throttling it at 3am, hours before the open, would trade a
#: visible outage for an invisible wrong number - the worse of the two.
#:
#: So the gate does the two things that are safe without touching MomX: it
#: MEASURES the true rate (see requestsLastMinute in state()), and it STOPS
#: everything the moment Schwab itself pushes back. Lowering this to 120 is the
#: correct end state and must follow the MomX fix, not precede it.
MAX_REQUESTS_PER_MINUTE = 1200

#: A burst is fine; a sustained rate is not. Opening a 6-panel chart grid
#: legitimately fires a cluster of pricehistory calls at once, and throttling
#: that to a trickle would make the app feel broken for no safety gain. The
#: bucket allows the cluster, then enforces the average.
BURST_CAPACITY = 30

#: How long a caller will wait for a slot before being told to use its cache.
#: Deliberately short - see the module docstring on not stalling threads.
MAX_WAIT_SECONDS = 0.5

#: 429 is Schwab telling us the rate is too high. Their guidance is 60 seconds,
#: and that is where this ends up - but not on the first one.
#:
#: A blanket 60-second global pause on a single 429 would freeze every chart on
#: screen for a minute, during the session, which is its own outage. Escalating
#: means an isolated 429 costs a blink while a genuine sustained overrun reaches
#: Schwab's full 60 seconds within three strikes. The counter resets on any
#: success, so "occasionally brushing the limit" and "hammering through it"
#: are treated differently - which they are.
RATE_LIMIT_COOLDOWN_LADDER = (5.0, 15.0, 60.0)

#: An Akamai "Access Denied" is not a rate limit, it is a ban already in force.
#: Requests sent during one cannot succeed, and there is good reason to think
#: continuing to send them is what extends it - so the cooldown is much longer
#: and escalates while the block persists. 300s matches the connection-limit
#: backoff alpaca_stream.py already uses for the same class of refusal.
BLOCK_COOLDOWN_SECONDS = 300.0
BLOCK_COOLDOWN_MAX_SECONDS = 1800.0


class SchwabUnavailable(RuntimeError):
    """Raised INSTEAD of sending a request we already know will fail or hurt.

    Carries `kind` so a caller (and the status endpoint) can tell the three
    apart rather than seeing one undifferentiated failure:

        "blocked"     Akamai is refusing this IP outright
        "rate_limit"  Schwab returned 429 recently; we are serving the cooldown
        "throttled"   our own gate is full; no slot came free in time
    """

    def __init__(self, message: str, kind: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = float(retry_after)


def looks_like_edge_block(status_code: int, body: str) -> bool:
    """Is this an Akamai edge refusal rather than a real Schwab answer?

    The distinction matters and is easy to get wrong: a genuine Schwab 403 is
    JSON and means something about the account or entitlement, while an edge
    block is an HTML page served before Schwab ever sees the request. Treating
    the second as the first sends a trader to re-authenticate, which cannot
    possibly help - the OAuth endpoint is behind the same block.

    Measured signature from 2026-09-04, identical on every path including the
    bare host root:

        <HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>
        <H1>Access Denied</H1> You don't have permission to access ...
        Reference #18.983b2f17.1788501265.3ecb85c3
        https://errors.edgesuite.net/18.983b2f17.1788501265.3ecb85c3
    """
    if int(status_code or 0) not in (401, 403):
        return False
    text = str(body or "")[:2000].lower()
    return "access denied" in text or "edgesuite" in text


class SchwabGate:
    """Token bucket + cooldown, shared by every Schwab client in the process."""

    def __init__(
        self,
        max_per_minute: int = MAX_REQUESTS_PER_MINUTE,
        burst: int = BURST_CAPACITY,
        clock=time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._max_per_minute = int(max_per_minute)
        self._burst = int(burst)
        self._sent: deque = deque()
        self._cooldown_until = 0.0
        self._cooldown_kind = ""
        self._cooldown_detail = ""
        self._consecutive_blocks = 0
        self._consecutive_rate_limits = 0
        self._last_success_at = 0.0
        self._last_failure_at = 0.0
        self._last_failure_kind = ""
        self._last_failure_detail = ""
        self._sent_total = 0
        self._refused_total = 0

    # -- the gate ---------------------------------------------------------
    def acquire(self, wait: float = MAX_WAIT_SECONDS) -> None:
        """Claim a slot, or raise SchwabUnavailable without sending anything.

        Sleeping happens OUTSIDE the lock: holding it across a sleep would
        serialise every thread behind the first waiter and turn a throttle into
        a stall.
        """
        deadline = self._clock() + max(float(wait), 0.0)
        while True:
            with self._lock:
                now = self._clock()
                if now < self._cooldown_until:
                    self._refused_total += 1
                    remaining = self._cooldown_until - now
                    raise SchwabUnavailable(
                        "Schwab is refusing this address (%s); no request sent. "
                        "Retrying in %.0fs. %s"
                        % (self._cooldown_kind or "cooling down", remaining, self._cooldown_detail),
                        kind=self._cooldown_kind or "blocked",
                        retry_after=remaining,
                    )
                self._expire(now)
                if len(self._sent) < self._capacity():
                    self._sent.append(now)
                    self._sent_total += 1
                    return
                # How long until the oldest request ages out of the window.
                sleep_for = max(60.0 - (now - self._sent[0]), 0.0)
            if self._clock() + sleep_for > deadline:
                with self._lock:
                    self._refused_total += 1
                raise SchwabUnavailable(
                    "Schwab request rate is at its ceiling (%d/min); no slot came "
                    "free in %.2fs, so the cached value is being used instead."
                    % (self._max_per_minute, max(float(wait), 0.0)),
                    kind="throttled",
                    retry_after=sleep_for,
                )
            time.sleep(min(sleep_for, 0.05))

    def _capacity(self) -> int:
        return max(self._max_per_minute, self._burst)

    def _expire(self, now: float) -> None:
        while self._sent and (now - self._sent[0]) >= 60.0:
            self._sent.popleft()

    # -- recording what came back ----------------------------------------
    def note_success(self) -> None:
        with self._lock:
            self._last_success_at = self._clock()
            self._consecutive_blocks = 0
            # A success proves we are inside the limit again - see the ladder.
            self._consecutive_rate_limits = 0
            # A success proves the block is over. Clearing it here is what lets
            # the app heal on its own rather than serving a stale cooldown.
            if self._cooldown_kind in ("blocked", "rate_limit"):
                self._cooldown_until = 0.0
                self._cooldown_kind = ""
                self._cooldown_detail = ""

    def note_rate_limited(self, retry_after: float = 0.0) -> None:
        """Schwab said 429. Stop for a while - this is the warning shot.

        Escalates along RATE_LIMIT_COOLDOWN_LADDER for CONSECUTIVE 429s and
        resets on any success, so brushing the limit costs a blink and running
        through it reaches Schwab's documented 60 seconds quickly. An explicit
        Retry-After is always honoured and never shortened.
        """
        with self._lock:
            self._consecutive_rate_limits += 1
            step = RATE_LIMIT_COOLDOWN_LADDER[
                min(self._consecutive_rate_limits, len(RATE_LIMIT_COOLDOWN_LADDER)) - 1
            ]
            pause = max(float(retry_after or 0.0), step)
            self._arm(
                "rate_limit",
                pause,
                "Schwab returned 429 Too Many Requests (%d in a row). Backing off "
                "%.0fs; ignoring this is what precedes an IP block."
                % (self._consecutive_rate_limits, pause),
            )

    def note_edge_block(self, detail: str = "") -> None:
        """Akamai refused us. Escalate, because sending more prolongs it."""
        with self._lock:
            self._consecutive_blocks += 1
            pause = min(
                BLOCK_COOLDOWN_SECONDS * (2 ** (self._consecutive_blocks - 1)),
                BLOCK_COOLDOWN_MAX_SECONDS,
            )
            self._arm(
                "blocked",
                pause,
                detail
                or "Schwab's edge (Akamai) returned Access Denied for this IP. "
                "This is not a login problem and re-authenticating cannot fix it - "
                "the OAuth endpoint is behind the same block.",
            )

    def note_failure(self, kind: str, detail: str) -> None:
        """A failure worth remembering that does not itself arm a cooldown."""
        with self._lock:
            self._last_failure_at = self._clock()
            self._last_failure_kind = str(kind)
            self._last_failure_detail = str(detail)[:400]

    def _arm(self, kind: str, seconds: float, detail: str) -> None:
        now = self._clock()
        self._cooldown_until = max(self._cooldown_until, now + float(seconds))
        self._cooldown_kind = kind
        self._cooldown_detail = detail
        self._last_failure_at = now
        self._last_failure_kind = kind
        self._last_failure_detail = detail[:400]

    def reset(self) -> None:
        """Forget everything - cooldowns, counters, the request window.

        Exists for tests. The gate is a process-wide singleton on purpose
        (Akamai bans the IP, not the app key), but that means one test arming a
        cooldown would refuse every Schwab call in every test after it, which
        showed up as unrelated tests failing in a full run and passing alone.
        Production never calls this: a cooldown must outlive whatever armed it.
        """
        with self._lock:
            self._sent.clear()
            self._cooldown_until = 0.0
            self._cooldown_kind = ""
            self._cooldown_detail = ""
            self._consecutive_blocks = 0
            self._consecutive_rate_limits = 0
            self._last_success_at = 0.0
            self._last_failure_at = 0.0
            self._last_failure_kind = ""
            self._last_failure_detail = ""
            self._sent_total = 0
            self._refused_total = 0

    # -- what the status endpoint reads ----------------------------------
    def state(self) -> dict:
        """An OBSERVATION of whether Schwab is answering, not an intention.

        The whole point: during the 2026-09-04 outage /api/schwab/status said
        healthy:true and the header lamp stayed green, because every field it
        reported described the TOKEN. These fields describe what actually
        happened on the wire.
        """
        with self._lock:
            now = self._clock()
            cooling = now < self._cooldown_until
            # Whether Schwab is ANSWERING, stated as an observation. Three cases,
            # spelled out rather than folded into one expression - this is the
            # field the header lamp will trust, and the whole bug being fixed
            # here was a status field that looked right and meant nothing.
            if cooling:
                reachable = False          # we are refusing to send; not reachable
            elif self._last_failure_at == 0.0:
                reachable = True           # nothing has ever failed
            else:
                reachable = self._last_success_at >= self._last_failure_at
            return {
                "reachable": reachable,
                # Has anything been observed AT ALL yet? Without this, a
                # freshly-restarted server reports reachable:true having never
                # made a call - "true because nothing has failed" is the same
                # empty reassurance as the token-derived healthy:true that let
                # tonight's 70-minute outage show a green lamp. A consumer that
                # cares about the difference can now tell "good" from "unknown".
                "observed": bool(self._last_success_at or self._last_failure_at),
                "coolingDown": cooling,
                "cooldownKind": self._cooldown_kind if cooling else "",
                "cooldownRemainingSeconds": max(self._cooldown_until - now, 0.0) if cooling else 0.0,
                "cooldownDetail": self._cooldown_detail if cooling else "",
                "secondsSinceSuccess": (now - self._last_success_at) if self._last_success_at else None,
                "secondsSinceFailure": (now - self._last_failure_at) if self._last_failure_at else None,
                "lastFailureKind": self._last_failure_kind,
                "lastFailureDetail": self._last_failure_detail,
                "requestsLastMinute": len([t for t in self._sent if (now - t) < 60.0]),
                "maxRequestsPerMinute": self._max_per_minute,
                "requestsSent": self._sent_total,
                "requestsRefused": self._refused_total,
            }


#: The one gate. Imported by data.schwab_client; exposed to the status endpoint.
GATE = SchwabGate()
