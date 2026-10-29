"""Explicit setup-owned roots; no private imports or developer-path fallback."""
import json,os
from pathlib import Path
if __package__:
    from .vta_protocol.runtime import resolve,RuntimeConfigurationError
else:
    from vta_protocol.runtime import resolve,RuntimeConfigurationError

def configuration_path(explicit=None,*,environ=None):
    env=os.environ if environ is None else environ
    value=explicit or env.get('VTA_RUNTIME_CONFIG')
    if value:return Path(value)
    if env.get('LOCALAPPDATA'):return Path(env['LOCALAPPDATA'])/'VTA/config/runtime.json'
    raise RuntimeConfigurationError('RUNTIME_MISSING','VTA configuration is missing. Run setup/repair.')

def load_runtime(explicit=None,*,environ=None):
    path=configuration_path(explicit,environ=environ)
    try:
        if path.stat().st_size>32768:raise ValueError('oversized configuration')
        value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise RuntimeConfigurationError('RUNTIME_MISSING','VTA runtime configuration is missing or invalid. Run setup/repair.') from exc
    return resolve(value)

def default_roots(environ=None):return (load_runtime(environ=environ)['engine_root'],)
