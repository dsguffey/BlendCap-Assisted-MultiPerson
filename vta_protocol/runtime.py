"""Setup-owned runtime layout contract. SPDX-License-Identifier: CC0-1.0"""
from pathlib import Path, PurePosixPath
import os

PRODUCT_VERSION='1.0.0-dev.m5'
LAYOUT_VERSION=1
PROTOCOL_MIN=7
PROTOCOL_MAX=7
ROOTS=('adapter','engine','control','body','analysis','analysis_source','models')
ROUTES={'control_python':'control','body_python':'body','analysis_python':'analysis',
        'body_root':'body','kinesia_root':'analysis_source','sam3_checkpoint':'models'}
BODY_MODELS=('checkpoints/sam-3d-body-dinov3/model.ckpt',
    'checkpoints/sam-3d-body-dinov3/model_config.yaml',
    'checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt',
    'checkpoints/moge-2-vits-normal/model.pt')

def user_data_root(environ=None):
    env=os.environ if environ is None else environ
    return Path(env['LOCALAPPDATA'])/'VTA' if env.get('LOCALAPPDATA') else Path.home()/'.local/share/VTA'

class RuntimeConfigurationError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def require(ok,message,code='RUNTIME_INVALID'):
    if not ok:raise RuntimeConfigurationError(code,message)

def contained(root,name):
    require(type(name) is str and 0<len(name)<=2048 and '\\' not in name and ':' not in name
        and not name.startswith('/') and not any(c in name for c in '\x00\r\n'),'Invalid package-relative path.')
    require(name=='.' or all(p not in ('','.','..') for p in name.split('/')),'Package path traversal rejected.')
    path=(root/PurePosixPath(name)).resolve()
    require(path.is_relative_to(root.resolve()),'Package path escapes its trusted root.')
    return path

def resolve(value, *, check_files=True):
    require(type(value) is dict and set(value)=={'schema','version','product_version','protocol','mode','roots','routes'},'Invalid runtime manifest fields.')
    require(value['schema']=='vta-runtime' and type(value['version']) is int and value['version']==LAYOUT_VERSION,'Unsupported runtime layout. Run matching setup/repair.','VERSION_MISMATCH')
    require(value['product_version']==PRODUCT_VERSION and value['protocol']==[PROTOCOL_MIN,PROTOCOL_MAX],
        'VTA adapter/engine/runtime versions differ. Install the matching package.','VERSION_MISMATCH')
    require(value['mode'] in ('source','installed','test'),'Unknown execution mode.')
    fast=type(value['roots']) is dict and 'review_control' in value['roots']
    require(type(value['roots']) is dict and set(value['roots'])==set(ROOTS)|({'review_control'} if fast else set()),'Missing explicit component root.')
    roots={}
    for key,name in value['roots'].items():
        require(type(name) is str and len(name)<=4096 and not name.startswith(('//','\\\\')) and '..' not in Path(name).parts,'Invalid trusted root.')
        path=Path(name)
        require(path.is_absolute() and (not path.drive or ':' not in name[len(path.drive):]),'Trusted root must be an absolute local path.')
        roots[key]=path.resolve()
        if check_files:require(roots[key].is_dir(),'VTA component root is missing: '+key+'. Run setup/repair.','RUNTIME_MISSING')
    routes={**ROUTES,**({'review_python':'review_control'} if fast else {})}
    require(type(value['routes']) is dict and set(value['routes'])==set(routes),'Invalid runtime routes.')
    result={key:contained(roots[owner],value['routes'][key]) for key,owner in routes.items()}
    result.update(public_root=roots['adapter'],engine_root=roots['engine'])
    if check_files:
        for key in ('control_python','body_python','analysis_python','sam3_checkpoint',*(('review_python',) if fast else ())):
            require(result[key].is_file(),'VTA runtime/model file is missing: '+key+'. Run repair.','MODEL_MISSING' if key=='sam3_checkpoint' else 'RUNTIME_MISSING')
        for key in ('control_python','body_python','analysis_python',*(('review_python',) if fast else ())):
            require(result[key].name.lower() in ('python.exe','python','python3'),'Only the setup-owned Python runtime is supported.')
        for key,name in (('public_root','blendcap_vta_provider_worker.py'),('engine_root','vta_engine.py'),('kinesia_root','src/sam_3d_pose_estimation/stream_detector.py'),('body_root','save_mhr_data.py')):
            require(contained(result[key],name).is_file(),'VTA program component is incomplete: '+key+'. Run repair.','RUNTIME_MISSING')
        for name in BODY_MODELS:
            require(contained(result['body_root'],name).is_file(),'VTA body/calibration model is missing: '+name+'. Run repair.','MODEL_MISSING')
    return result

def provider_runtime(resolved):
    return {key:str(resolved[key]) for key in ('public_root','body_root','body_python','analysis_python','kinesia_root','sam3_checkpoint')}
