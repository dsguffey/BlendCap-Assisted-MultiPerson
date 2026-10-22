"""Optional native finger conventions, not new tracking or body-profile support."""
import math

PARENTS={};MAPS={name:{} for name in ('MIXAMO','RIGIFY','EPIC_UE4','EPIC_UE5','ARP','CLOUDRIG','GODOT_HUMANOID')}
for side,suffix in (('left','L'),('right','R')):
    for finger in ('thumb','index','middle','ring','pinky'):
        for segment in range(1,4):
            s=f'{side}_{finger}_{segment}'
            PARENTS[s]=f'{side}_hand' if segment==1 else f'{side}_{finger}_{segment-1}'
            MAPS['MIXAMO'][s]=f'{side.title()}Hand{finger.title()}{segment}'
            MAPS['RIGIFY'][s]=f'{"thumb" if finger=="thumb" else "f_"+finger}.{segment:02d}.{suffix}'
            for family in ('EPIC_UE4','EPIC_UE5'):MAPS[family][s]=f'{finger}_{segment:02d}_{suffix.lower()}'
            MAPS['ARP'][s]=f'c_{finger}{segment}.{suffix.lower()}'
            MAPS['CLOUDRIG'][s]=f'FK-Finger_{finger.title()}{segment}.{suffix}'
            part=(('Metacarpal','Proximal','Distal') if finger=='thumb' else ('Proximal','Intermediate','Distal'))[segment-1]
            MAPS['GODOT_HUMANOID'][s]=side.title()+('Little' if finger=='pinky' else finger.title())+part


def resolve(target,match,available=None):
    """Fail soft, parent-first. Unsafe/customized optional chains are omitted."""
    result={};rigify=match.profile.id=='RIGIFY'
    driven={f.data_path for f in target.animation_data.drivers} if target.animation_data else set()
    for s,base in MAPS.get(match.profile.id,{}).items():
        if available is not None and s not in available:continue
        parent=PARENTS[s]
        if parent in PARENTS and parent not in result:continue
        name=match.prefix+base;bone=target.pose.bones.get(name)
        if bone is None or bone.constraints or bone.rotation_mode not in ('QUATERNION','XYZ','XZY','YXZ','YZX','ZXY','ZYX'):continue
        if any(not math.isfinite(v) or abs(v-1)>1e-5 for v in bone.scale):continue
        if any(path.startswith(bone.path_from_id()+'.') for path in driven):continue
        if rigify:
            stem,segment,side=base.split('.')
            expected=match.prefix+f'MCH-{stem}.{segment}_drv.{side}'
            master=match.prefix+stem+'.01_master.'+side
            if master not in target.pose.bones or match.prefix+'DEF-'+base not in target.pose.bones:continue
        else:
            expected=result.get(parent,match.mapping.get(parent))
            if match.profile.id=='EPIC_UE5' and s.endswith('_1') and '_thumb_' not in s:
                side,finger,_=s.split('_')
                metacarpal=target.pose.bones.get(match.prefix+finger+'_metacarpal_'+('l' if side=='left' else 'r'))
                if (not metacarpal or not metacarpal.parent or metacarpal.parent.name!=expected
                        or metacarpal.constraints or not metacarpal.bone.use_inherit_rotation
                        or metacarpal.bone.inherit_scale!='FULL' or not metacarpal.bone.use_local_location
                        or any(not math.isfinite(v) or abs(v-1)>1e-5 for v in metacarpal.scale)
                        or any(path.startswith(metacarpal.path_from_id()+'.') for path in driven)):continue
                expected=metacarpal.name
            if not bone.bone.use_inherit_rotation or bone.bone.inherit_scale!='FULL' or not bone.bone.use_local_location:continue
        if not bone.parent or bone.parent.name!=expected:continue
        result[s]=name
    return result
