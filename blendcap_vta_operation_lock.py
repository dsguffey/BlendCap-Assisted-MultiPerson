"""Worker ownership and conservative crash recovery for the existing project lock.

No project schema changes. Polls inspect only; explicit commands/workers recover
provably abandoned locks. Unknown lock formats and uncertain owners stay blocked.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from uuid import uuid4


def process_alive(pid):
    if not isinstance(pid,int) or pid<=0:return True
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as w
        dll=ctypes.WinDLL('kernel32',use_last_error=True)
        dll.OpenProcess.argtypes=(w.DWORD,w.BOOL,w.DWORD);dll.OpenProcess.restype=w.HANDLE
        dll.GetExitCodeProcess.argtypes=(w.HANDLE,ctypes.POINTER(w.DWORD))
        dll.CloseHandle.argtypes=(w.HANDLE,)
        handle=dll.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()!=87 # Invalid PID, not permission denied.
        try:
            code=w.DWORD()
            return not dll.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:dll.CloseHandle(handle)
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except PermissionError:return True
    return True


def _windows_exclusive(path,expected,remove=False):
    """Check/delete the exact file by exclusive handle; never unlink a replacement.

    Legacy reconstruction/private mutations hold their CRT descriptor throughout
    work. Windows share exclusion proves that an empty legacy lock is not held.
    """
    import ctypes
    from ctypes import wintypes as w
    dll=ctypes.WinDLL('kernel32',use_last_error=True)
    dll.CreateFileW.argtypes=(w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE)
    dll.CreateFileW.restype=w.HANDLE
    dll.ReadFile.argtypes=(w.HANDLE,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD),w.LPVOID)
    dll.SetFileInformationByHandle.argtypes=(w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD)
    dll.CloseHandle.argtypes=(w.HANDLE,)
    absolute=str(Path(path).absolute())
    absolute=('\\\\?\\UNC\\'+absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\'+absolute)
    handle=dll.CreateFileW(absolute,0x80000000|(0x10000 if remove else 0),0,None,3,0,None)
    if handle==ctypes.c_void_p(-1).value:return False
    try:
        data=ctypes.create_string_buffer(len(expected)+1);count=w.DWORD()
        if not dll.ReadFile(handle,data,len(expected)+1,ctypes.byref(count),None):return False
        if data.raw[:count.value]!=expected:return False
        if not remove:return True
        disposition=w.BOOL(True)
        return bool(dll.SetFileInformationByHandle(handle,4,ctypes.byref(disposition),ctypes.sizeof(disposition)))
    finally:dll.CloseHandle(handle)


def state(path):
    path=Path(path)
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)():return 'unknown'
    try:raw=path.read_bytes()
    except FileNotFoundError:return 'idle'
    except OSError:return 'unknown'
    if not raw:
        return 'stale' if os.name=='nt' and _windows_exclusive(path,raw) else 'unknown'
    try:
        owner=json.loads(raw)
        pid=owner if type(owner) is int else owner['pid'] if owner.get('version')==1 else None
        if type(pid) is not int or pid<=0:return 'unknown'
    except (ValueError,TypeError,KeyError,AttributeError):return 'unknown'
    return 'active' if process_alive(pid) else 'stale'


def _remove_exact(path,raw):
    if os.name=='nt':return _windows_exclusive(path,raw,True)
    import fcntl
    try:
        with Path(path).open('rb') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if os.fstat(handle.fileno()).st_ino!=Path(path).stat().st_ino or handle.read()!=raw:return False
            Path(path).unlink();return True
    except (OSError,FileNotFoundError):return False


def recover(path):
    """Explicit mutation boundary only, never a draw/poll side effect."""
    path=Path(path)
    try:raw=path.read_bytes()
    except FileNotFoundError:return True
    if state(path)!='stale':return False
    return _remove_exact(path,raw)


@contextmanager
def acquire(path,*,operation='generation',request_id=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    recover(path)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    raw=json.dumps(dict(version=1,pid=os.getpid(),token=uuid4().hex,
        operation=operation,request_id=request_id),sort_keys=True).encode()
    try:
        os.write(descriptor,raw);os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        _remove_exact(path,raw)
