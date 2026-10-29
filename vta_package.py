"""Developer-only package staging/verification proof; not a user installer.

SPDX-License-Identifier: GPL-3.0-or-later
No downloading, pip, archive extraction, execution, project cleanup or deployment.
"""
import hashlib,json,os,shutil
from pathlib import Path
from uuid import uuid4
try:
    from .vta_protocol.runtime import contained,PRODUCT_VERSION,require
except ImportError:
    from vta_protocol.runtime import contained,PRODUCT_VERSION,require

MAX_FILES=20000
def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def manifest(value):
    require(type(value) is dict and set(value)=={'schema','version','product_version','files'},'Invalid package manifest.')
    require(value['schema']=='vta-package' and type(value['version']) is int and value['version']==1 and value['product_version']==PRODUCT_VERSION,'Incompatible package manifest.','VERSION_MISMATCH')
    require(type(value['files']) is list and 0<len(value['files'])<=MAX_FILES,'Invalid package file count.')
    seen=set()
    for row in value['files']:
        require(type(row) is dict and set(row)=={'path','component','bytes','sha256','source','license','notices','redistribution'},'Invalid provenance entry.')
        name=row['path'];contained(Path.cwd(),name)
        require(name!='.' and name.casefold() not in seen and name.split('/')[0] in ('adapter','engine','runtime','models','licenses'),'Invalid/duplicate payload path.')
        require(not any(p in ('.git','__pycache__','checkpoints','research','logs','sources') or p.endswith('_vta') for p in name.split('/')) and not name.lower().endswith(('.mp4','.tmp','.log','.pyc')),'Developer/project data is not package payload.')
        require(not name.lower().endswith('.blend') or name in ('adapter/vta_workspace.blend','adapter/vta_workspace_mode.blend'),'Only audited program workspace assets may be packaged as .blend files.')
        seen.add(name.casefold())
        require(type(row['bytes']) is int and row['bytes']>=0 and type(row['sha256']) is str and len(row['sha256'])==64 and all(c in '0123456789abcdef' for c in row['sha256']),'Invalid file identity.')
        require(row['redistribution']=='approved','Payload redistribution is not approved; retain as acquisition/provenance metadata only.','PROVENANCE_REQUIRED')
        for key in ('component','source','license','notices'):require(type(row[key]) is str and 0<len(row[key])<=4096,'Missing provenance/notices.')
    return value

def regular(root,name):
    path=contained(root,name)
    current=root
    for part in Path(name).parts:
        current=current/part
        require(not current.is_symlink() and not getattr(current,'is_junction',lambda:False)(),'Linked package member rejected.')
    require(path.is_file() and path.stat().st_nlink==1,'Package member is missing/linked.')
    return path

def verify(root,value):
    manifest(value);root=Path(root).resolve();issues=[]
    for row in value['files']:
        try:
            path=regular(root,row['path'])
            if path.stat().st_size!=row['bytes'] or file_hash(path)!=row['sha256']:issues.append(dict(path=row['path'],reason='corrupt'))
        except (OSError,ValueError):issues.append(dict(path=row['path'],reason='missing-or-unsafe'))
    return issues

def stage(source,destination,value):
    """Build only an audited explicit allowlist into a new deterministic tree."""
    manifest(value);source=Path(source).resolve();destination=Path(destination).resolve()
    require(not destination.exists() and not destination.is_relative_to(source),'Use a new separate staging directory.')
    require(not verify(source,value),'Payload input failed integrity verification.')
    destination.mkdir(parents=True)
    for row in sorted(value['files'],key=lambda r:r['path']):
        path=contained(destination,row['path']);path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(regular(source,row['path']),path)
    (destination/'package.json').write_text(json.dumps(value,sort_keys=True,separators=(',',':')),encoding='utf-8')
    require(not verify(destination,value),'Staging verification failed.')
    return destination

def repair(root,master,value):
    """Restore only manifest-owned program files, with verified atomic replacement.

    Caller must first quiesce VTA. Not a multi-file updater; versioned staging and
    a single activation pointer are the update model. Projects are never scanned.
    """
    manifest(value);root=Path(root).resolve();master=Path(master).resolve()
    require(root!=master and not root.is_relative_to(master) and not master.is_relative_to(root),'Repair master must be separate.')
    require(not verify(master,value),'Repair master failed verification.')
    rows={r['path']:r for r in value['files']};changed=[]
    for issue in verify(root,value):
        name=issue['path'];path=contained(root,name)
        require(not path.is_symlink(),'Repair cannot replace links.')
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        try:
            shutil.copyfile(regular(master,name),temporary)
            require(file_hash(temporary)==rows[name]['sha256'],'Repair copy changed.')
            os.replace(temporary,path);changed.append(name)
        finally:temporary.unlink(missing_ok=True)
    return changed

def activation_record(release,value):
    """Pure proposed pointer, suitable for atomic publication after setup checks."""
    require(type(release) is str and release and all(c.isalnum() or c in '.-_' for c in release),'Invalid release ID.')
    manifest(value)
    return dict(schema='vta-active-release',version=1,release=release,product_version=PRODUCT_VERSION,
        manifest_sha256=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def uninstall_plan(root,value):
    """Exact owned-file allowlist; never recursive-delete unknown/user files."""
    manifest(value);root=Path(root).resolve()
    return [contained(root,r['path']) for r in value['files']]+[root/'package.json']
