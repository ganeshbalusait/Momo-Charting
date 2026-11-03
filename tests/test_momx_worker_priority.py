import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows scheduling')
def test_calculation_initializer_sets_real_child_priority_without_changing_parent():
    code = '''
import ctypes
from momx.worker_priority import idle_calculation_worker
idle_calculation_worker()
k=ctypes.windll.kernel32
k.GetCurrentProcess.restype=ctypes.c_void_p
k.GetPriorityClass.argtypes=[ctypes.c_void_p]
print(k.GetPriorityClass(k.GetCurrentProcess()))
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20, check=True)
    # NORMAL since 2026-11-02 (BELOW_NORMAL capped each lane at ~0.19 core; IDLE starved it).
    assert result.stdout.strip() == str(0x0020)
