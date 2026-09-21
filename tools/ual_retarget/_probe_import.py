import bpy
op = bpy.ops.import_scene.fbx
props = op.get_rna_type().properties
for p in props:
    if p.identifier in {"rna_type","filepath","check_existing","filter_glob","files","directory","filemode"}:
        continue
    print(p.identifier, "| default:", getattr(p, "default", "-"))
