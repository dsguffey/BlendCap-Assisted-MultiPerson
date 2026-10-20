"""Independent optional MediaPipe CPU process. Plain JSON only; offline models.

No Blender, BlendCap application or private-core imports. No model downloads.
The caller supplies exact existing Character contexts, never a face identity.
"""
import argparse
import hashlib
import importlib.abc
import json
import math
from pathlib import Path
import sys
import time

MODELS = {
    'face_landmarker.task':'64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff',
    'blaze_face_short_range.tflite':'b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f',
}
FORBIDDEN={'bpy','blendcap','vta_core','pipeline','save_face_data'}


class ImportGuard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in FORBIDDEN:raise ImportError('Face provider forbids application imports: '+fullname)


sys.dont_write_bytecode=True


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def inside(face,region):
    return bool(region and region[0]<=(face[0]+face[2])/2<=region[2] and region[1]<=(face[1]+face[3])/2<=region[3])


def associate(faces,context):
    if context['hard_boundary'] or not context['head_roi']:return None,'unavailable','no_trusted_head_context'
    choices=[i for i,face in enumerate(faces) if inside(face['box'],context['head_roi'])]
    if len(choices)>1:return None,'ambiguous','multiple_compatible_faces'
    if not choices:return None,'not_detected','no_compatible_face'
    chosen=choices[0]
    if any(inside(faces[chosen]['box'],roi) for roi in context['other_body_rois']):
        return None,'ambiguous','other_Character_context_overlaps_face'
    return chosen,'observed','unique_face_in_assigned_context'


def run(request,video,models,stop):
    sys.meta_path.insert(0,ImportGuard())
    def offline(event,args):
        if event in {'socket.connect','socket.getaddrinfo','socket.bind'}:raise RuntimeError('Face provider is offline')
    sys.addaudithook(offline)
    for name,expected in MODELS.items():
        path=models/name
        if not path.is_file():raise ValueError('Missing local face model '+name+'. Install the pinned VTA face model assets; no runtime download is performed.')
        if sha(path)!=expected:raise ValueError('Face model hash mismatch: '+name)
    import cv2
    import mediapipe as mp
    import numpy as np
    if mp.__version__!='0.10.32':raise ValueError('Face capture requires the validated MediaPipe 0.10.32 runtime')
    source=request['source']
    if request['contract']!='vta-face-request-1' or sha(video)!=source['video_sha256']:
        raise ValueError('Face source/request identity mismatch')
    fps=source['fps']
    if type(fps) not in (int,float) or not math.isfinite(fps) or fps<=0:raise ValueError('Invalid face source FPS')
    config=dict(delegate='CPU',mode='VIDEO',num_faces=1,min_detection_confidence=.5,
                crop_margin=.35,interpolation=False,association='unique-assigned-context-v1')
    result=dict(contract='vta-face-evidence-1',project_id=request['project_id'],scene_id=request['scene_id'],
        run_id=request['run_id'],context_digest=request['context_digest'],source=source,characters=[],
        provider=dict(id='mediapipe-face',version='1',package_version=mp.__version__,
            model_id='google-face-landmarker-float16-1',model_sha256=MODELS['face_landmarker.task'],
            detector_sha256=MODELS['blaze_face_short_range.tflite'],
            configuration_digest=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest(),config=config))
    vision=mp.tasks.vision;base=mp.tasks.BaseOptions
    detector=vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
        base_options=base(model_asset_path=str(models/'blaze_face_short_range.tflite'),delegate=base.Delegate.CPU),min_detection_confidence=.5))
    try:
        for character in request['characters']:
            cap=cv2.VideoCapture(str(video))
            if not cap.isOpened():raise ValueError('Cannot decode facial source')
            landmarker=None;previous=None;previous_stamp=-1;rows=[];next_frame=0
            try:
                for context in character['frames']:
                    if stop and stop.exists():return dict(status='cancelled',run_id=request['run_id'])
                    frame=context['source_frame'];stamp=round(frame*1000/fps)
                    if type(frame) is not int or frame<next_frame or stamp<=previous_stamp:raise ValueError('Non-monotonic face source frames/timestamps')
                    previous_stamp=stamp
                    while next_frame<frame:
                        if not cap.grab():raise ValueError('Facial source ended early')
                        next_frame+=1
                    ok,bgr=cap.read();next_frame=frame+1
                    if not ok or int(cap.get(cv2.CAP_PROP_POS_FRAMES))!=next_frame:raise ValueError('Facial source decode/frame mismatch')
                    height,width=bgr.shape[:2]
                    if (width,height)!=(source['width'],source['height']):raise ValueError('Facial source dimensions changed')
                    row=dict(source_frame=frame,timestamp_seconds=frame/fps,provenance=context['provenance'],
                        association_context=dict(head_roi=context['head_roi'],other_body_rois=context['other_body_rois']),
                        observation_id=request['run_id']+':'+character['character_id']+':'+str(frame),
                        body_roi=context['body_roi'],face_roi=None,crop_to_source=None,
                        coordinate_convention=dict(origin='top-left',units='source_pixels',box_format='xyxy',mirrored=False),
                        hard_boundary=context['hard_boundary'],state='unavailable',reason='no_trusted_head_context',
                        origin='observed_source_inference',weights=None,neutral=None,unsupported_channels=['tongueOut'],detection_score=None)
                    faces=[]
                    if context['head_roi'] and not context['hard_boundary']:
                        a,b,c,d=context['body_roi'];head=context['head_roi']
                        # Native assigned close-up ROI; full-body cases use only
                        # their head envelope, never a full-frame fallback.
                        if head!=context['body_roi']:d=min(d,b+(d-b)*.45)
                        x0,y0,x1,y1=math.floor(a),math.floor(b),math.ceil(c),math.ceil(d)
                        rgb=cv2.cvtColor(bgr[y0:y1,x0:x1],cv2.COLOR_BGR2RGB)
                        detections=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)))
                        for detection in detections.detections:
                            bb=detection.bounding_box
                            faces.append(dict(box=[max(0,x0+bb.origin_x),max(0,y0+bb.origin_y),
                                min(width,x0+bb.origin_x+bb.width),min(height,y0+bb.origin_y+bb.height)],score=float(detection.categories[0].score)))
                        chosen,state,reason=associate(faces,context)
                        row.update(state=state,reason=reason)
                        if chosen is not None:
                            face=faces[chosen]['box'];a,b,c,d=face;margin=max(c-a,d-b)*.35
                            x0,y0,x1,y1=max(0,math.floor(a-margin)),max(0,math.floor(b-margin)),min(width,math.ceil(c+margin)),min(height,math.ceil(d+margin))
                            if landmarker is None or previous is None or previous!=frame-1:
                                if landmarker:landmarker.close()
                                landmarker=vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                                    base_options=base(model_asset_path=str(models/'face_landmarker.task'),delegate=base.Delegate.CPU),
                                    running_mode=vision.RunningMode.VIDEO,num_faces=1,output_face_blendshapes=True))
                            rgb=cv2.cvtColor(bgr[y0:y1,x0:x1],cv2.COLOR_BGR2RGB)
                            observation=landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)),stamp)
                            if len(observation.face_blendshapes)==1:
                                values={v.category_name:float(v.score) for v in observation.face_blendshapes[0]}
                                row.update(weights={k:v for k,v in values.items() if k!='_neutral'},neutral=values.get('_neutral'),
                                    face_roi=face,crop_to_source=[[1,0,x0],[0,1,y0],[0,0,1]],detection_score=faces[chosen]['score'])
                                previous=frame
                            else:row.update(state='not_detected',reason='expression_face_unavailable');previous=None
                        else:previous=None
                    else:previous=None
                    rows.append(row)
                result['characters'].append(dict(character_id=character['character_id'],frames=rows))
            finally:
                cap.release()
                if landmarker:landmarker.close()
    finally:detector.close()
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--video',type=Path,required=True);parser.add_argument('--models',type=Path,required=True)
    parser.add_argument('--stop',type=Path)
    args=parser.parse_args()
    try:
        request=json.loads(args.request.read_text(encoding='utf-8'))
        value=run(request,args.video,args.models,args.stop)
        code=0
    except Exception as exc:
        value=dict(status='failed',error=type(exc).__name__,details=str(exc));code=1
    args.output.write_text(json.dumps(value,allow_nan=False),encoding='utf-8')
    return code


if __name__=='__main__':sys.exit(main())
