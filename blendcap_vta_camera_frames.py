"""GPL source decoding for camera cleanup; no private imports or solver logic.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json,os,sys,time
from pathlib import Path

def run(folder,video,first,last):
    import cv2,numpy as np
    from vta_protocol.process import watch_parent
    watch_parent(int(os.environ.pop('VTA_PROVIDER_PARENT')))
    folder=Path(folder).resolve(strict=True)
    if folder.name!='frames' or not folder.parent.name.startswith('rotation-'):raise ValueError('Invalid camera frame directory.')
    cv2.setNumThreads(1);capture=cv2.VideoCapture(video)
    try:
        if not capture.isOpened():raise ValueError('Source video unavailable.')
        for number in range(last+1):
            if (folder.parent/'cancel').exists():return
            ok,frame=capture.read()
            if not ok:raise ValueError('Cannot decode source frame '+str(number))
            if number<first:continue
            deadline=time.monotonic()+180
            while len(list(folder.glob('*.npy')))>=2:
                if (folder.parent/'cancel').exists():return
                if time.monotonic()>deadline:raise TimeoutError('Camera frame consumer stopped.')
                time.sleep(.02)
            path=folder/(str(number)+'.npy');temporary=folder/(str(number)+'.tmp')
            with temporary.open('xb') as stream:np.save(stream,cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),allow_pickle=False)
            os.replace(temporary,path)
    finally:capture.release()

if __name__=='__main__':
    sys.dont_write_bytecode=True;sys.path.insert(0,str(Path(__file__).resolve().parent))
    run(sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]))
