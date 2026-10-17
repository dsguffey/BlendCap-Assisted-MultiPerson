"""File transport for independent native Save As; no review/state policy writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

if __package__:
    from .blendcap_vta_generation_ipc import filesystem_path, preflight_generation_paths
else:
    from blendcap_vta_generation_ipc import filesystem_path, preflight_generation_paths


def file_hash(path):
    with open(filesystem_path(path),'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def read_json(path):
    with open(filesystem_path(path),encoding='utf-8') as stream:
        return json.load(stream)


def exists(path):
    return os.path.lexists(filesystem_path(path))


def tree_hashes(root):
    """Snapshot a real owned directory, never following reparse points."""
    root=Path(root)
    result={}
    for directory,folders,names in os.walk(root,followlinks=False):
        for name in folders+names:
            path=Path(directory)/name
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
                raise ValueError('Linked VTA destination contents cannot be replaced safely.')
        for name in names:
            path=Path(directory)/name
            result[str(path.relative_to(root))]=file_hash(path)
    return result


def replacement_receipt(blend):
    """Positive ownership: exact saved .blend + canonical path + project identity.

    Old v1 manifests cannot prove which .blend belongs to them. Refuse those,
    rather than infer ownership from a basename or matching project ID alone.
    """
    blend=Path(blend).resolve();root=blend.with_name(blend.stem+'_vta')
    if root.is_symlink() or getattr(root,'is_junction',lambda:False)():
        raise ValueError('Linked VTA companion cannot be replaced safely.')
    manifest=read_json(root/'copy_manifest.json')
    if (manifest.get('version')!=2 or manifest.get('destination_blend')!=str(blend)
            or not blend.is_file() or manifest.get('blend_sha256')!=file_hash(blend)
            or not isinstance(manifest.get('project_id'),str) or not manifest['project_id']
            or not isinstance(manifest.get('transaction_id'),str) or not manifest['transaction_id']
            or manifest.get('project_id')!=read_json(root/'project_state.json').get('project_id')):
        raise ValueError('Destination VTA ownership cannot be verified. Choose a new name; nothing was merged or overwritten.')
    return manifest


def seal_receipt(blend,expected):
    blend=Path(blend).resolve();root=blend.with_name(blend.stem+'_vta')
    path=root/'copy_manifest.json'
    if read_json(path)!=expected:raise ValueError('VTA copy receipt changed during native Save.')
    updated={**expected,'blend_sha256':file_hash(blend)}
    temporary=root/('.receipt-'+uuid4().hex+'.tmp')
    try:
        with open(filesystem_path(temporary),'x',encoding='utf-8') as stream:
            json.dump(updated,stream,indent=2,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(filesystem_path(temporary),filesystem_path(path))
    finally:
        if exists(temporary):os.unlink(filesystem_path(temporary))


class CopyPlan:
    """Exact bytes for authority/evidence; referenced output closure, not a tree copy."""
    def __init__(self,source,destination):
        self.source,self.destination=Path(source).resolve(),Path(destination).resolve()
        if (self.source==self.destination or self.destination.is_relative_to(self.source)
                or self.source.is_relative_to(self.destination)):
            raise ValueError('VTA Save As companion paths overlap.')
        self.files={}
        self.outputs={}
        self.media={}
        self.token=uuid4().hex
        self.replaced=None
        self.prior_hashes=None
        self.manifest=None
        self.destination_blend=None

    def authorize_replacement(self,blend):
        """Called only by native Save As, which owns .blend overwrite consent."""
        blend=Path(blend).resolve()
        if blend.with_name(blend.stem+'_vta')!=self.destination:
            raise ValueError('Destination .blend and companion do not correspond.')
        self.destination_blend=blend
        if exists(self.destination):
            replacement_receipt(blend)
            self.prior_hashes=tree_hashes(self.destination)

    def finish(self):
        """Commit only after native save succeeded; retain changed backups."""
        seal_receipt(self.destination_blend,self.manifest)
        if self.replaced is not None:
            if tree_hashes(self.replaced)!=self.prior_hashes:
                raise ValueError('Prior companion changed; recovery backup retained at '+str(self.replaced))
            shutil.rmtree(filesystem_path(self.replaced))
            self.replaced=None

    def add(self,source,relative,expected=None):
        source=Path(source).resolve()
        relative=Path(relative)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('Unsafe VTA copy dependency path.')
        value=file_hash(source)
        if expected and value!=expected:
            raise ValueError('Referenced VTA file changed: '+str(source))
        prior=self.files.get(relative)
        if prior and prior[1]!=value:
            raise ValueError('Conflicting VTA copy dependencies: '+str(relative))
        self.files[relative]=(source,value)
        return self.destination/relative

    def project(self):
        original_hash=file_hash(self.source/'project_state.json')
        raw=read_json(self.source/'project_state.json')
        self.add(self.source/'project_state.json','project_state.json',original_hash)
        # Candidate observations cannot safely be regenerated: human keys and
        # derived ownership reference their exact IDs. Keep referenced revisions.
        required=set(raw.get('active_analysis_revision_by_source',{}).values())
        def revisions(value):
            if isinstance(value,dict):
                if isinstance(value.get('analysis_revision_id'),str):required.add(value['analysis_revision_id'])
                for child in value.values():revisions(child)
            elif isinstance(value,list):
                for child in value:revisions(child)
        revisions(raw)
        declared={r['revision_id'] for r in raw.get('analysis_revisions',[])}
        for revision in required|declared:
            leaf=Path(revision+'.json')
            if leaf.name!=revision+'.json' or len(leaf.parts)!=1:
                raise ValueError('Unsafe analysis revision path.')
            path=self.source/'analysis'/leaf
            if revision in required or exists(path):self.add(path,Path('analysis')/leaf)
        if any((self.source/'analysis').glob('evidence-*.json')):
            raise ValueError('Deferred Recovery evidence is unsupported; no VTA copy was made.')
        return raw

    def output(self,path,expected=None):
        original=Path(path).resolve()
        if original in self.outputs:
            if expected and file_hash(original)!=expected:raise ValueError('Scene-reference output changed.')
            return self.outputs[original]
        source=original
        # Older QA copies can retain an absolute reference into an ancestor
        # companion. Prefer an identical local immutable output when present.
        if not source.is_relative_to(self.source):
            parts=source.parts
            if 'reconstruction' in parts:
                relative=Path(*parts[parts.index('reconstruction'):])
                local=self.source/relative
                if exists(local) and file_hash(local)==file_hash(source):source=local
        relative=(source.relative_to(self.source) if source.is_relative_to(self.source) else
                  Path('reconstruction')/('ref-'+file_hash(source)[:16])/'output.json')
        result=self.add(source,relative,expected)
        report=read_json(source)
        if report.get('schema')!='vta-reviewed-skeletal-output':
            raise ValueError('Unsupported referenced VTA reconstruction output.')
        for record in report['characters']:
            for field in ('bvh','npz'):
                if record.get(field):
                    leaf=Path(record[field])
                    if leaf.is_absolute() or '..' in leaf.parts:raise ValueError('Unsafe reconstruction dependency.')
                    self.add(source.parent/leaf,relative.parent/leaf,record.get(field+'_sha256'))
        calibration=(report.get('reconstruction') or {}).get('calibration') or {}
        key=calibration.get('key')
        if key and len(key)==64 and all(c in '0123456789abcdef' for c in key):
            leaf=Path('reconstruction')/'calibration'/(key+'.json')
            if exists(self.source/leaf):self.add(self.source/leaf,leaf)
        self.outputs[original]=result
        return result

    def video(self,path,expected):
        path=Path(path).resolve()
        if path not in self.media:
            if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
                raise ValueError('Invalid VTA source hash.')
            suffix=path.suffix.lower()
            if len(suffix)>12:raise ValueError('Unsupported source media extension.')
            self.media[path]=self.add(path,Path('sources')/(expected+suffix),expected)
        return self.media[path]

    def publish(self):
        """One owned stage, hash verification, no hard-linked mutable state."""
        if exists(self.destination) and self.prior_hashes is None:
            raise FileExistsError('Destination VTA companion already exists; choose a new name. Nothing was merged or overwritten.')
        preflight_generation_paths(self.destination)
        for relative in self.files:
            if os.name=='nt' and len(str(self.destination/relative))>=260:
                raise ValueError('Destination VTA file path is too long for current Windows readers; choose a shorter folder/name.')
        self.destination.parent.mkdir(parents=True,exist_ok=True)
        stage=self.destination.parent/('.vta-copy-'+uuid4().hex[:16])
        os.mkdir(filesystem_path(stage))
        try:
            for relative,(source,value) in self.files.items():
                target=stage/relative
                os.makedirs(filesystem_path(target.parent),exist_ok=True)
                shutil.copyfile(filesystem_path(source),filesystem_path(target))
                if file_hash(target)!=value:raise ValueError('VTA copy verification failed: '+str(relative))
            for source,value in self.files.values():
                if file_hash(source)!=value:raise ValueError('VTA project changed during Save As; retry while idle.')
            manifest=dict(version=2 if self.destination_blend else 1,transaction_id=self.token,
                source_companion=str(self.source),files={str(k):v[1] for k,v in self.files.items()})
            if self.destination_blend:
                manifest.update(destination_blend=str(self.destination_blend),blend_sha256=None,
                    project_id=read_json(stage/'project_state.json')['project_id'])
            with open(filesystem_path(stage/'copy_manifest.json'),'x',encoding='utf-8') as stream:
                json.dump(manifest,stream,indent=2,sort_keys=True)
                stream.flush();os.fsync(stream.fileno())
            if self.prior_hashes is not None:
                if tree_hashes(self.destination)!=self.prior_hashes:
                    raise ValueError('Destination changed during copying; retry while idle.')
                replacement_receipt(self.destination_blend)
                self.replaced=self.destination.parent/('.vta-prior-'+self.token)
                os.rename(filesystem_path(self.destination),filesystem_path(self.replaced))
            elif exists(self.destination):raise FileExistsError('Destination VTA companion appeared during copying; it was not overwritten.')
            try:os.rename(filesystem_path(stage),filesystem_path(self.destination))
            except Exception:
                if self.replaced is not None:
                    os.rename(filesystem_path(self.replaced),filesystem_path(self.destination));self.replaced=None
                raise
            self.manifest=manifest
            return manifest
        finally:
            if exists(stage):shutil.rmtree(filesystem_path(stage)) # Exclusively created stage, never a caller path.

    def rollback(self):
        """Remove only our still-byte-identical published copy after native failure."""
        if self.destination.is_symlink() or getattr(self.destination,'is_junction',lambda:False)():return False
        try:manifest=read_json(self.destination/'copy_manifest.json')
        except (OSError,ValueError):return False
        expected={str(k):v[1] for k,v in self.files.items()}
        if (manifest.get('transaction_id')!=self.token or manifest.get('source_companion')!=str(self.source)
                or manifest.get('files')!=expected):return False
        actual={str(Path(root).relative_to(self.destination)/name)
            for root,_,names in os.walk(self.destination) for name in names}
        if actual!=set(expected)|{'copy_manifest.json'}:return False
        if any(file_hash(self.destination/k)!=value for k,value in expected.items()):return False
        shutil.rmtree(filesystem_path(self.destination))
        if self.replaced is not None:
            os.rename(filesystem_path(self.replaced),filesystem_path(self.destination))
            self.replaced=None
        return True
