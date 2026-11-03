"""Run the scanner's calculation subprocesses at NORMAL, without power throttling.

History (all 2026-11-02, the i7-1355U laptop with thinkorswim open):
- IDLE starved the scan completely: the five IDLE children got ~0.01 core
  each, Mag7 (10 names) took 16 min and the Watchlist sat on 09:25 candles.
- BELOW_NORMAL was next. Measured at 17:15 CT with the machine at 80% (20%
  free): every scan lane got exactly ~0.19 core. Raised to NORMAL the same
  lanes got ~0.5 core each, 2.5x; a Watchlist pass took 3.5 min and he asked
  for under 1 min. Windows 11 also applies EcoQoS (efficiency-mode) throttling
  to background-priority processes, so the opt-out below is set as well.
NORMAL is level with api_server and the gateway (the watchdog runs those at
NORMAL), so charts and the scan share the CPU evenly instead of the scan
getting leftovers.
"""
import sys

NORMAL_PRIORITY_CLASS = 0x0020
BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def set_current_process(priority_class: int = NORMAL_PRIORITY_CLASS) -> None:
    """Set this process's priority class and opt it out of power throttling.

    Raises OSError if the priority cannot be set; the throttling opt-out is
    best effort (older Windows builds do not have it).
    """
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.SetPriorityClass.restype = ctypes.c_int
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), priority_class):
        raise OSError('Could not set the scanner process priority')

    class _Throttle(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    try:
        kernel.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetProcessInformation.restype = ctypes.c_int
        # ProcessPowerThrottling = 4; EXECUTION_SPEED controlled, state off.
        state = _Throttle(1, 0x1, 0)
        kernel.SetProcessInformation(kernel.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state))
    except Exception:  # noqa: BLE001 - throttling opt-out is a nicety
        pass


def idle_calculation_worker():
    """Pool initializer (name kept for the existing call sites)."""
    set_current_process(NORMAL_PRIORITY_CLASS)
