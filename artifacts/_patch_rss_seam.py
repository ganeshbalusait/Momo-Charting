import io
p = "momx/news.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = """    now: Any = None,
    get: Callable | None = None,
) -> dict[str, dict]:"""
NEW = """    now: Any = None,
    get: Callable | None = None,
    rss_get: Callable | None = None,
) -> dict[str, dict]:"""
assert s.count(OLD) == 1, "signature anchor"
s = s.replace(OLD, NEW)

OLD2 = """        missing = [symbol for symbol in wanted if symbol not in result]
        if missing:
            backfilled = _rss_backfill(
                missing, cutoff, get if get is not None else _default_get(),
            )
            if backfilled:
                result = {**backfilled, **result}"""
NEW2 = """        # The RSS getter is a SEPARATE seam from ``get`` on purpose. ``get`` is
        # Benzinga-shaped (called with params= and credential headers) and the
        # existing tests inject it and count its calls; reusing it for a
        # per-ticker RSS URL would both break those counts and hand the wrong
        # request shape to a stub. So RSS runs with its own getter: the real
        # one in production (``get`` unset), and only an explicitly injected
        # ``rss_get`` under test - never the live network from a unit test.
        missing = [symbol for symbol in wanted if symbol not in result]
        if missing and (rss_get is not None or get is None):
            backfilled = _rss_backfill(
                missing, cutoff, rss_get if rss_get is not None else _default_get(),
            )
            if backfilled:
                result = {**backfilled, **result}"""
assert s.count(OLD2) == 1, "wiring anchor"
s = s.replace(OLD2, NEW2)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("rss_get seam added")
