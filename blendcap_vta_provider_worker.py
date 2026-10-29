"""Isolated GPL body runtime; numeric/file protocol, no private VTA imports.

SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse,hashlib,json,os,sys,time,subprocess,importlib.abc
from pathlib import Path

class PrivateGuard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in ('vta_core','vta_engine','bpy','bpy_extras'):
            raise ImportError('Private/Blender modules are forbidden in provider: '+fullname)

def publish(path,value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def within(root,path):
    if not path.resolve().is_relative_to(root) or (path.is_file() and path.stat().st_nlink!=1):raise ValueError('Unsafe provider artifact path.')
    return path

def run(folder,bundle):
    sys.meta_path.insert(0,PrivateGuard());sys.dont_write_bytecode=True
    root=folder.resolve();bundle=bundle.resolve()
    # Trusted live configuration selects the existing offline runtime. Saved job
    # input cannot select an executable, import module or checkpoint.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from vta_protocol import fields,require
    from vta_protocol.jobs import finite_tree
    sys.path.insert(0,str(bundle))
    from save_mhr_data import load_estimator_fast
    import numpy as np,cv2,torch
    cv2.setNumThreads(1)
    from blendcap_vta_body_provider import infer_reviewed_pose,runtime_identity,export_command,digest
    estimator=None;number=0;source=None;video=None;idle_deadline=time.monotonic()+1200
    requests=root/'requests';responses=root/'responses';requests.mkdir(exist_ok=True);responses.mkdir(exist_ok=True)
    def decode(frame):
        capture=cv2.VideoCapture(str(video))
        try:
            if not capture.isOpened():raise ValueError('Source video could not be opened.')
            # Exact sequential decoding, retaining existing source-frame policy.
            for n in range(frame+1):
                ok,image=capture.read()
                if not ok:raise ValueError('Source ended before requested frame.')
            if image.shape[:2]!=(source['height'],source['width']):raise ValueError('Source dimensions changed.')
            return cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        finally:capture.release()
    try:
        while True:
            path=requests/(str(number)+'.json')
            if not path.exists():
                if time.monotonic()>idle_deadline:return
                time.sleep(.02);continue
            idle_deadline=time.monotonic()+1200
            if path.stat().st_size>32*1024*1024:raise ValueError('Provider request exceeds bounds.')
            data=json.loads(path.read_text(encoding='utf-8'));finite_tree(data)
            fields(data,('operation','payload'));operation=data['operation'];payload=data['payload']
            result=None
            try:
                if operation=='describe':
                    fields(payload,('source','source_path','code_hashes','fov_mode'))
                    from vta_protocol.observation import source as validate_source
                    validate_source(payload['source'])
                    source=payload['source'];video=Path(payload['source_path']).resolve(strict=True)
                    with video.open('rb') as stream:require(hashlib.file_digest(stream,'sha256').hexdigest()==source['video_sha256'],'Source hash mismatch.')
                    started=time.perf_counter();estimator=load_estimator_fast();load=time.perf_counter()-started
                    required={'checkpoint_sha256':bundle/'checkpoints/sam-3d-body-dinov3/model.ckpt',
                        'mhr_sha256':bundle/'checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt',
                        'config_sha256':bundle/'checkpoints/sam-3d-body-dinov3/model_config.yaml',
                        'capture_source_sha256':bundle/'save_mhr_data.py','export_source_sha256':bundle/'npz_to_bvh.py'}
                    provenance={key:digest(path) for key,path in required.items()}
                    provenance.update(payload['code_hashes'])
                    # Both GPL adapter files participate in reconstruction identity.
                    provenance['worker_sha256']=hashlib.sha256(b''.join(
                        name.encode()+b'\0'+(Path(__file__).parent/name).read_bytes()
                        for name in ('blendcap_vta_provider_worker.py','blendcap_vta_body_provider.py'))).hexdigest()
                    identity=runtime_identity(bundle,provenance,estimator,torch)
                    intrinsics=None
                    if payload['fov_mode']=='FIRST':
                        if estimator.fov_estimator is None:raise ValueError('FIRST calibration requires the offline MoGe model.')
                        with torch.inference_mode():matrix=estimator.fov_estimator.get_cam_intrinsics(decode(0))
                        if hasattr(matrix,'detach'):matrix=matrix.detach().cpu().numpy()
                        matrix=np.asarray(matrix);intrinsics=(matrix[0] if matrix.shape==(1,3,3) else matrix).tolist()
                    result=dict(provider=identity,intrinsics=intrinsics,load_seconds=load,provenance=provenance)
                elif operation=='infer':
                    fields(payload,('requests','intrinsics'));require(estimator is not None,'Load provider first.')
                    require(type(payload['requests']) is list and len(payload['requests'])<=65536,'Excessive inference batch.')
                    poses=[];success=[];failures=[];previous_frame=None;image=None
                    intrinsics=None if payload['intrinsics'] is None else torch.as_tensor([payload['intrinsics']],dtype=torch.float32)
                    for index,item in enumerate(payload['requests']):
                        fields(item,('character_id','frame','box_xyxy'))
                        require(type(item['frame']) is int and source['start_frame']<=item['frame']<=source['end_frame'],'Invalid source frame.')
                        require(type(item['box_xyxy']) is list and len(item['box_xyxy'])==4,'Invalid reviewed box.')
                        if (root/'cancel').exists():raise InterruptedError('Cancelled; prior output retained.')
                        if item['frame']!=previous_frame:image=decode(item['frame']);previous_frame=item['frame']
                        pose=infer_reviewed_pose(estimator,np,torch,image,dict(box_xyxy=item['box_xyxy']),source['height'],cam_int=intrinsics)
                        if pose is None:failures.append(dict(character_id=item['character_id'],frame=item['frame'],reason='nonfinite-pose'))
                        else:poses.append(pose);success.append(dict(character_id=item['character_id'],frame=item['frame']))
                        publish(root/'progress.json',dict(completed=index+1,total=len(payload['requests'])))
                    name='raw-'+str(number)+'.npz';output=within(root,root/name)
                    with output.open('xb') as stream:
                        np.savez_compressed(stream,**{key:np.asarray([p[key] for p in poses]) for key in ('pred_global_rots','pred_joint_coords','pred_cam_t','focal_length')})
                    result=dict(successes=success,failures=failures,file=name,sha256=digest(output),bytes=output.stat().st_size)
                elif operation=='export':
                    fields(payload,('index',));index=payload['index'];require(type(index) is int and 0<=index<256,'Invalid export target.')
                    pose=within(root,root/('character-'+str(index)+'.npz'));bvh=within(root,root/('character-'+str(index)+'.bvh'))
                    command=export_command(sys.executable,bundle,pose,bvh)
                    subprocess.run(command,cwd=bundle,check=True,shell=False,timeout=300)
                    result=dict(file=bvh.name,sha256=digest(bvh),bytes=bvh.stat().st_size)
                elif operation=='close':
                    fields(payload,());return
                else:raise ValueError('Unknown provider operation.')
                finite_tree(result);publish(responses/(str(number)+'.json'),dict(result=result,error=None))
            except Exception as exc:
                publish(responses/(str(number)+'.json'),dict(result=None,error=dict(kind=type(exc).__name__,message=str(exc)[:900])))
                if isinstance(exc,InterruptedError):return
            number+=1
    finally:
        del estimator
        import gc
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--folder',required=True);parser.add_argument('--body-root',required=True)
    args=parser.parse_args();run(Path(args.folder),Path(args.body_root))
