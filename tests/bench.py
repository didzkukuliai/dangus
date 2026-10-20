import json
import math
import os
import statistics
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smoke_test import enable, make_image


def ms(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000.0)
    return round(statistics.median(out), 2)


def scene(pkg):
    sc = bpy.context.scene
    cfg = sc.dangus
    for o in list(bpy.data.objects):
        if o.type == "MESH":
            bpy.data.objects.remove(o)
    for k in range(2):
        L = cfg.layers.add()
        L.name = "Sky%d" % k
        for n in range(4):
            it = L.items.add()
            it.image = make_image("b%d%d" % (k, n), (0.2 * n, 0.5, 1 - 0.2 * n))
            m = it.masks.add()
            m.kind = "NOISE"
        L.masks.add().kind = "GRADIENT"
        L.blend = "OVERLAY" if k else "MIX"
    C = cfg.layers.add()
    C.name = "Clouds"
    for n in range(4):
        it = C.items.add()
        it.kind, it.image = "DECAL", make_image("c%d" % n, (1, 1, 1))
        if "alpha_source" in it.bl_rna.properties:
            it.alpha_source = "LUMINANCE" if n % 2 == 0 else "CHANNEL"
        else:
            it.alpha_from_luminance = n % 2 == 0
        for kind in ("HUE_SAT", "RAMP", "CURVES"):
            m = it.modifiers.add()
            m.kind, m.uid = kind, "bm%d%s" % (n, kind)
        it.masks.add().kind = "VORONOI"
    fog = C.items.add()
    fog.kind, fog.image, fog.fog_mode = "FOG", make_image("fog", (1, 1, 1)), "TEXTURE"
    grad = C.items.add()
    grad.kind = "FOG"
    S = cfg.layers.add()
    S.name = "Sun"
    if "kind" in S.bl_rna.properties:
        S.kind, S.blend = "SUN", "ADD"
        S.items.add().kind = "SUN_COLOR"
    else:
        S.sun_enabled = True
        S.items.add().image = make_image("sunbase", (0, 0, 0))
    return sc, cfg, C


def node_stats():
    groups = [g for g in bpy.data.node_groups if g.name.startswith("dangus")]
    frames = sum(1 for g in groups for n in g.nodes if n.bl_idname == "NodeFrame")
    return sum(len(g.nodes) for g in groups), sum(len(g.links) for g in groups), len(groups), frames


def render(sc, engine):
    sc.render.engine = engine
    sc.render.resolution_x = sc.render.resolution_y = 64
    sc.cycles.samples, sc.cycles.use_denoising = 8, False
    sc.eevee.taa_render_samples = 8
    t = time.perf_counter()
    bpy.ops.render.render()
    first = (time.perf_counter() - t) * 1000.0
    t = time.perf_counter()
    bpy.ops.render.render()
    return round(first, 1), round((time.perf_counter() - t) * 1000.0, 1)


def main():
    pkg = enable()
    nb = pkg.nodes_builder
    sc, cfg, C = scene(pkg)
    nb.build_sky(sc)
    res = {"version": pkg.utils.VERSION if hasattr(pkg, "utils") else "?"}
    res["build_ms"] = ms(lambda: nb.build_sky(sc), 5)
    cloud = C.items[0]

    def value_edit():
        cloud.opacity = 0.5 if cloud.opacity > 0.75 else 1.0
        nb.refresh(sc)
    res["sync_ms"] = ms(value_edit, 20)
    res["noop_ms"] = ms(lambda: nb.refresh(sc), 20)

    def structural():
        if len(cloud.masks) > 1:
            cloud.masks.remove(1)
        else:
            cloud.masks.add().kind = "NOISE"
        nb.refresh(sc)
    res["struct_ms"] = ms(structural, 6)
    res["nodes"], res["links"], res["groups"], res["frames"] = node_stats()
    res["eevee_first_ms"], res["eevee_ms"] = render(sc, "BLENDER_EEVEE")
    res["cycles_first_ms"], res["cycles_ms"] = render(sc, "CYCLES")
    print("BENCH " + json.dumps(res))


if __name__ == "__main__":
    main()
