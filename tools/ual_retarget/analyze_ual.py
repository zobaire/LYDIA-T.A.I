# analyze_ual.py — READ-ONLY analysis: compare UAL1_Standard.glb rig vs Mixamo character.fbx rig
# Run:  blender.exe --background --python analyze_ual.py
# Touches nothing on disk; only imports both assets and prints a bone-mapping report.

import bpy
import sys
import os

GLB = r"C:\Users\book\Desktop\fate's-pair\animations\GUN_ANIMATIONS\UAL1_Standard.glb"
FBX = r"C:\Users\book\Desktop\fate's-pair\meshs\character.fbx"

# UAL bone -> Mixamo bone (mixamorig_*). Leaf bones (xx_04_leaf, ball_leaf) intentionally skipped.
UAL_TO_MIXAMO = {
    "pelvis": "mixamorig_Hips",
    "spine_01": "mixamorig_Spine",
    "spine_02": "mixamorig_Spine1",
    "spine_03": "mixamorig_Spine2",
    "neck_01": "mixamorig_Neck",
    "Head": "mixamorig_Head",
    "clavicle_l": "mixamorig_LeftShoulder",
    "upperarm_l": "mixamorig_LeftArm",
    "lowerarm_l": "mixamorig_LeftForeArm",
    "hand_l": "mixamorig_LeftHand",
    "index_01_l": "mixamorig_LeftHandIndex1",
    "index_02_l": "mixamorig_LeftHandIndex2",
    "index_03_l": "mixamorig_LeftHandIndex3",
    "middle_01_l": "mixamorig_LeftHandMiddle1",
    "middle_02_l": "mixamorig_LeftHandMiddle2",
    "middle_03_l": "mixamorig_LeftHandMiddle3",
    "pinky_01_l": "mixamorig_LeftHandPinky1",
    "pinky_02_l": "mixamorig_LeftHandPinky2",
    "pinky_03_l": "mixamorig_LeftHandPinky3",
    "ring_01_l": "mixamorig_LeftHandRing1",
    "ring_02_l": "mixamorig_LeftHandRing2",
    "ring_03_l": "mixamorig_LeftHandRing3",
    "thumb_01_l": "mixamorig_LeftHandThumb1",
    "thumb_02_l": "mixamorig_LeftHandThumb2",
    "thumb_03_l": "mixamorig_LeftHandThumb3",
    "thigh_l": "mixamorig_LeftUpLeg",
    "calf_l": "mixamorig_LeftLeg",
    "foot_l": "mixamorig_LeftFoot",
    "ball_l": "mixamorig_LeftToeBase",
    "clavicle_r": "mixamorig_RightShoulder",
    "upperarm_r": "mixamorig_RightArm",
    "lowerarm_r": "mixamorig_RightForeArm",
    "hand_r": "mixamorig_RightHand",
    "index_01_r": "mixamorig_RightHandIndex1",
    "index_02_r": "mixamorig_RightHandIndex2",
    "index_03_r": "mixamorig_RightHandIndex3",
    "middle_01_r": "mixamorig_RightHandMiddle1",
    "middle_02_r": "mixamorig_RightHandMiddle2",
    "middle_03_r": "mixamorig_RightHandMiddle3",
    "pinky_01_r": "mixamorig_RightHandPinky1",
    "pinky_02_r": "mixamorig_RightHandPinky2",
    "pinky_03_r": "mixamorig_RightHandPinky3",
    "ring_01_r": "mixamorig_RightHandRing1",
    "ring_02_r": "mixamorig_RightHandRing2",
    "ring_03_r": "mixamorig_RightHandRing3",
    "thumb_01_r": "mixamorig_RightHandThumb1",
    "thumb_02_r": "mixamorig_RightHandThumb2",
    "thumb_03_r": "mixamorig_RightHandThumb3",
    "thigh_r": "mixamorig_RightUpLeg",
    "calf_r": "mixamorig_RightLeg",
    "foot_r": "mixamorig_RightFoot",
    "ball_r": "mixamorig_RightToeBase",
}

OUT = sys.stdout


def log(*a):
    print(*a, file=OUT, flush=True)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def list_bones(arm_obj, label):
    arm = arm_obj.data
    bones = arm.bones
    log(f"--- {label}: {len(bones)} bones ---")
    roots = [b for b in bones if b.parent is None]
    for r in roots:
        _print_chain(r, 0)
    log("")


def _print_chain(bone, depth):
    log("  " * depth + bone.name)
    for c in bone.children:
        _print_chain(c, depth + 1)


def main():
    log("=" * 70)
    log("UAL1_Standard.glb vs character.fbx — READ-ONLY analysis")
    log("=" * 70)

    # ---- import GLB ----
    log("\n[1] Importing UAL GLB...")
    bpy.ops.import_scene.gltf(filepath=GLB)
    ual_arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not ual_arms:
        log("!! no armature found in GLB"); return
    # GLB may contain a skeleton mesh too; pick the armature that owns the UAL bones
    ual_arm = None
    for a in ual_arms:
        names = {b.name for b in a.data.bones}
        if "pelvis" in names and "spine_01" in names:
            ual_arm = a
    if ual_arm is None:
        ual_arm = ual_arms[0]
    log(f"UAL armature object: '{ual_arm.name}'")
    list_bones(ual_arm, "UAL skeleton (full)")

    # ---- list pistol actions from the GLB ----
    log("[2] Pistol-related actions in GLB:")
    for act in bpy.data.actions:
        n = act.name
        if "Pistol" in n or "TPose" in n:
            fr = act.frame_range
            log(f"    {n:28s} frames {int(fr[0])}..{int(fr[1])}  ({int(fr[1]-fr[0]+1)} frames)")
    log("")

    # ---- import Mixamo FBX ----
    log("[3] Importing character.fbx (Mixamo rig)...")
    bpy.ops.import_scene.fbx(filepath=FBX, ignore_leaf_bones=False)
    mix_arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.name != ual_arm.name]
    if not mix_arms:
        log("!! no armature found in FBX"); return
    mix_arm = None
    for a in mix_arms:
        names = {b.name for b in a.data.bones}
        if "mixamorig_Hips" in names:
            mix_arm = a
    if mix_arm is None:
        mix_arm = mix_arms[0]
    log(f"Mixamo armature object: '{mix_arm.name}'")
    list_bones(mix_arm, "Mixamo skeleton (full)")

    # ---- mapping check ----
    log("[4] Mapping check (UAL -> Mixamo):")
    ual_names = {b.name for b in ual_arm.data.bones}
    mix_names = {b.name for b in mix_arm.data.bones}
    ok = missing_src = missing_dst = 0
    missing_src_list, missing_dst_list = [], []
    for src, dst in UAL_TO_MIXAMO.items():
        if src not in ual_names:
            missing_src += 1; missing_src_list.append(src); continue
        if dst not in mix_names:
            missing_dst += 1; missing_dst_list.append(dst); continue
        ok += 1
    log(f"    matched: {ok}   |  missing in UAL: {missing_src_list or 'none'}   |  missing in Mixamo: {missing_dst_list or 'none'}")

    # extra UAL bones not in map (skipped on purpose?)
    unmapped = sorted(ual_names - set(UAL_TO_MIXAMO.keys()) - {"root", "Mannequin", "Armature", "Head"})
    # (Head is in map; keep list clean)
    unmapped = sorted(ual_names - set(UAL_TO_MIXAMO.keys()))
    log(f"    UAL bones NOT in map (will be skipped): {unmapped}")

    log("\n[5] Rest-pose axis sanity (world head offset of UAL vs Mixamo):")
    for arm, label in ((ual_arm, "UAL"), (mix_arm, "Mixamo")):
        head_bone = next((b for b in arm.data.bones if b.name in ("Head", "mixamorig_Head")), None)
        if head_bone:
            world = arm.matrix_world @ head_bone.head_local
            log(f"    {label}: Head rest head @ ({world.x:.3f}, {world.y:.3f}, {world.z:.3f})")
    log("\nDone. Nothing was written to disk.")

    # force blender to exit cleanly
    bpy.ops.wm.quit_blender()


try:
    main()
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
