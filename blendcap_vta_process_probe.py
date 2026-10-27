"""Isolated read-only experiment; NOT imported or routed by the production addon.

SPDX-License-Identifier: GPL-3.0-or-later
Only JSON crosses the boundary. Never discover/import the private package here.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.abc
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

SCHEMA = 'vta-source-coverage-probe'
MAX_LINE = 8192


class ProbeError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def assert_host_isolation():
    if any(name == 'vta_core' or name.startswith('vta_core.') for name in sys.modules):
        raise ProbeError('HOST_NOT_ISOLATED', 'Start a fresh host without private VTA modules.')
    if any(isinstance(path, str) and (Path(path)/'vta_core/__init__.py').is_file()
           for path in sys.path):
        raise ProbeError('HOST_NOT_ISOLATED', 'Private package search paths are forbidden in proof host.')


class _PrivateImportGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'vta_core' or fullname.startswith('vta_core.'):
            raise ProbeError('PRIVATE_IMPORT_BLOCKED', 'Private VTA import forbidden in proof host.')


@contextmanager
def private_import_guard():
    assert_host_isolation()
    guard = _PrivateImportGuard()
    sys.meta_path.insert(0, guard)
    try:
        yield
        assert_host_isolation()
    finally:
        sys.meta_path.remove(guard)


def _json(line):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite JSON value')
    try:
        value = json.loads(line, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(value, dict):
            raise ValueError('JSON object required')
        return value
    except (ValueError, UnicodeError) as exc:
        raise ProbeError('INVALID_RESPONSE', str(exc)) from exc


@dataclass(frozen=True)
class ProbeResult:
    source_range: tuple[int, int]
    engine_pid: int
    startup_s: float       # Parent spawn -> engine ready, includes private imports.
    operation_s: float     # Request send -> reply; excludes startup/child exit.
    total_s: float         # Spawn -> child exit.
    engine_compute_s: float


class SourceCoverageProcessClient:
    def __init__(self, python, launcher, *, timeout=15):
        self.python, self.launcher = Path(python), Path(launcher)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('finite positive timeout required')
        self.timeout = timeout
        self.last_result = None

    def source_coverage(self, location):
        self.last_result = None
        with private_import_guard():
            result = self._query(location)
        self.last_result = result
        return result.source_range

    def _query(self, location):
        if not self.python.is_file() or not self.launcher.is_file():
            raise ProbeError('ENGINE_UNAVAILABLE', 'Explicit Python/launcher path is unavailable.')
        request_id = uuid4().hex
        body = json.dumps(dict(schema=SCHEMA, version=1, request_id=request_id,
            operation='source-coverage', companion_root=str(location.companion_root),
            source_path=str(location.source_path), source_frame=location.source_frame),
            allow_nan=False).encode('utf-8') + b'\n'
        if len(body) > 4096:
            raise ProbeError('INVALID_REQUEST', 'Request exceeds proof size limit.')
        started = time.perf_counter()
        deadline = started + self.timeout
        try:
            process = subprocess.Popen([str(self.python), '-I', '-B', str(self.launcher)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as exc:
            raise ProbeError('ENGINE_UNAVAILABLE', str(exc)) from exc
        lines, stopped = queue.Queue(maxsize=3), threading.Event()
        diagnostic = bytearray()
        def stdout():
            try:
                while not stopped.is_set():
                    line = process.stdout.readline(MAX_LINE + 1)
                    while not stopped.is_set():
                        try:
                            lines.put(line, timeout=.05)
                            break
                        except queue.Full:
                            continue
                    if not line or len(line) > MAX_LINE:
                        return
            except (OSError, ValueError):
                pass
        def stderr():
            try:
                while chunk := process.stderr.read(256):
                    diagnostic.extend(chunk[:max(0, 4096-len(diagnostic))])
            except (OSError, ValueError):
                pass
        readers = [threading.Thread(target=fn, daemon=True) for fn in (stdout, stderr)]
        for reader in readers:
            reader.start()
        def next_line():
            try:
                line = lines.get(timeout=max(0, deadline-time.perf_counter()))
            except queue.Empty as exc:
                raise ProbeError('TIMEOUT', 'Proof engine timed out; no in-process fallback.') from exc
            if len(line) > MAX_LINE:
                raise ProbeError('INVALID_RESPONSE', 'Engine output exceeded line limit.')
            return line
        def envelope(value, kind):
            if (value.get('schema') != SCHEMA or type(value.get('version')) is not int
                    or value['version'] != 1 or value.get('kind') != kind
                    or type(value.get('pid')) is not int or value['pid'] != process.pid):
                raise ProbeError('INVALID_RESPONSE', 'Invalid version/kind/process identity.')
        try:
            line = next_line()
            if not line:
                raise ProbeError('CHILD_FAILED', 'Engine exited before readiness.')
            ready = _json(line)
            envelope(ready, 'ready')
            if ready.get('ok') is not True:
                raise ProbeError('ENGINE_UNAVAILABLE', 'Engine could not initialize.')
            if ready.get('host_modules_loaded') != []:
                raise ProbeError('INVALID_RESPONSE', 'Engine reports host modules loaded.')
            ready_at = time.perf_counter()
            try:
                process.stdin.write(body)
                process.stdin.close()
            except OSError as exc:
                raise ProbeError('CHILD_FAILED', 'Engine stopped before request delivery.') from exc
            reply_line = next_line()
            if not reply_line:
                raise ProbeError('CHILD_FAILED', 'Engine exited without a result.')
            reply = _json(reply_line)
            replied_at = time.perf_counter()
            envelope(reply, 'result')
            if reply.get('request_id') != request_id or type(reply.get('ok')) is not bool:
                raise ProbeError('INVALID_RESPONSE', 'Wrong request identity/result status.')
            if next_line():
                raise ProbeError('INVALID_RESPONSE', 'Unexpected extra engine output.')
            try:
                status = process.wait(timeout=max(0, deadline-time.perf_counter()))
            except subprocess.TimeoutExpired as exc:
                raise ProbeError('TIMEOUT', 'Engine did not exit after its result.') from exc
            if not reply['ok']:
                error = reply.get('error')
                if (status != 2 or not isinstance(error, dict)
                        or not isinstance(error.get('code'), str)
                        or not isinstance(error.get('message'), str)):
                    raise ProbeError('INVALID_RESPONSE', 'Malformed engine error.')
                raise ProbeError(error['code'], error['message'])
            if status:
                raise ProbeError('CHILD_FAILED', 'Engine failed after reporting success.')
            bounds, compute = reply.get('source_range'), reply.get('compute_s')
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(v) is not int for v in bounds) or bounds[0] > bounds[1]
                    or type(compute) not in (int, float) or not math.isfinite(compute) or compute < 0):
                raise ProbeError('INVALID_RESPONSE', 'Invalid bounds/timing.')
            return ProbeResult(tuple(bounds), process.pid, ready_at-started,
                               replied_at-ready_at, time.perf_counter()-started, compute)
        finally:
            stopped.set()
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
            for reader in readers:
                reader.join(timeout=.5)
