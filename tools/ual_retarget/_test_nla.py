# _test_nla.py - quick test: bake ONE clip, push action to NLA, export, check curves exist
import bpy, os, sys

GLB = r"C:\Users\book\Desktop\fate's-pair\animations\GUN_ANIMATIONS\UAL1_Standard.glb"
FBX = r"C:\Users\book\Desktop\fate's-pair\meshs\character.fbx"
OUT = r"C:\Users\book\Desktop\fate's-pair\animations\GUN_ANIMATIONS\ual_pistol\_nla_test.fbx"
CLIP = "Pistol_Aim_Neutral"

MAP = {
    "pelvis": "mixamorig:Hips",
    "spine_01": "mixamorig:Spine",
    "spine_02": "mixamorig:Spine1",
    "spine_03": "mixamorig:Spine2",
    "neck_01": "mixamorig:Neck",
    "Head": "mixamorig:Head",
    "clavicle_l": "mixamorig:LeftShoulder",
    "upperarm_l": "mixamorig:LeftArm",
    "lowerarm_l": "mixamorig:LeftForeArm",
    "hand_l": "mixamorig:LeftHand",
    "index_01_l": "mixamorig:LeftHandIndex1",
    "index_02_l": "mixamorig:LeftHandIndex2",
    "index_03_l": "mixamorig:LeftHandIndex3",
    "middle_01_l": "mixamorig:LeftHandMiddle1",
    "middle_02_l": "mixamorig:LeftHandMiddle2",
    "middle_03_l": "mixamorig:LeftHandMiddle3",
    "pinky_01_l": "mixamorig:LeftHandPinky1",
    "pinky_02_l": "mixamorig:LeftHandPinky2",
    "pinky_03_l": "mixamorig:LeftHandPinky3",
    "ring_01_l": "mixamorig:LeftHandRing1",
    "ring_02_l": "mixamorig:LeftHandRing2",
    "ring_03_l": "mixamorig:LeftHandRing3",
    "thumb_01_l": "mixamorig:LeftHandThumb1",
    "thumb_02_l": "mixamorig:LeftHandThumb2",
    "thumb_03_l": "mixamorig:LeftHandThumb3",
    "thigh_l": "mixamorig:LeftUpLeg",
    "calf_l": "mixamorig:LeftLeg",
    "foot_l": "mixamorig:LeftFoot",
    "ball_l": "mixamorig:LeftToeBase",
    "clavicle_r": "mixamorig:RightShoulder",
    "upperarm_r": "mixamorig:RightArm",
    "lowerarm_r": "mixamorig:RightForeArm",
    "hand_r": "mixamorig:RightHand",
    "index_01_r": "mixamorig:RightHandIndex1",
    "index_02_r": "mixamorig:RightHandIndex2",
    "index_03_r": "mixamorig:RightHandIndex3",
    "middle_01_r": "mixamorig:RightHandMiddle1",
    "middle_02_r": "mixamorig:RightHandMiddle2",
    "middle_03_r": "mixamorig:RightHandMiddle3",
    "pinky_01_r": "mixamorig:RightHandPinky1",
    "pinky_02_r": "mixamorig:RightHandPinky2",
    "pinky_03_r": "mixamorig:RightHandPinky3",
    "ring_01_r": "mixamorig:RightHandRing1",
    "ring_02_r": "mixamorig:RightHandRing2",
    "ring_03_r": "mixamorig:RightHandRing3",
    "thumb_01_r": "mixamorig:RightHandThumb1",
    "thumb_02_r": "mixamorig:RightHandThumb2",
    "thumb_03_r": "mixamorig:RightHandThumb3",
    "thigh_r": "mixamorig:RightUpLeg",
    "calf_r": "mixamorig:RightLeg",
    "foot_r": "mixamorig:RightFoot",
    "ball_r": "mixamorig:RightToeBase",
}


def find_arm(pred):
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE" and pred({b.name for b in o.data.bones}):
            return o


print("importing...", flush=True)
bpy.ops.import_scene.gltf(filepath=GLB)
ual = find_arm(lambda n: "pelvis" in n)
bpy.ops.import_scene.fbx(filepath=FBX, ignore_leaf_bones=False)
mix = find_arm(lambda n: "mixamorig:Hips" in n)

ual_names = {b.name for b in ual.data.bones}
mix_names = {b.name for b in mix.data.bones}
pairs = [(s, d) for s, d in MAP.items() if s in ual_names and d in mix_names]
print("pairs: %d" % len(pairs), flush=True)

src_act = bpy.data.actions[CLIP]
src_act.name = "__src_" + CLIP
ual.animation_data_create().action = bpy.data.actions["__src_" + CLIP]

tgt_act = bpy.data.actions.new(CLIP)
mix.animation_data_create().action = tgt_act

for _, d in pairs:
    pb = mix.pose.bones[d]
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (1, 0, 0, 0)

f0 = int(round(src_act.frame_range[0]))
f1 = int(round(src_act.frame_range[1]))
print("keying frames %d..%d" % (f0, f1), flush=True)
for f in range(f0, f1 + 1):
    bpy.context.scene.frame_set(f)
    bpy.context.view_layer.update()
    for s, d in pairs:
        q = ual.pose.bones[s].matrix_basis.to_quaternion().normalized()
        pb = mix.pose.bones[d]
        pb.rotation_quaternion = q
        pb.keyframe_insert("rotation_quaternion", frame=f)

# push the active action into NLA (Blender 5.2 exporter bakes NLA strips)
print("pushing NLA...", flush=True)
if mix.animation_data.nla_tracks:
    mix.animation_data.nla_tracks.clear()
track = mix.animation_data.nla_tracks.new()
track.name = "Pose"
track.strips.new(tgt_act.name, f0, tgt_act)
bpy.context.scene.frame_set(f0)
bpy.ops.object.select_all(action="DESELECT")
mix.select_set(True)
bpy.context.view_layer.objects.active = mix

bpy.context.scene.frame_start = f0
bpy.context.scene.frame_end = f1

print("exporting...", flush=True)
if os.path.exists(OUT):
    os.remove(OUT)
bpy.ops.export_scene.fbx(
    filepath=OUT, check_existing=False, use_selection=True,
    object_types={"ARMATURE"}, add_leaf_bones=False,
    bake_anim=True, bake_anim_use_all_bones=True,
    bake_anim_use_all_actions=False, bake_anim_use_nla_strips=True,
    bake_anim_step=1, bake_anim_simplify_factor=0.0,
)
print("done", flush=True)
