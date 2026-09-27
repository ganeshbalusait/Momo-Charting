from __future__ import annotations

import math

import numpy as np
import pandas as pd

from api_server import _serialize_value


def test_plain_scalars_pass_through():
    assert _serialize_value("AAPL") == "AAPL"
    assert _serialize_value(7) == 7
    assert _serialize_value(True) is True
    assert _serialize_value(None) is None
    assert _serialize_value(301.40) == 301.40


def test_float_nan_and_inf_become_none():
    # pd.isna(float("nan")) is True, so the old path returned None. Keep that.
    assert _serialize_value(float("nan")) is None
    # pd.isna(inf) is False, so inf must survive as-is.
    assert _serialize_value(float("inf")) == float("inf")


def test_numpy_and_pandas_values_still_convert():
    assert _serialize_value(np.int64(5)) == 5
    assert _serialize_value(np.float64(1.5)) == 1.5
    assert _serialize_value(pd.NA) is None
    # pd.NaT is a datetime subclass, so it reaches the isoformat branch before
    # the NA check and serializes as the string "NaT". That is what this code
    # has always done; the fast path must not quietly change it.
    assert _serialize_value(pd.NaT) == "NaT"


def test_numpy_scalars_keep_their_existing_nan_handling():
    # np.float64 IS a subclass of float and np.generic is checked BEFORE the
    # pd.isna() call, so a numpy NaN currently survives as a float NaN rather
    # than becoming None. An isinstance-based fast path would capture it and
    # flip it to None; exact-type checks must not.
    result = _serialize_value(np.float64("nan"))
    assert isinstance(result, float)
    assert math.isnan(result)
    # np.int64/np.bool_ are not Python int/bool subclasses, so they must still
    # be unwrapped by the np.generic branch - returning them raw would make
    # json.dumps raise.
    assert type(_serialize_value(np.int64(5))) is int
    assert type(_serialize_value(np.bool_(True))) is bool


def test_nested_structures_still_recurse():
    payload = {"bars": [{"time": 1, "close": float("nan")}, {"time": 2, "close": 3.5}]}
    assert _serialize_value(payload) == {
        "bars": [{"time": 1, "close": None}, {"time": 2, "close": 3.5}]
    }


def test_bool_is_not_treated_as_int_shortcut():
    # bool is a subclass of int; make sure the fast path keeps it a bool.
    result = _serialize_value(False)
    assert result is False
    assert isinstance(result, bool)
