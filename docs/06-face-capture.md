# Facial capture is not part of current VTA

Current VTA supports reviewed body/hand animation, not facial capture/animation.
The inherited Face capture toggle, capture worker and ARKit application UI have
been retired. MediaPipe and its face/pose model downloads are not required for
installation, startup or supported VTA workflows.

Older files/caches are not deleted or migrated. Shared body-export and
existing-data retargeting helpers remain; they do not imply support for new
facial capture. Historical upstream screenshots and license notices are retained
where applicable. This page supersedes the old face-capture instructions.
