"""Run calculation subprocesses at BELOW_NORMAL, level with api_server.

IDLE was tried first (2026-11-02) and starved the scan completely: with two
thinkorswim windows, Chrome and the app server holding ~11 of 12 cores at the
open, the five IDLE children got ~0.01 core each, Mag7 (10 names) took 16 min
and the Watchlist board sat on 09:25 premarket candles - every RVOL stale.
BELOW_NORMAL matches api_server/gateway, so the scan gets a fair share but
never outranks the chart API or the trader's own apps (all NORMAL).
"""
import sys

BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def idle_calculation_worker():
    if sys.platform != 'win32':
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.SetPriorityClass.restype = ctypes.c_int
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
        raise OSError('Could not set calculation worker to below-normal priority')
