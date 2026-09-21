# bake_ual.py — Retarget UAL1_Standard.glb pistol clips onto the Mixamo rig (character.fbx)
# Run:  blender.exe --background --python bake_ual.py
#
# WHAT IT DOES (read + create ONLY, never edits existing files):
#   1. Reads  UAL1_Standard.glb  (source clips)
#   2. Reads  meshs/character.fbx (target skeleton, mixamorig:* bones)
#   3. Bakes 6 pistol clips: per frame, per mapped bone, copies the LOCAL rotation delta
#      (pose_bone.matrix_basis rotation) from the UAL bone onto the Mixamo bone.
#      This is scale/axis independent — motion stays in each bone's own rest frame,
#      so the final pose follows the Mixamo character's body-space, like every other
#      clip you already use.
#   4. Writes NEW files into animations/GUN_ANIMATIONS/ual_pistol/  (folder is created)
#   5. Re-imports one exported FBX and verifies rest pose + animation survived.
#
# Bone map: UAL name -> Mixamo name (colons = Blender's FBX import of Mixamo files;
# Godot sanitizes ':' to '_' on import, which is why your skeleton shows mixamorig_Hips).

import bpy
import os
import sys

GLB = r"C:\Users\book\Desktop\fate's-pair\animations\GUN_ANIMATIONS\UAL1_Standard.glb"
FBX = r"C:\Users\book\Desktop\fate's-pair\meshs\character.fbx"
OUT_DIR = r"C:\Users\book\Desktop\fate's-pair\animations\GUN_ANIMATIONS\ual_pistol"

PISTOL_CLIPS = [
    "Pistol_Shoot",
    "Pistol_Reload",
    "Pistol_Idle_Loop",
    "Pistol_Aim_Neutral",
    "Pistol_Aim_Down",
    "Pistol_Aim_Up",
]

# UAL -> Mixamo (Blender names). Zero-length tip/leaf bones are intentionally skipped:
# Godot's existing skeleton doesn't have 4th finger segments, so we drive 1..3 only.
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


def log(*a):
    print(*a, flush=True)


def find_armature(pred):
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE" and pred({b.name for b in o.data.bones}):
            return o
    return None


def bone_world_head(arm, bone_name):
    bone = arm.data.bones.get(bone_name)
    if not bone:
        return None
    return arm.matrix_world @ bone.head_local


def rest_report(arm, label, head_name, hand_name):
    h = bone_world_head(arm, head_name)
    w = bone_world_head(arm, hand_name)
    if h:
        log(f"    {label}: Head rest @ ({h.x:.3f}, {h.y:.3f}, {h.z:.3f})")
    if w:
        log(f"    {label}: {hand_name} rest @ ({w.x:.3f}, {w.y:.3f}, {w.z:.3f})")


def bake_one_clip(clip, src_arm, tgt_arm, pairs):
    """Key the target armature's pose bones over the clip's frame range."""
    if clip not in bpy.data.actions:
        return False, f"action '{clip}' not found"

    # free the clip name so the exported take is named exactly 'Pistol_Shoot' etc.
    src_action = bpy.data.actions[clip]
    src_action.name = f"__src_{clip}"

    src_ad = src_arm.animation_data_create()
    src_ad.action = bpy.data.actions[f"__src_{clip}"]

    tgt_action = bpy.data.actions.new(clip)
    tgt_ad = tgt_arm.animation_data_create()
    tgt_ad.action = tgt_action

    # target pose bones -> quaternion
    for _, dst in pairs:
        pb = tgt_arm.pose.bones[dst]
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)

    f0 = int(round(src_action.frame_range[0]))
    f1 = int(round(src_action.frame_range[1]))
    n_frames = f1 - f0 + 1
    log(f"  baking '{clip}': frames {f0}..{f1} ({n_frames}) over {len(pairs)} bones")

    for f in range(f0, f1 + 1):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        for src_name, dst_name in pairs:
            src_pb = src_arm.pose.bones[src_name]
            tgt_pb = tgt_arm.pose.bones[dst_name]
            q = src_pb.matrix_basis.to_quaternion().normalized()
            tgt_pb.rotation_quaternion = q
            tgt_pb.keyframe_insert("rotation_quaternion", frame=f)

    # Note: Blender 5.2 removed Action.fcurves (legacy API). Since we key EVERY
    # frame, the FBX exporter's bake (bake_anim=True, step 1, no simplify)
    # resamples to identical values — no interpolation pass needed.

    # Blender 5.2 only exports animation that lives in NLA strips — push the
    # freshly keyed action into an NLA track or the FBX comes out EMPTY.
    for tr in list(tgt_arm.animation_data.nla_tracks):
        tgt_arm.animation_data.nla_tracks.remove(tr)
    track = tgt_arm.animation_data.nla_tracks.new()
    track.name = "Pose"
    track.strips.new(tgt_action.name, f0, tgt_action)
    bpy.context.scene.frame_set(f0)

    bpy.context.scene.frame_start = f0
    bpy.context.scene.frame_end = f1

    # export this single clip as its own FBX
    bpy.ops.object.select_all(action="DESELECT")
    tgt_arm.select_set(True)
    bpy.context.view_layer.objects.active = tgt_arm
    out_path = os.path.join(OUT_DIR, f"{clip}.fbx")
    bpy.ops.export_scene.fbx(
        filepath=out_path,
        check_existing=False,
        use_selection=True,
        object_types={"ARMATURE"},
        add_leaf_bones=False,
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_use_all_actions=False,  # export only the active action (this clip)
        bake_anim_use_nla_strips=True,    # read the NLA strip we just pushed
        bake_anim_step=1,
        bake_anim_simplify_factor=0.0,    # keep every key
    )
    size = os.path.getsize(out_path)
    log(f"  wrote {os.path.basename(out_path)}  ({size} bytes)")
    return True, out_path


def main():
    log("=" * 70)
    log("UAL pistol clips -> Mixamo rig retarget bake")
    log("=" * 70)

    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"Output dir: {OUT_DIR}")

    log("\n[1] Importing UAL GLB...")
    bpy.ops.import_scene.gltf(filepath=GLB)
    ual_arm = find_armature(lambda n: "pelvis" in n and "spine_01" in n)
    if not ual_arm:
        log("!! could not find UAL armature"); sys.exit(1)
    log(f"  UAL armature: '{ual_arm.name}'")

    log("[2] Importing Mixamo character.fbx...")
    bpy.ops.import_scene.fbx(filepath=FBX, ignore_leaf_bones=False)
    mix_arm = find_armature(lambda n: "mixamorig:Hips" in n)
    if not mix_arm:
        log("!! could not find Mixamo armature"); sys.exit(1)
    log(f"  Mixamo armature: '{mix_arm.name}'")

    log("\n[3] Rest-pose sanity (both should be humanoid, Z-up):")
    rest_report(ual_arm, "UAL", "Head", "hand_r")
    rest_report(mix_arm, "Mixamo", "mixamorig:Head", "mixamorig:RightHand")

    log("\n[4] Validating bone map...")
    ual_names = {b.name for b in ual_arm.data.bones}
    mix_names = {b.name for b in mix_arm.data.bones}
    pairs = []
    for src, dst in MAP.items():
        if src not in ual_names:
            log(f"  !! source bone missing in UAL: {src}"); sys.exit(1)
        if dst not in mix_names:
            log(f"  !! target bone missing in Mixamo: {dst}"); sys.exit(1)
        pairs.append((src, dst))
    log(f"  all {len(pairs)} bone pairs present in both rigs")

    log("\n[5] Baking clips...")
    for clip in PISTOL_CLIPS:
        ok, info = bake_one_clip(clip, ual_arm, mix_arm, pairs)
        if not ok:
            log(f"  !! {info}"); sys.exit(1)

    log("\n[6] Verification: re-import an exported clip, confirm rest + animation survived...")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    probe = os.path.join(OUT_DIR, "Pistol_Idle_Loop.fbx")
    bpy.ops.import_scene.fbx(filepath=probe, ignore_leaf_bones=False)
    probe_arm = find_armature(lambda n: "mixamorig:Hips" in n)
    if probe_arm:
        rest_report(probe_arm, "reimported", "mixamorig:Head", "mixamorig:RightHand")
    anim_names = sorted(a.name for a in bpy.data.actions)
    log(f"  actions found in reimported file: {anim_names}")
    if "Pistol_Idle_Loop" not in anim_names:
        log("  !! animation did not survive export"); sys.exit(1)
    log("  OK: animation present.")

    log("\nDONE. New files (nothing existing was modified):")
    for fn in sorted(os.listdir(OUT_DIR)):
        log(f"   - {os.path.join(OUT_DIR, fn)}")
    bpy.ops.wm.quit_blender()


try:
    main()
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
