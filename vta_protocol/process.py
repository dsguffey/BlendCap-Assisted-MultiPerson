"""Provider parent-loss guard. SPDX-License-Identifier: CC0-1.0

Windows uses a process HANDLE, avoiding PID reuse. Jobs intentionally survive
Blender exit; providers must not survive their owning job process. No signals are
sent to a PID obtained from a saved project/status record.
"""
import os,threading,time

def watch_parent(pid, *, interval=.5, exit_process=os._exit):
    if type(pid) is not int or pid<=0:raise ValueError('Invalid live parent identity.')
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
        handle=kernel.OpenProcess(0x100000,False,pid)
        if not handle:exit_process(73);return
        def run():
            try:
                result=kernel.WaitForSingleObject(handle,0xffffffff)
                if result==0:exit_process(73)
            finally:kernel.CloseHandle(handle)
    else:
        # A direct child's parent changes to the system reaper when orphaned;
        # no kill(pid,0) probe that could accidentally accept PID reuse.
        def run():
            while os.getppid()==pid:time.sleep(interval)
            exit_process(73)
    thread=threading.Thread(target=run,name='vta-parent-watch',daemon=True);thread.start()
    return thread
