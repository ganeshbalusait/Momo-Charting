

def test_history_keeps_the_skittles_and_squeeze_timeline_items_for_the_setup_tags():
    """2026-10-10 MRNA: the live Setup showed "SKIT↑ 2h 09:30" but History a bare
    "A" - the stored row lost the timeline the tag is built from."""
    from momx import history
    row = {"symbol": "MRNA", "gradeFresh": {"icons": ["SKIT"], "timeline": [
        {"at": "2026-10-09T09:30:16-04:00", "what": "SKIT 2h bg green"},
        {"at": "2026-10-09T09:30:16-04:00", "what": "RVOL 5m bg cyan"},
        {"at": "2026-10-09T10:07:00-04:00", "what": "SQZ 2h released"},
    ]}}
    kept = history._archive_row(row, strip_bulk=True)["gradeFresh"]["timeline"]
    assert [e["what"] for e in kept] == ["SKIT 2h bg green", "SQZ 2h released"]
    assert len(row["gradeFresh"]["timeline"]) == 3  # the live row is never touched
    bare = history._archive_row({"gradeFresh": {"timeline": [{"what": "ADX 5m cross"}]}}, strip_bulk=True)
    assert "timeline" not in bare["gradeFresh"]


def test_past_days_get_the_recorded_skit_times_at_read_time_without_rewriting_files(tmp_path):
    """Days archived before the timeline was kept: SKIT times come from the grade
    log's recorded bgChangedAt, added when History is read. The file is untouched."""
    import json
    from momx import history
    hist = tmp_path / "momx_history" / "Watchlist"
    hist.mkdir(parents=True)
    day = "2026-10-09"
    doc = {"list": "Watchlist", "date": day, "rows": {"MRNA": {"firstSeenAt": "x", "lastSeenAt": "x", "hits": 2,
           "truncated": False, "snapshots": [
               {"at": "2026-10-09T09:20:00-04:00", "changed": [], "row": {"symbol": "MRNA"}},
               {"at": "2026-10-09T09:31:04-04:00", "changed": [], "row": {"symbol": "MRNA", "gradeFresh": {"icons": []}}}]}}}
    (hist / f"{day}.json").write_text(json.dumps(doc), encoding="utf-8")
    before = (hist / f"{day}.json").read_bytes()
    ev = tmp_path / "momx_grade_events" / "Watchlist"
    ev.mkdir(parents=True)
    (ev / f"{day}.json").write_text(json.dumps({"events": [
        {"symbol": "MRNA", "skittles": {
            "2h": {"bg": "green", "bgChangedAt": "2026-10-09T09:30:16-04:00"},
            "D": {"bg": "black", "bgChangedAt": "2026-10-09T09:30:16-04:00"},
            "4h": {"bg": "cyan", "bgChangedAt": "2026-10-08T15:00:00-04:00"}}}]}), encoding="utf-8")
    for payload in (history.history_response("Watchlist", date=day, directory=tmp_path / "momx_history"),
                    history.history_response("Watchlist", date=day, symbol="MRNA", directory=tmp_path / "momx_history")):
        rows = sorted(payload["rows"], key=lambda r: r["at"])
        assert "timeline" not in rows[0]["row"].get("gradeFresh", {})  # 09:20 is before the cross
        assert rows[1]["row"]["gradeFresh"]["timeline"] == [{"at": "2026-10-09T09:30:16-04:00", "what": "SKIT 2h bg green"}]
        assert rows[1]["row"]["gradeFresh"]["icons"] == []
    assert (hist / f"{day}.json").read_bytes() == before


def test_history_keeps_only_the_di_values_behind_a_strong_mom_tag():
    from momx import history
    row = {"symbol": "X", "adx": {"5m": {"adx": 30, "plus": 31.0, "minus": 12.0, "bg": "cyan", "barAt": 1},
                                  "30m": {"adx": 28, "plus": 27.0, "minus": 15.0}, "1h": {"plus": 1}}}
    assert history._archive_row(row, strip_bulk=True)["adx"] == {
        "5m": {"plus": 31.0, "minus": 12.0}, "30m": {"plus": 27.0, "minus": 15.0}}


def test_past_days_get_squeeze_fires_from_the_stored_boxes(tmp_path):
    """MRNA 2h 2026-10-09: white 09:41 -> orange 09:47 = a HIGH squeeze fired; a
    box that ARRIVES already fired (no earlier snapshot) gets nothing."""
    import json
    from momx import history
    hist = tmp_path / "momx_history" / "Watchlist"
    hist.mkdir(parents=True)
    day = "2026-10-09"
    snap = lambda at, bg: {"at": f"{day}T{at}:00-04:00", "changed": [], "row": {"symbol": "MRNA", "sqz": {"2h": {"bg": bg}}}}
    doc = {"list": "Watchlist", "date": day, "rows": {
        "MRNA": {"firstSeenAt": "x", "lastSeenAt": "x", "hits": 3, "truncated": False,
                 "snapshots": [snap("09:31", "white"), snap("09:41", "white"), snap("09:47", "orange")]},
        "X": {"firstSeenAt": "x", "lastSeenAt": "x", "hits": 1, "truncated": False,
              "snapshots": [{"at": f"{day}T09:35:00-04:00", "changed": [], "row": {"symbol": "X", "sqz": {"2h": {"bg": "cyan"}}}}]}}}
    (hist / f"{day}.json").write_text(json.dumps(doc), encoding="utf-8")
    rows = history.history_response("Watchlist", date=day, directory=tmp_path / "momx_history")["rows"]
    tl = {(r["symbol"], r["at"][11:16]): (r["row"].get("gradeFresh") or {}).get("timeline", []) for r in rows}
    assert tl[("MRNA", "09:41")] == []
    assert tl[("MRNA", "09:47")] == [{"at": f"{day}T09:47:00-04:00", "what": "SQZ 2h released"}]
    assert tl[("X", "09:35")] == []


def test_reusing_the_last_written_document_writes_the_same_file(tmp_path):
    """2026-10-16: the recorder re-parsed its own day file every build (~11% of
    the worker GIL). Reusing what it just wrote must change no byte."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from momx import history as h
    et = ZoneInfo("America/New_York")
    boards = []
    for i in range(4):
        boards.append({"rows": [
            {"symbol": "AAA", "scanPass": True, "rvol": {"5m": {"value": 2.0 + i, "bg": "green"}}},
            {"symbol": "BBB", "scanPass": i % 2 == 0, "rvol": {"5m": {"value": 1.0, "bg": "red"}}},
        ]})
    out = {}
    for mode in ("cached", "disk"):
        directory = tmp_path / mode
        h._LAST_WRITE.clear()
        for i, board in enumerate(boards):
            if mode == "disk":
                h._WRITTEN.clear()
            h.record_board("Watchlist", board, now_et=datetime(2026, 10, 16, 10, 0, tzinfo=et) + timedelta(minutes=10 * i),
                           directory=directory)
        out[mode] = (directory / "Watchlist" / "2026-10-16.json").read_bytes()
    assert out["cached"] == out["disk"]
