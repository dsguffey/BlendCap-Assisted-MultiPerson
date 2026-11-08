"""GPL local engine client. No private imports, provider dependencies or shell.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

try:
    from . import vta_protocol as wire
    from .vta_protocol.review_reply import OPS as REVIEW_OPS
    from .blendcap_vta_runtime import load_runtime
except ImportError:
    import vta_protocol as wire
    from vta_protocol.review_reply import OPS as REVIEW_OPS
    from blendcap_vta_runtime import load_runtime

LOG=logging.getLogger(__name__)


class EngineError(RuntimeError):
    def __init__(self,code,message):self.code=code;super().__init__(message)


@dataclass(frozen=True)
class EngineEndpoint:
    python: Path
    launcher: Path
    review_python: Path | None = None


def resolve_engine(configured_root='', *, python=None):
    """Resolve executable files only; never discover/import a private module."""
    review_python=None
    try:
        if python is None:
            runtime=load_runtime();roots=(runtime['engine_root'],)
            if configured_root and wire.path_form(str(configured_root)).resolve()!=runtime['engine_root']:
                raise ValueError('Core location differs from the trusted runtime configuration.')
            python=runtime['control_python'];review_python=runtime.get('review_python')
        else:
            roots=(wire.path_form(str(configured_root)),) if configured_root else (load_runtime()['engine_root'],)
    except ValueError as exc:raise EngineError(getattr(exc,'code','ENGINE_UNAVAILABLE'),str(exc)) from exc
    root=next((p for p in roots if (p/'vta_engine.py').is_file()),None)
    if root is None:raise EngineError('ENGINE_UNAVAILABLE','MoCap engine launcher is unavailable; check the Core location.')
    if python is None or not Path(python).is_file():raise EngineError('ENGINE_UNAVAILABLE','Standalone Python is unavailable.')
    return EngineEndpoint(Path(python).resolve(),(root/'vta_engine.py').resolve(),review_python)


class EngineClient:
    """One private child per request; one overall bounded startup/query deadline."""
    def __init__(self,endpoint,*,timeout=15):
        self.endpoint=endpoint;self.timeout=wire.timeout_value(timeout)
        self.last_metrics=None;self.last_diagnostics='';self.last_pid=None
        self.last_presentation=None;self.last_python=None

    def call(self,operation,payload,*,request_id=None):
        self.last_metrics=None;self.last_diagnostics='';self.last_pid=None
        self.last_presentation=None;self.last_python=None
        try:body=wire.dumps(wire.request(request_id or uuid4().hex,payload,operation));request=wire.loads(body)
        except wire.ProtocolError as exc:raise EngineError(exc.code,str(exc)) from exc
        return self._exchange(request,body)

    def _exchange(self,request,body):
        started=time.perf_counter();deadline=started+self.timeout
        endpoint=self.endpoint
        simple=(request['operation'] in REVIEW_OPS and (request['operation']!='review-batch' or request['payload']['action'] in ('assign','suppress')))
        python=(endpoint.review_python if endpoint.review_python is not None and (simple or request['operation']=='review-observation') else endpoint.python)
        self.last_python=python
        if not python.is_file() or not endpoint.launcher.is_file():
            raise EngineError('ENGINE_UNAVAILABLE','Engine executable/launcher is missing.')
        try:
            process=subprocess.Popen([str(python),'-I','-B',str(endpoint.launcher)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),shell=False)
        except OSError as exc:raise EngineError('ENGINE_UNAVAILABLE','Engine process could not start.') from exc
        self.last_pid=process.pid
        lines=queue.Queue(maxsize=4);stopped=threading.Event();diagnostic=bytearray()
        def stdout():
            try:
                while not stopped.is_set():
                    line=process.stdout.readline(wire.MAX_BYTES+1)
                    while not stopped.is_set():
                        try:lines.put(line,timeout=.05);break
                        except queue.Full:continue
                    if not line or len(line)>wire.MAX_BYTES:return
            except (OSError,ValueError):pass
        def stderr():
            try:
                while chunk:=process.stderr.read(512):diagnostic.extend(chunk[:max(0,8192-len(diagnostic))])
            except (OSError,ValueError):pass
        readers=[threading.Thread(target=fn,daemon=True) for fn in (stdout,stderr)]
        for thread in readers:thread.start()
        def next_line():
            try:line=lines.get(timeout=max(0,deadline-time.perf_counter()))
            except queue.Empty as exc:raise EngineError('TIMEOUT','Engine deadline exceeded.') from exc
            if len(line)>wire.MAX_BYTES:raise EngineError('INVALID_RESPONSE','Engine message exceeds size limit.')
            return line
        def message():
            line=next_line()
            if not line:raise EngineError('CHILD_FAILED','Engine exited without its expected message.')
            return wire.loads(line)
        failed=True
        try:
            wire.validate_ready(message(),process.pid);ready=time.perf_counter()
            def send():
                try:process.stdin.write(body);process.stdin.close()
                except (OSError,ValueError):pass  # EOF/exit/deadline is handled by the reader.
            writer=threading.Thread(target=send,daemon=True);readers.append(writer);writer.start()
            result=wire.validate_response(message(),request['request_id'],request['operation']);replied=time.perf_counter()
            if next_line():raise EngineError('INVALID_RESPONSE','Unexpected additional engine output.')
            try:status=process.wait(timeout=max(0,deadline-time.perf_counter()))
            except subprocess.TimeoutExpired as exc:raise EngineError('TIMEOUT','Engine did not shut down.') from exc
            expected=2 if result['status']=='error' else 0
            if status!=expected:raise EngineError('CHILD_FAILED','Unexpected engine exit code: '+str(status))
            self.last_metrics=dict(startup_s=ready-started,round_trip_s=replied-ready,total_s=time.perf_counter()-started)
            if result['status']=='error':raise EngineError(result['error']['code'],result['error']['message'])
            if request['operation']=='source-coverage':
                first,last=result['result']['source_range']
                if not first<=request['payload']['source_frame']<=last:
                    raise EngineError('INVALID_RESPONSE','Source range does not contain the queried frame.')
            if request['operation']=='mutation-status' and result['result']['mutation_id']!=request['payload']['mutation_id']:
                raise EngineError('INVALID_RESPONSE','Mutation status identity mismatch.')
            item=result['result'];payload=request['payload'];operation=request['operation']
            if operation in REVIEW_OPS and 'presentation' in payload:
                self.last_presentation=result.get('presentation')
            if operation=='character-state' and item['character_id']!=payload['character_id']:
                raise EngineError('INVALID_RESPONSE','Observed Character identity mismatch.')
            if operation in wire.MUTATIONS:wire.validate_target(operation,payload,item)
            if operation=='tracking-plan':
                if item['checkpoint']['project_id']!=payload['project_id'] or item['checkpoint']['revision']!=payload['expected_revision']:
                    raise EngineError('INVALID_RESPONSE','Plan revision mismatch.')
                if {r['character_id'] for r in item['outcomes']}!=set(payload['character_ids']):
                    raise EngineError('INVALID_RESPONSE','Plan targets mismatch.')
            LOG.debug('VTA engine request=%s operation=%s completed',request['request_id'],request['operation'])
            failed=False
            return result['result']
        except wire.ProtocolError as exc:
            raise EngineError('INVALID_RESPONSE',str(exc)) from exc
        finally:
            stopped.set()
            if process.poll() is None:
                try:process.kill()
                except ProcessLookupError:pass
            process.wait()
            for stream in (process.stdin,process.stdout,process.stderr):
                try:
                    if not stream.closed:stream.close()
                except (OSError,ValueError):pass
            for thread in readers:thread.join(timeout=.5)
            self.last_diagnostics=diagnostic.decode('utf-8',errors='replace')
            LOG.debug('VTA engine request=%s pid=%s exit=%s diagnostics=%r',request['request_id'],process.pid,
                      process.returncode,self.last_diagnostics)
            if failed:
                LOG.warning('VTA engine failed request=%s operation=%s pid=%s exit=%s diagnostics=%r',
                    request['request_id'],request['operation'],process.pid,process.returncode,self.last_diagnostics)


class SourceCoverageClient:
    def __init__(self,client):self.client=client

    def source_coverage(self,location):
        result=self.client.call('source-coverage',dict(companion_root=str(location.companion_root),
            source_path=str(location.source_path),source_frame=location.source_frame))
        return tuple(result['source_range'])
