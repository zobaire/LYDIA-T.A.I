import bpy

# factory scene has a default cube
cube = bpy.data.objects["Cube"]
ad = cube.animation_data_create()
act = bpy.data.actions.new("ProbeAct")
ad.action = act

# insert two location keys
cube.location = (1.0, 0.0, 0.0)
cube.keyframe_insert("location", frame=1)
cube.location = (2.0, 0.0, 0.0)
cube.keyframe_insert("location", frame=10)

print("has fcurves attr:", hasattr(act, "fcurves"))
print("has layers attr:", hasattr(act, "layers"))
print("action name:", act.name)

if hasattr(act, "layers"):
    print("num layers:", len(act.layers))
    for li, layer in enumerate(act.layers):
        print(f"  layer[{li}] strips:", len(layer.strips))
        for si, strip in enumerate(layer.strips):
            print(f"    strip[{si}] name:", strip.name, "| type:", type(strip).__name__)
            attrs = [a for a in dir(strip) if not a.startswith("_")]
            print("    strip attrs:", attrs)
            # try to find channels/keys
            for attr in ("channels", "keys", "fcurves", "keyframes"):
                if hasattr(strip, attr):
                    v = getattr(strip, attr)
                    print(f"    strip.{attr} ->", v if not hasattr(v, "__len__") else f"len {len(v)}")
