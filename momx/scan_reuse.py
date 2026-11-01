"""Bounded reuse of unchanged, old scanner tapes within one worker process."""
import copy
import hashlib
import pickle
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

_CACHE = OrderedDict()


def run(args, calculate, newest, now):
    # Never cache a developing/live session. Old input retains its original
    # timestamps; this does not advance the scanner's observation history.
    stamps = [newest(frame) for frame in args[1:4]]
    if len(stamps) != 3 or any(stamp is None or now - stamp <= 86400 for stamp in stamps):
        return calculate(args)
    try:
        day = datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date()
        digest = hashlib.sha256(pickle.dumps((day, args), protocol=5)).digest()
    except Exception:
        return calculate(args)
    key = (args[0], args[6] if len(args) > 6 else True)
    previous = _CACHE.get(key)
    if previous and previous[0] == digest:
        _CACHE.move_to_end(key)
        return copy.deepcopy(previous[1])
    result = calculate(args)
    # A successful scan may deliberately omit display columns for nonmatches.
    # Failed calculations must be retried, never made sticky.
    if result[5] is None:
        _CACHE[key] = (digest, copy.deepcopy(result))
        _CACHE.move_to_end(key)
        while len(_CACHE) > 2048:
            _CACHE.popitem(last=False)
    return result
