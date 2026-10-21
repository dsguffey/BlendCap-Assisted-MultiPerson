"""Public target knowledge only. Never imports private VTA or Blender APIs.

Control-family signatures are diagnostic, NOT a claim of bake support. The
strict FK adapter must not fall back from an identified control rig to an export
skeleton just because that same object happens to contain deform bone names.
"""
from dataclasses import dataclass
from .vta_fingers import PARENTS as FINGER_PARENTS


PARENTS={'hips':None,'spine_lower':'hips','spine_middle':'spine_lower','chest':'spine_middle',
         'neck':'chest','head':'neck'}
for side in ('left','right'):
    for chain,parent in ((('clavicle','upper_arm','forearm','hand'),'chest'),
                         (('thigh','shin','foot','toe'),'hips')):
        for semantic in chain:
            PARENTS[side+'_'+semantic]=parent;parent=side+'_'+semantic
PARENTS.update(FINGER_PARENTS)


@dataclass(frozen=True)
class TargetProfile:
    id: str
    label: str
    bones: dict
    signatures: tuple
    root: str | None = None
    layer: str = 'ordinary FK/deform skeleton'
    available: bool = True
    limitation: str = 'Body FK and optional captured fingers; no new hand solver, IK, contact locking or twist solve.'
    hand_references: tuple = ()
    marker_keys: tuple = ()
    excluded_bones: tuple = ()
    variant_refusal: str = ''
    observations: dict | None = None

    @property
    def required(self):return tuple(s for s in self.bones if not s.endswith('_toe'))

    @property
    def optional(self):return tuple(s for s in self.bones if s.endswith('_toe'))


mixamo={'hips':'Hips','spine_lower':'Spine','spine_middle':'Spine1','chest':'Spine2','neck':'Neck','head':'Head'}
epic={'hips':'pelvis','spine_lower':'spine_01','spine_middle':'spine_02','chest':'spine_03','neck':'neck_01','head':'head'}
for side in ('left','right'):
    suffix='l' if side=='left' else 'r'
    for semantic,bone in (('clavicle','Shoulder'),('upper_arm','Arm'),('forearm','ForeArm'),('hand','Hand'),
                          ('thigh','UpLeg'),('shin','Leg'),('foot','Foot'),('toe','ToeBase')):
        mixamo[side+'_'+semantic]=side.title()+bone
    for semantic,bone in (('clavicle','clavicle'),('upper_arm','upperarm'),('forearm','lowerarm'),('hand','hand'),
                          ('thigh','thigh'),('shin','calf'),('foot','foot'),('toe','ball')):
        epic[side+'_'+semantic]=bone+'_'+suffix

MIXAMO=TargetProfile('MIXAMO','Mixamo FK',mixamo,('Hips',),
    hand_references=(('LeftHandMiddle1','LeftHandIndex1'),('RightHandMiddle1','RightHandIndex1')))
EPIC_UE4=TargetProfile('EPIC_UE4','Unreal/Epic UE4 FK',epic,('pelvis',),root='root',
    limitation='UE4 three-spine/one-neck naming only. UE5 Manny/Quinn, Control Rig, twist and IK are not supported.',
    hand_references=(('middle_01_l','index_01_l'),('middle_01_r','index_01_r')),
    excluded_bones=('spine_04','spine_05','neck_02'),
    variant_refusal='UE5 Manny/Quinn spine/neck variants are not supported by the UE4 profile.')

rigify={'hips':'spine_fk','spine_lower':'spine_fk.001','spine_middle':'spine_fk.002',
        'chest':'spine_fk.003','neck':'neck','head':'head'}
rigify_observations={'hips':'DEF-spine','spine_lower':'DEF-spine.001','spine_middle':'DEF-spine.002',
                     'chest':'DEF-spine.003','neck':'DEF-spine.004','head':'DEF-spine.006'}
for side,suffix in (('left','.L'),('right','.R')):
    for semantic,control,deform in (
            ('clavicle','shoulder','shoulder'),('upper_arm','upper_arm_fk','upper_arm'),
            ('forearm','forearm_fk','forearm'),('hand','hand_fk','hand'),
            ('thigh','thigh_fk','thigh'),('shin','shin_fk','shin'),
            ('foot','foot_fk','foot'),('toe','toe_fk','toe')):
        rigify[side+'_'+semantic]=control+suffix;rigify_observations[side+'_'+semantic]='DEF-'+deform+suffix
RIGIFY=TargetProfile('RIGIFY','Rigify',rigify,('torso','upper_arm_fk.L','thigh_fk.L'),
    layer='standard generated body FK controls',marker_keys=('rig_id',),observations=rigify_observations,
    hand_references=(('DEF-f_middle.01.L','DEF-f_index.01.L'),('DEF-f_middle.01.R','DEF-f_index.01.R')),
    limitation='Standard generated human FK and optional captured fingers; no custom feature sets, IK/tweak solving or regeneration.')
# Control adapters below remain unavailable; preserved legacy maps are not VTA support.
ARP=TargetProfile('ARP','Auto-Rig Pro controls',{},('c_root_master.x','c_arm_fk.l','c_thigh_fk.l'),
    layer='FK controls with proprietary-generated mechanism/deform dependencies',available=False,
    limitation='Auto-Rig Pro control adapter and real-rig validation are pending; no proprietary API or asset is bundled.')
CLOUDRIG=TargetProfile('CLOUDRIG','CloudRig controls',{},('TORSO-Spine','FK-UpperArm.L','FK-Thigh.L'),
    layer='FK controls with IK-switch-driven constraints',available=False,marker_keys=('cloudrig',),
    limitation='CloudRig control adapter and real-rig validation are pending; legacy automatic IK conversion is not enabled.')
PROFILES=(MIXAMO,EPIC_UE4,RIGIFY,ARP,CLOUDRIG)
PROFILE_ITEMS=(('AUTO','Automatic','Strict detection; ambiguous targets require explicit selection'),)+tuple(
    (p.id,p.label,('Supported: ' if p.available else 'Detected only: ')+p.limitation) for p in PROFILES)


@dataclass(frozen=True)
class TargetMatch:
    profile: TargetProfile
    prefix: str
    mapping: dict


def _prefixes(names,profile):
    anchor=profile.signatures[0]
    for name in sorted(names):
        if not name.endswith(anchor):continue
        prefix=name[:-len(anchor)]
        if prefix and prefix not in ('mixamorig',) and prefix[-1] not in ':_.-|':continue
        if all(prefix+signature in names for signature in profile.signatures):yield prefix


def resolve_target(names,profile_id='AUTO',*,control_markers=()):
    """Resolve names without guessing namespaces or overriding failed preflight."""
    names=set(names)
    if profile_id!='AUTO' and profile_id not in {p.id for p in PROFILES}:raise ValueError('Unknown target profile: '+profile_id)
    controls=[p for p in PROFILES if not p.available and (p.id in control_markers or any(True for _ in _prefixes(names,p)))]
    if controls:raise ValueError('; '.join(p.limitation for p in controls))
    candidates=[(p,prefix) for p in PROFILES if p.available for prefix in _prefixes(names,p)
                if profile_id in ('AUTO',p.id)]
    if ('RIGIFY' in control_markers or any(True for _ in _prefixes(names,RIGIFY))) and any(
            True for p in (MIXAMO,EPIC_UE4) for _ in _prefixes(names,p)):
        raise ValueError('Ambiguous/customized Rigify target; do not combine target skeletons on one object.')
    if not candidates:raise ValueError('Target profile/namespace is missing or unsupported; use a supported FK skeleton.')
    if len(candidates)>1:raise ValueError('Target profile or namespace is ambiguous; choose an explicit target profile (or separate the namespaces).')
    profile,prefix=candidates[0]
    if not profile.available:raise ValueError(profile.limitation)
    missing=[name for semantic,name in profile.bones.items() if semantic in profile.required and prefix+name not in names]
    if profile.root and prefix+profile.root not in names:missing.append(profile.root)
    if missing:raise ValueError('Missing required '+profile.label+' bones: '+', '.join(missing))
    if any(prefix+n in names for n in profile.excluded_bones):raise ValueError(profile.variant_refusal)
    return TargetMatch(profile,prefix,{s:prefix+n for s,n in profile.bones.items() if prefix+n in names})
