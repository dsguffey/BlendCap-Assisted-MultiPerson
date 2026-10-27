"""Deterministic Blender vector tool icon: camera <-> Character; no raster/deps."""
import math
from pathlib import Path


def icon_bytes():
    triangles=[]
    def polygon(points,color):
        for i in range(1,len(points)-1):triangles.append((points[0],points[i],points[i+1],color))
    def rect(x0,y0,x1,y1,color):polygon(((x0,y0),(x1,y0),(x1,y1),(x0,y1)),color)
    grey=(220,225,230,255);blue=(80,210,245,255)
    # Camera body, two small reels and lens.
    rect(14,94,64,153,grey)
    rect(21,159,38,177,grey);rect(43,159,60,177,grey)
    polygon(((68,109),(86,98),(86,149),(68,138)),grey)
    # Opposed arrows, with a readable shaft at toolbar scale.
    rect(109,120,171,128,blue)
    polygon(((96,124),(115,140),(115,108)),blue)
    polygon(((184,124),(165,108),(165,140)),blue)
    # Head, torso, arms and legs of the target Character.
    polygon(tuple((215+14*math.cos(i*math.tau/12),172+14*math.sin(i*math.tau/12)) for i in range(12)),grey)
    rect(202,96,228,148,grey);rect(190,101,199,144,grey);rect(231,101,240,144,grey)
    rect(202,61,212,93,grey);rect(218,61,228,93,grey)
    coords=bytes(round(v) for a,b,c,color in triangles for point in (a,b,c) for v in point)
    colors=bytes(v for a,b,c,color in triangles for _ in range(3) for v in color)
    return b'VCO\0'+bytes((255,255,0,0))+coords+colors


if __name__=='__main__':
    path=Path(__file__).resolve().parents[1]/'blendcap/icons/vta_depth.dat'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(icon_bytes());print(path,len(icon_bytes()))
