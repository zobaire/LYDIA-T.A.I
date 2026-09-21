import bpy
op = bpy.ops.export_scene.fbx
props = op.get_rna_type().properties
for p in props:
    if p.identifier in {"rna_type", "filepath", "check_existing", "filter_glob", "files", "directory", "filemode", "relpath"}:
        continue
    print(p.identifier, "| default:", p.default if hasattr(p, "default") else "-")
