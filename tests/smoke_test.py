import math
import os
import subprocess
import sys
import tempfile
import time

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def enable():
    import addon_utils
    import importlib
    installed = {m.__name__ for m in addon_utils.modules(refresh=True)}
    for mod in ("bl_ext.user_default.dangus", "bl_ext.blender_org.dangus"):
        if mod in installed:
            addon_utils.enable(mod, default_set=False)
            if hasattr(bpy.context.scene, "dangus"):
                print("testing installed extension:", mod)
                return importlib.import_module(mod)
    sys.path.insert(0, ROOT)
    import dangus
    if not hasattr(bpy.types.Scene, "dangus"):
        dangus.register()
    print("testing source tree:", os.path.dirname(dangus.__file__))
    return dangus


def write_png(path, rgb, w=64, h=32, alpha=False):
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True)
    px = []
    for y in range(h):
        for x in range(w):
            u, v = x / (w - 1), y / (h - 1)
            a = max(0.0, 1.0 - 2.0 * math.hypot(u - 0.5, v - 0.5)) if alpha else 1.0
            px += [rgb[0] * (0.5 + 0.5 * u), rgb[1] * (0.5 + 0.5 * v), rgb[2], a]
    img.pixels.foreach_set(px)
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return path


def make_image(name, rgb):
    img = bpy.data.images.new(name, 32, 16, alpha=True)
    img.pixels.foreach_set([c for _ in range(32 * 16) for c in (*rgb, 1.0)])
    return img


class Groups:

    def __init__(self, scene=None):
        self.top = (scene or bpy.context.scene).world.node_tree.nodes["DG_Sky"].node_tree
        assert self.top.get("dangus") == "top", self.top.name
        self.interface, self.trees = self.top.interface, [self.top]
        for g in self.trees:
            for n in g.nodes:
                if n.bl_idname == "ShaderNodeGroup" and n.node_tree not in self.trees:
                    assert n.node_tree.get("dangus"), n.node_tree.name
                    self.trees.append(n.node_tree)
        self.nodes = [n for g in self.trees for n in g.nodes]
        self.links = [l for g in self.trees for l in g.links]
        self.drivers = [fc for g in self.trees if g.animation_data for fc in g.animation_data.drivers]


def group(scene=None):
    return Groups(scene)


def check_world(tree):
    names = sorted(n.name for n in tree.nodes if n.name.startswith("DG_"))
    assert names == ["DG_Background", "DG_Output", "DG_Sky"], names
    assert tree.nodes["DG_Output"].is_active_output
    assert tree.nodes["DG_Background"].inputs["Color"].is_linked, "the sky is not connected"


def check_connected(gs):
    for g in gs.trees:
        linked_from = {l.from_node.name for l in g.links}
        for n in g.nodes:
            if n.bl_idname == "NodeFrame":
                continue
            required = []
            if n.bl_idname == "NodeGroupOutput":
                required = [s.name for s in n.inputs if s.name == "Color"]
            elif (n.bl_idname.startswith("ShaderNodeTex") and n.bl_idname != "ShaderNodeTexCoord") or (
                    n.bl_idname == "ShaderNodeMapping" and not n.name.startswith("DG_CloudOrigin")):
                required = ["Vector"]
            elif n.bl_idname == "ShaderNodeMix" and n.data_type == "RGBA":
                required = ["B_Color"] if n.name.startswith("DG_Layer") else ["A_Color"] if \
                    n.name.startswith(("DG_Ground", "DG_SunGlowTint")) else ["A_Color", "B_Color"]
            elif n.bl_idname == "ShaderNodeMapRange":
                required = ["Value"]
            for ident in required:
                s = next((s for s in n.inputs if ident in (s.identifier, s.name)), None)
                assert s is not None, "%s/%s: no socket %r" % (g.name, n.name, ident)
                assert s.is_linked, "%s/%s.%s is not connected" % (g.name, n.name, ident)
            if n.bl_idname not in ("NodeGroupOutput", "NodeGroupInput"):
                assert n.name in linked_from, "%s/%s output is unused" % (g.name, n.name)


def count(g, prefix):
    return sum(1 for n in g.nodes if n.name.startswith(prefix))


def tag(gs):
    for g in gs.trees:
        n = next((n for n in g.nodes if n.bl_idname != "NodeFrame"), None)
        if n is not None:
            n["probe"] = 1


def survived(gs):
    return all(any(n.get("probe") for n in g.nodes) for g in gs.trees if len(g.nodes))


def eevee_id():
    ids = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
           if e.identifier.startswith("BLENDER_EEVEE")]
    assert ids, "no EEVEE engine id found"
    return ids[0]


def setup_render(sc, engine):
    sc.render.engine = engine
    sc.render.resolution_x, sc.render.resolution_y = 64, 64
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "Standard"
    sc.cycles.samples = 4
    sc.cycles.seed = 0
    sc.cycles.device = "CPU"
    sc.eevee.taa_render_samples = 4


def render_mean(path):
    bpy.ops.render.render(write_still=False)
    bpy.data.images["Render Result"].save_render(path)
    img = bpy.data.images.load(path, check_existing=False)
    px = list(img.pixels)
    bpy.data.images.remove(img)
    return sum(px[i] for i in range(len(px)) if i % 4 != 3) / (len(px) * 0.75)


def render(engine, tmp):
    sc = bpy.context.scene
    setup_render(sc, engine)
    for f in (1, 100):
        sc.frame_set(f)
        mean = render_mean(os.path.join(tmp, "r_%s_%d.png" % (engine, f)))
        assert mean > 0.02, "%s frame %d rendered black (mean %.4f)" % (engine, f, mean)
    print("render OK: %s (mean %.3f)" % (engine, mean))


def add_images(tmp, names, kind):
    files = [{"name": os.path.basename(p)} for p in names]
    r = bpy.ops.dangus.add_image(directory=tmp, files=files, kind=kind)
    assert r == {"FINISHED"}, "add_image(%s) returned %s" % (kind, r)


def cloud_geometry_test(pkg, tmp):
    import numpy as np
    from mathutils import Vector
    decals, nodes_builder = pkg.decals, pkg.nodes_builder
    sc = bpy.data.scenes.new("geo")
    sc.render.engine = "CYCLES"
    sc.cycles.samples, sc.cycles.device = 4, "CPU"
    sc.render.resolution_x = sc.render.resolution_y = 96
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "Standard"
    cam = bpy.data.objects.new("geo_cam", bpy.data.cameras.new("geo_cam"))
    cam.data.angle = math.radians(60.0)
    sc.collection.objects.link(cam)
    sc.camera = cam
    L = sc.dangus.layers.add()
    it = L.items.add()
    it.kind, it.image, it.softness = "DECAL", make_image("geo_cloud", (1, 1, 1)), 0.0
    img = it.image
    img.scale(64, 32)
    img.pixels.foreach_set([1.0] * (64 * 32 * 4))
    nodes_builder.build_sky(sc)
    ob = it.decal_object
    half = math.tan(math.radians(10.0)) * 10.0
    d = Vector((0.3, -0.8, 0.5)).normalized()
    decals.place(ob, d, 10.0, half)

    def shot(look, name):
        cam.rotation_euler = (-look).to_track_quat("Z", "Y").to_euler()
        bpy.context.view_layer.update()
        bpy.ops.render.render(scene=sc.name)
        path = os.path.join(tmp, name + ".png")
        bpy.data.images["Render Result"].save_render(path, scene=sc)
        im = bpy.data.images.load(path, check_existing=False)
        a = np.empty(len(im.pixels), np.float32)
        im.pixels.foreach_get(a)
        bpy.data.images.remove(im)
        lum = a.reshape(96, 96, 4)[..., :3].mean(-1)
        ys, xs = np.nonzero(lum > 0.5)
        return xs, ys

    expect_w = math.tan(math.radians(10.0)) / math.tan(math.radians(30.0)) * 96
    xs, ys = shot(d, "geo_center")
    assert len(xs), "cloud not visible where its Empty is"
    cx, cy = xs.mean(), ys.mean()
    w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert abs(cx - 47.5) < 1.5 and abs(cy - 47.5) < 1.5, (cx, cy)
    assert abs(w - expect_w) < 3 and abs(h - expect_w / 2) < 3, (w, h, expect_w)

    decals.place(ob, d, 10.0, half, roll=math.pi / 2)
    xs, ys = shot(d, "geo_roll")
    w2, h2 = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert abs(w2 - h) < 3 and abs(h2 - w) < 3, ((w, h), (w2, h2))

    d2 = Vector((-0.6, 0.2, 0.3)).normalized()
    ob.location = d2 * 10.0
    xs, ys = shot(d2, "geo_moved")
    assert len(xs) and abs(xs.mean() - 47.5) < 4 and abs(ys.mean() - 47.5) < 4, "cloud did not follow"
    xs, _ = shot(-d2, "geo_behind")
    assert len(xs) == 0, "cloud visible behind the viewer"
    bpy.data.scenes.remove(sc)
    print("cloud geometry OK: centred, %.0fx%.0f px (expected %.0fx%.0f), rolls, follows"
          % (w, h, expect_w, expect_w / 2))


def pano(name, tmp, W=144, H=72):
    import numpy as np
    sc = bpy.data.scenes.new(name)
    sc.render.engine = "CYCLES"
    sc.cycles.samples, sc.cycles.device = 1, "CPU"
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "OPEN_EXR"
    cam = bpy.data.objects.new(name + "_cam", bpy.data.cameras.new(name + "_cam"))
    cam.data.type, cam.data.panorama_type = "PANO", "EQUIRECTANGULAR"
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.collection.objects.link(cam)
    sc.camera = cam

    def shot():
        bpy.ops.render.render(scene=sc.name)
        path = os.path.join(tmp, name + ".exr")
        bpy.data.images["Render Result"].save_render(path, scene=sc)
        im = bpy.data.images.load(path, check_existing=False)
        a = np.empty(len(im.pixels), np.float32)
        im.pixels.foreach_get(a)
        bpy.data.images.remove(im)
        return a.reshape(H, W, 4)[..., :3]

    def row(el_deg):
        return int((el_deg + 90.0) / 180.0 * H)
    return sc, shot, row


def fog_test(pkg, tmp):
    import numpy as np
    nodes_builder = pkg.nodes_builder
    sc, shot, row = pano("fog", tmp)
    L = sc.dangus.layers.add()
    L.items.add().image = make_image("fog_white", (1, 1, 1))
    nodes_builder.build_sky(sc)
    fog = L.items.add()
    fog.kind, fog.uid = "FOG", "fogtest"
    fog.fog_color, fog.fog_density = (1.0, 0.0, 0.0), 1.0
    fog.fog_top, fog.fog_fade, fog.fog_falloff = math.radians(40.0), math.radians(40.0), "LINEAR"
    assert nodes_builder.refresh(sc) == "rebuild"
    px = shot()
    assert np.allclose(px[row(2)].mean(0), (1, 0.1, 0.1), atol=0.1), px[row(2)].mean(0)
    assert np.allclose(px[row(60)].mean(0), (1, 1, 1), atol=0.02), px[row(60)].mean(0)
    mid = px[row(20)].mean(0)[1]
    assert 0.35 < mid < 0.65, mid
    assert px[row(-30)].max() < 0.05, "below the horizon is the ground"
    fog.fog_density = 0.5
    assert nodes_builder.refresh(sc) == "sync"
    assert np.allclose(shot()[row(1)].mean(0), (1, 0.5, 0.5), atol=0.05)

    lr = bpy.data.images.new("fog_lr", 64, 32, alpha=True)
    lr.pixels.foreach_set([c for y in range(32) for x in range(64)
                           for c in ((1, 0, 0, 1) if x < 32 else (0, 1, 0, 1))])
    fog.image, fog.fog_mode, fog.fog_color = lr, "TEXTURE", (1, 1, 1)
    fog.fog_density, fog.fog_top, fog.fog_fade = 1.0, math.radians(45.0), math.radians(1.0)
    assert nodes_builder.refresh(sc) == "rebuild"

    def bands(el_deg):
        red = shot()[row(el_deg)]
        red = red[:, 0] > red[:, 1]
        return int(np.count_nonzero(red != np.roll(red, 1)))

    expect = {"STRETCH": 2, "FILL": 8, "REPEAT": 18}
    for fit, n in expect.items():
        fog.fog_fit, fog.fog_tile = fit, math.radians(20.0)
        assert nodes_builder.refresh(sc) == "sync", "fit is a live value"
        got = bands(20)
        assert got == n, "%s: %d colour changes around the dome, expected %d" % (fit, got, n)
    tb = bpy.data.images.new("fog_tb", 32, 32, alpha=True)
    tb.pixels.foreach_set([c for y in range(32) for x in range(32)
                           for c in ((0, 0, 1, 1) if y < 16 else (0, 1, 0, 1))])
    fog.image, fog.fog_fit = tb, "STRETCH"
    nodes_builder.refresh(sc)
    px = shot()
    assert px[row(5)].mean(0)[2] > 0.9 and px[row(38)].mean(0)[1] > 0.9, \
        (px[row(5)].mean(0), px[row(38)].mean(0))
    over = L.items.add()
    over.kind, over.fog_color, over.fog_density = "FOG", (0, 0, 0), 1.0
    over.fog_top, over.fog_fade = math.radians(45.0), math.radians(1.0)
    nodes_builder.refresh(sc)
    assert shot()[row(20)].max() < 0.02, "the upper fog item must cover the lower one"
    L.items.move(2, 1)
    nodes_builder.refresh(sc)
    assert shot()[row(5)].mean(0)[2] > 0.9
    bpy.data.scenes.remove(sc)
    print("fog OK: rises from the horizon (ground below), gradient profile, live density, "
          "Stretch/Fill/Repeat, bottom-up, list order")


def sun_test(pkg, tmp):
    import numpy as np
    from mathutils import Vector
    nodes_builder, decals = pkg.nodes_builder, pkg.decals
    W, H = 288, 144
    sc, shot, row = pano("sun", tmp, W, H)
    cfg = sc.dangus
    base = cfg.layers.add()
    base.items.add().image = make_image("sun_black", (0, 0, 0))
    S = cfg.layers.add()
    S.kind, S.blend = "SUN", "ADD"
    col = S.items.add()
    col.kind, col.solid_color = "SUN_COLOR", (1, 1, 1)
    S.sun_azimuth, S.sun_elevation = 0.0, math.radians(30.0)
    S.sun_size, S.sun_softness = math.radians(10.0), 0.05
    S.sun_strength, S.sun_cloud_strength = 10.0, 1.0
    S.sun_glow, S.sun_scatter = 0.0, 0.0
    nodes_builder.build_sky(sc)

    def peak(channel=0):
        px = shot()[..., channel]
        ys, xs = np.nonzero(px > 0.9 * px.max())
        ang = xs / W * 2 * math.pi
        x = (math.atan2(np.sin(ang).mean(), np.cos(ang).mean()) / (2 * math.pi) * W) % W
        return px.max(), int(round(ys.mean())), int(round(x)) % W

    v, y, x0 = peak()
    assert abs(v - 10.0) < 0.3, "clear-sky disc should be 10, got %.3f" % v
    assert abs(y - row(30)) <= 2, (y, row(30))
    S.sun_azimuth = math.radians(90.0)
    assert nodes_builder.refresh(sc) == "sync"
    _, _, x1 = peak()
    assert min(abs(x1 - x0) % W, W - abs(x1 - x0) % W) in range(W // 4 - 3, W // 4 + 4), (x0, x1)

    top = cfg.layers.add()
    it = top.items.add()
    it.kind, it.image = "DECAL", make_image("sun_cloud", (1, 1, 1))
    it.tint, it.softness = (0, 0, 0), 0.0
    decals.sync_objects(sc)
    ce = math.cos(math.radians(30.0))
    d = Vector((ce * math.cos(S.sun_azimuth), ce * math.sin(S.sun_azimuth), 0.5))
    decals.place(it.decal_object, d, 10.0, 10.0 * math.tan(math.radians(25.0)))
    assert nodes_builder.refresh(sc) == "rebuild"
    check_world(sc.world.node_tree)
    v, _, _ = peak()
    assert abs(v - 1.0) < 0.05, "behind full cloud the disc should be 1, got %.3f" % v
    it.opacity = 0.5
    assert nodes_builder.refresh(sc) == "sync"
    v, _, _ = peak()
    assert abs(v - 10.0 * 0.1 ** 0.5) < 0.15, "half cover: expected %.3f, got %.3f" % (
        10.0 * 0.1 ** 0.5, v)
    S.sun_scatter = 1.0
    assert nodes_builder.refresh(sc) == "sync"
    v_scatter = peak()[0]
    assert v_scatter > v + 0.5, "scatter must light thin cloud even without glow: %.3f" % v_scatter
    S.sun_glow = 1.0
    nodes_builder.refresh(sc)
    assert peak()[0] > v_scatter
    S.sun_scatter, S.sun_glow = 0.0, 0.0
    it.opacity = 1.0
    cfg.layers.move(2, 0)
    nodes_builder.refresh(sc)
    assert abs(peak()[0] - 10.0) < 0.3, "a layer below must not hide the sun"
    cfg.layers.move(0, 2)

    top.items.remove(0)
    top.items.add().image = make_image("sun_white", (1, 1, 1))
    top.blend = "MIX"
    nodes_builder.refresh(sc)
    assert abs(peak()[0] - 1.0) < 0.05, "a sky layer above hides the sun: %.3f" % peak()[0]
    top.blend = "ADD"
    assert nodes_builder.refresh(sc) == "sync", "blend mode is a live value"
    assert abs(peak()[0] - 11.0) < 0.3, peak()[0]
    cfg.layers.remove(2)

    col.solid_color = (1, 0, 0)
    nodes_builder.refresh(sc)
    px = shot()
    v, y, x = peak()
    assert px[y, x, 1] < 0.05 and abs(px[y, x, 0] - 10.0) < 0.3, px[y, x]
    img = S.items.add()
    tex = bpy.data.images.new("sun_tex", 64, 32, alpha=True)
    tex.pixels.foreach_set([c for yy in range(32) for xx in range(64)
                            for c in ((0, 0, 1, 1) if xx < 32 else (0, 1, 0, 1))])
    img.kind, img.image = "SUN_IMAGE", tex
    img.map_scale = (math.tan(math.radians(4.0)) / math.tan(math.radians(15.0)),) * 2 + (1.0,)
    nodes_builder.refresh(sc)
    seg = shot()[y - 8:y + 9][:, [(x + k) % W for k in range(-8, 9)]].reshape(-1, 3)
    blue = [p for p in seg if p[2] > 5.0 and p[1] < 1.0 and p[0] < 0.5]
    green = [p for p in seg if p[1] > 5.0 and p[2] < 1.0 and p[0] < 0.5]
    assert blue and green, "the sun image fills the disc as a texture, lit like the disc"
    assert seg[:, 0].max() < 0.5, "the image covers the red solid colour"
    S.sun_size = math.radians(4.0)
    nodes_builder.refresh(sc)
    seg = shot()[y - 8:y + 9][:, [(x + k) % W for k in range(-8, 9)]].reshape(-1, 3)
    lit = [p for p in seg if p.max() > 5.0]
    assert 0 < len(lit) < len(blue) + len(green), "Size shrinks the image with the disc"
    img.emission = 0.5
    nodes_builder.refresh(sc)
    seg = shot()[y - 8:y + 9][:, [(x + k) % W for k in range(-8, 9)]]
    assert 4.0 < seg.max() < 5.5, "image strength scales it: %.2f" % seg.max()
    img.emission = 1.0
    S.items.remove(0)
    assert nodes_builder.refresh(sc) == "rebuild"
    seg = shot()[y - 14:y + 15][:, [(x + k) % W for k in range(-14, 15)]]
    lit = np.argwhere(seg.max(axis=2) > 2.0)
    assert len(lit), "an image-only sun is drawn"
    assert seg[..., 0].max() < 0.5, "an image-only sun draws no white disc"
    assert abs(lit[:, 0].mean() - 14) <= 1.5 and abs(lit[:, 1].mean() - 14) <= 1.5, (
        "the image sits on the sun", lit.mean(axis=0))
    S.sun_glow = 1.0
    nodes_builder.refresh(sc)
    p = shot()[y, (x + 30) % W]
    assert p[1] > 1e-3 and abs(p[1] - p[2]) < 0.2 * p[1] and p[0] < 0.1 * p[1], ("the glow takes the image colour", p)
    S.sun_glow = 0.0
    S.items.add().kind = "SUN_COLOR"
    S.items.move(1, 0)
    col = S.items[0]
    S.sun_size = math.radians(10.0)
    S.items.remove(1)
    col.solid_color = (1, 1, 1)

    S.sun_elevation = math.radians(-20.0)
    m = S.masks.add()
    m.kind, m.angle_from, m.angle_to = "GRADIENT", math.radians(-5), math.radians(5)
    nodes_builder.refresh(sc)
    assert shot()[..., 0].max() < 0.05, "the masked sun should be hidden"
    S.masks.clear()
    S.sun_elevation = math.radians(30.0)

    S.sun_mode, S.sun_latitude, S.sun_day, S.sun_hours = "TIME", 0.0, 81, 9.0
    assert nodes_builder.refresh(sc) == "rebuild"
    _, y2, x2 = peak()
    assert abs(y2 - row(45)) <= 2 and min(abs(x2 - x0), W - abs(x2 - x0)) <= 3, ((y2, x2), x0)
    S.sun_hours = 15.0
    assert nodes_builder.refresh(sc) == "sync", "the time of day is a live value"
    _, y3, x3 = peak()
    assert abs(y3 - row(45)) <= 2 and min(abs(x3 - x0 - W // 2) % W, W - abs(x3 - x0 - W // 2) % W) <= 3
    hm, hours = pkg.utils.hours_to_hm, pkg.utils.hm_to_hours
    assert [round(hm(h), 2) for h in (10.5, 10.99, 0.0, 23.999, 9.0 + 47 / 60)] == [
        10.3, 10.59, 0.0, 24.0, 9.47], "hours.minutes"
    assert [round(hours(v), 4) for v in (10.3, 9.47, 9.75, 0.0, 24.0)] == [
        10.5, round(9 + 47 / 60, 4), round(9 + 59 / 60, 4), 0.0, 24.0], "minutes stop at 59"
    S.sun_time = 10.30
    assert abs(S.sun_hours - 10.5) < 1e-6 and abs(S.sun_time - 10.3) < 1e-6, "the slider reads h.mm"
    S.sun_hours = 9.0
    S.sun_mode, S.sun_azimuth, S.sun_elevation = "ANGLES", 0.0, math.radians(30.0)
    nodes_builder.refresh(sc)

    S.sun_mode, S.sun_azimuth, S.sun_elevation = "ANGLES", 0.0, math.radians(30.0)

    base.items[0].image = make_image("sun_grey", (0.5, 0.5, 0.5))
    S.sun_strength = S.sun_cloud_strength = S.sun_glow = 0.0
    nodes_builder.refresh(sc)
    plain = shot()[row(60)].mean()
    S.sun_relight_sky, S.sun_elevation = True, math.radians(3.0)
    assert nodes_builder.refresh(sc) == "rebuild"
    px = shot()
    toward, away = px[row(8), x0], px[row(8), (x0 + W // 2) % W]
    assert toward[0] > 2.0 * toward[2], "towards a low sun the sky turns warm: %s" % toward
    assert abs(away[0] - away[2]) < 0.01 and 0.7 * plain < away[0] < plain, "away from it stays neutral: %s" % away
    S.sun_elevation = math.radians(-20.0)
    assert nodes_builder.refresh(sc) == "sync"
    assert shot()[row(60)].mean() < 0.1 * plain, "night darkens the layers below"
    S.sun_relight_amount = 0.0
    nodes_builder.refresh(sc)
    assert abs(shot()[row(60)].mean() - plain) < 1e-3
    S.sun_relight_sky, S.sun_relight_amount = False, 1.0
    S.sun_strength, S.sun_cloud_strength, S.sun_elevation = 10.0, 1.0, math.radians(3.0)
    nodes_builder.refresh(sc)
    white = shot()[peak()[1], peak()[2]]
    S.sun_relight_disc = True
    assert nodes_builder.refresh(sc) == "rebuild"
    px = shot()
    _, y, x = peak()
    assert px[y, x, 0] > 3.0 * px[y, x, 2] and abs(white[0] - white[2]) < 0.05, \
        "relight sun: a low sun turns orange (%s, white was %s)" % (px[y, x], white)
    S.sun_relight_disc, S.sun_elevation = False, math.radians(30.0)

    bpy.data.scenes.remove(sc)
    print("sun layer OK: placement, clouds above dim it (10 / 1 / 3.16 Beer-Lambert), sky "
          "layers above hide it, scatter without glow, colour, image, mask, time of day "
          "(hours:minutes, keyframed), keyframed azimuth, relight sky (sunset, night), relight sun, sun image as a texture")


def modifiers_test(pkg, tmp):
    import numpy as np
    nodes_builder = pkg.nodes_builder
    sc, shot, row = pano("mods", tmp, 64, 32)
    L = sc.dangus.layers.add()
    it = L.items.add()
    it.image = make_image("mods_white", (1, 1, 1))
    it.uid = "modsitem"
    nodes_builder.build_sky(sc)

    def colour():
        return shot()[18:28].reshape(-1, 3).mean(0)

    def check(want, what):
        got = colour()
        assert np.allclose(got, want, atol=0.03), "%s: %s, expected %s" % (what, got, want)

    def add(kind, mods=None, **props):
        m = (it.modifiers if mods is None else mods).add()
        m.kind, m.uid = kind, "%s%d" % (kind.lower(), len(it.modifiers))
        for k, v in props.items():
            setattr(m, k, v)
        return m

    check((1, 1, 1), "no modifiers")
    m = add("MULTIPLIER", multiplier=0.5)
    nodes_builder.refresh(sc)
    check((0.5, 0.5, 0.5), "multiplier")
    m.factor = 0.5
    assert nodes_builder.refresh(sc) == "sync"
    check((0.75, 0.75, 0.75), "half factor")
    m.enabled = False
    assert nodes_builder.refresh(sc) == "sync"
    check((1, 1, 1), "disabled")
    it.modifiers.clear()
    for kind, props, want in (("INVERT", {}, (0, 0, 0)),
                              ("HUE_SAT", {"value": 0.5}, (0.5, 0.5, 0.5)),
                              ("BRIGHT_CONTRAST", {"bright": -0.5}, (0.5, 0.5, 0.5))):
        it.modifiers.clear()
        add(kind, **props)
        nodes_builder.refresh(sc)
        check(want, kind)
    it.modifiers.clear()
    mm = add("MATH", math_op="SUBTRACT", math_value=0.25)
    nodes_builder.refresh(sc)
    check((0.75, 0.75, 0.75), "math subtract")
    mm.math_op, mm.math_value = "ADD", 1.0
    assert nodes_builder.refresh(sc) == "sync", "math operation is a live value"
    check((2, 2, 2), "math add")
    mm.math_clamp = True
    nodes_builder.refresh(sc)
    check((1, 1, 1), "math add, clamped")
    it.modifiers.clear()
    add("MULTIPLIER", multiplier=0.5)
    add("GAMMA", gamma=2.0)
    nodes_builder.refresh(sc)
    check((0.25, 0.25, 0.25), "multiplier then gamma")
    it.modifiers.move(0, 1)
    assert nodes_builder.refresh(sc) == "rebuild"
    check((0.5, 0.5, 0.5), "gamma then multiplier")

    it.modifiers.clear()
    ramp = add("RAMP")
    nodes_builder.refresh(sc)
    g = group_of(sc)
    nodes_builder.ramp_node(g, ramp.uid).color_ramp.elements[1].color = (1, 0, 0, 1)
    check((1, 0, 0), "ramp")
    curves = add("CURVES")
    nodes_builder.refresh(sc)
    cn = nodes_builder.curves_node(group_of(sc), curves.uid)
    cn.mapping.curves[3].points[1].location = (1.0, 0.5)
    cn.mapping.update()
    check((0.5, 0, 0), "ramp then curves")
    nodes_builder.build_sky(sc)
    check((0.5, 0, 0), "ramp / curves after a rebuild")
    it.modifiers.clear()

    mk = L.masks.add()
    mk.kind, mk.angle_from, mk.angle_to = "GRADIENT", math.radians(-90), math.radians(-89)
    add("INVERT", mods=mk.modifiers)
    nodes_builder.refresh(sc)
    check((0, 0, 0), "inverted layer mask")
    mk.modifiers[0].factor = 0.5
    assert nodes_builder.refresh(sc) == "sync"
    check((0.5, 0.5, 0.5), "half-inverted layer mask")
    bpy.data.scenes.remove(sc)
    print("modifiers OK: every kind, factor, on/off, stack order, ramp/curves kept, masks")


def rgba_image(name, rgba, w=32, h=16):
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.pixels.foreach_set([c for _ in range(w * h) for c in rgba])
    return img


def image_test(pkg, tmp):
    nodes_builder = pkg.nodes_builder
    sc, shot, row = pano("img", tmp, 64, 32)
    cfg = sc.dangus
    under = cfg.layers.add()
    under.items.add().image = make_image("img_red", (1, 0, 0))
    over = cfg.layers.add()
    it = over.items.add()
    it.image = rgba_image("img_half_green", (0, 1, 0, 0.5))
    nodes_builder.build_sky(sc)
    px = shot()[16]
    assert np_allclose(px, px * 0 + (0.5, 0.5, 0)), "half transparent sky shows what is below"
    it.alpha_source = "NONE"
    assert nodes_builder.refresh(sc) == "rebuild"
    px = shot()[16]
    assert np_allclose(px, px * 0 + (0, 0.216, 0)), "opaque: transparency turns black"
    it.image = make_image("img_green", (0, 1, 0))
    it.alpha_image = make_image("img_alpha_grey", (0.5, 0.5, 0.5))
    it.alpha_source = "IMAGE"
    nodes_builder.refresh(sc)
    px = shot()[16]
    assert np_allclose(px, px * 0 + (0.786, 0.214, 0)), "alpha from another image"
    inv = it.alpha_modifiers.add()
    inv.kind = "INVERT"
    nodes_builder.refresh(sc)
    px = shot()[16]
    assert np_allclose(px, px * 0 + (0.214, 0.786, 0)), "alpha modifiers act on the whole alpha"
    it.alpha_modifiers.clear()
    it.image, it.alpha_source = make_image("img_black", (0, 0, 0)), "LUMINANCE"
    nodes_builder.refresh(sc)
    px = shot()[16]
    assert np_allclose(px, px * 0 + (1, 0, 0)), "luminance alpha: black is transparent"
    it.alpha_source = "CHANNEL"
    half = rgba_image("img_half", (0, 0, 1, 1), 32, 16)
    pix = [c for y in range(16) for x in range(32) for c in ((1, 1, 0, 1) if x < 16 else (0, 0, 1, 1))]
    half.pixels.foreach_set(pix)
    it.image = half
    nodes_builder.refresh(sc)
    a = shot()[16]
    it.map_location = (0.5, 0.0, 0.0)
    assert nodes_builder.refresh(sc) == "sync", "mapping is a live value"
    b = shot()[16]
    assert np_allclose(np_roll(a, 32), b), "a half-width shift turns the image half way"
    it.map_location = (0.0, 0.0, 0.0)
    it.map_scale = (2.0, 1.0, 1.0)
    nodes_builder.refresh(sc)
    c = shot()[16]
    assert np_allclose(c[:32], c[32:]), "scale 2 repeats the image twice"
    bpy.data.scenes.remove(sc)
    print("images OK: alpha from channel / none / image / luminance, alpha modifiers, "
          "mapping location (live) and scale")


def np_roll(a, n):
    import numpy as np
    return np.roll(a, n, axis=0)


def np_allclose(a, b):
    import numpy as np
    return np.abs(a - b).max() < 0.08 or (np.abs(a - b).max(axis=1) > 0.08).mean() < 0.1


def bake_test(pkg, tmp):
    import numpy as np
    nodes_builder, operators = pkg.nodes_builder, pkg.operators
    sc, shot, row = pano("bake", tmp, 128, 64)
    sc.cycles.samples = 16
    cfg = sc.dangus
    L = cfg.layers.add()
    for k in range(3):
        L.items.add().image = make_image("bake%d" % k, (0.2 + 0.3 * k, 0.6, 0.9 - 0.3 * k))
    nodes_builder.build_sky(sc)
    ref = shot()
    cfg.bake_path, cfg.bake_width, cfg.bake_samples = os.path.join(tmp, "baked"), 512, 16
    path, animated, frames = pkg.bake.bake(sc)
    assert path.endswith(".exr") and os.path.exists(path) and not animated, path
    assert bpy.context.scene != sc and "dangus_bake" not in bpy.data.scenes
    baked = operators.load_panorama(sc, path)
    nodes_builder.refresh(sc)
    assert [x.enabled for x in cfg.layers] == [False, True] and baked.items[0].panorama
    got = shot()
    diff = np.abs(got - ref).mean()
    assert diff < 0.02, "the loaded panorama differs from the live sky (mean %.4f)" % diff
    cfg.layers.remove(1)
    L.enabled, L.sky_spin = True, math.radians(30.0)
    sc.frame_start, sc.frame_end = 1, 3
    cfg.bake_kind, cfg.bake_format, cfg.bake_animation = "IMAGE", "OPEN_EXR", True
    cfg.bake_path, cfg.bake_width, cfg.bake_samples = os.path.join(tmp, "anim", "sky"), 256, 16
    steps = []
    path, animated, frames = pkg.bake.bake(sc, lambda d, t, label: steps.append((d, t, label)))
    files = sorted(os.listdir(os.path.join(tmp, "anim")))
    assert animated and frames == 3 and files == ["sky_0001.exr", "sky_0002.exr", "sky_0003.exr"], files
    assert [d for d, t, _l in steps] == [1, 2, 3] and steps[-1][1] == 3, steps
    seq = operators.load_panorama(sc, path, animated, frames)
    assert seq.items[0].image.source == "SEQUENCE" and seq.items[0].frame_duration == 3
    assert sc.sync_mode == "FRAME_DROP", "baked animations play in real time"
    nodes_builder.refresh(sc)
    msg = "EXR sequence"
    if bpy.app.build_options.codec_ffmpeg:
        cfg.bake_kind, cfg.bake_samples = "VIDEO", 4
        for video in [e.identifier for e in cfg.bl_rna.properties["bake_video"].enum_items]:
            cfg.bake_video, cfg.bake_width = video, 512 if video == "MKV_AV1" else 256
            cfg.bake_path = os.path.join(tmp, "video", video)
            steps.clear()
            path, animated, frames = pkg.bake.bake(sc, lambda d, t, label: steps.append(label))
            ext = pkg.bake.VIDEO[video][2]
            assert path.endswith(ext) and os.path.getsize(path) > 0 and animated, (video, path)
            assert steps[-2] == "Encoding the video..." and steps[-1] == "Done", steps
        msg += ", 6 video formats"
    bpy.data.scenes.remove(sc)
    print("bake OK: EXR still (mean diff %.4f after loading it back), %s" % (diff, msg))


def keyframe_test(pkg, tmp):
    import numpy as np
    nodes_builder = pkg.nodes_builder
    sc = bpy.context.scene
    cfg = sc.dangus
    for L in cfg.layers:
        L.enabled = False
    base = cfg.layers.add()
    base.name = "KeyBase"
    base.items.add().image = make_image("key_black", (0, 0, 0))
    S = cfg.layers.add()
    S.name, S.kind, S.blend, S.sun_mode = "KeySun", "SUN", "ADD", "TIME"
    S.items.add().kind = "SUN_COLOR"
    S.sun_latitude, S.sun_day, S.sun_size, S.sun_glow = 0.0, 81, math.radians(8.0), 0.0
    nodes_builder.build_sky(sc)
    for ob in [o for o in sc.objects if o.type == "MESH"]:
        bpy.data.objects.remove(ob)
    cam = bpy.data.objects.new("key_cam", bpy.data.cameras.new("key_cam"))
    cam.data.type, cam.data.panorama_type = "PANO", "EQUIRECTANGULAR"
    cam.rotation_euler = (math.pi / 2, 0, -math.pi / 2)
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.render.engine, sc.cycles.samples = "CYCLES", 1
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 72, 36, 100
    sc.render.image_settings.file_format = "OPEN_EXR"

    def peak_x(path=None):
        if path is None:
            path = os.path.join(tmp, "key.exr")
            bpy.ops.render.render()
            bpy.data.images["Render Result"].save_render(path)
        im = bpy.data.images.load(path, check_existing=False)
        a = np.empty(len(im.pixels), np.float32)
        im.pixels.foreach_get(a)
        w, h = im.size
        bpy.data.images.remove(im)
        px = a.reshape(h, w, 4)[..., 0]
        return np.unravel_index(px.argmax(), px.shape)[1] / w

    def cd(a, b):
        d = abs(a - b) % 1.0
        return min(d, 1.0 - d)

    index = list(cfg.layers).index(S)
    for f, hours in ((1, 9.0), (11, 15.0)):
        S.sun_hours = hours
        sc.keyframe_insert("dangus.layers[%d].sun_hours" % index, frame=f)
    for f, opacity in ((1, 1.0), (11, 0.5)):
        S.opacity = opacity
        sc.keyframe_insert("dangus.layers[%d].opacity" % index, frame=f)
    nodes_builder.refresh(sc)
    gn = sc.world.node_tree.nodes["DG_Sky"]
    sc.frame_set(1)
    x1 = peak_x()
    sc.frame_set(11)
    x11 = peak_x()
    assert abs(gn.inputs["KeySun Opacity"].default_value - 0.5) < 1e-6, "keyframed opacity"
    assert abs(cd(x11, x1) - 0.5) < 0.05, "keyframed time moves the sun: %.3f -> %.3f" % (x1, x11)
    bag = nodes_builder.channelbag(sc)

    def curve(attr):
        fc = bag.fcurves.find("dangus.layers[%d].%s" % (index, attr))
        return [(k.co[0], round(k.co[1], 4)) for k in fc.keyframe_points] if fc else []

    assert curve("sun_time") == [(1.0, 9.0), (11.0, 15.0)], "the Time slider shows the keys"
    assert nodes_builder.TIME_TRACK in sc.animation_data.nla_tracks
    sc.frame_set(6)
    assert abs(S.sun_hours - 12.0) < 1e-4 and abs(S.sun_time - 12.0) < 1e-4, (
        "between keys the slider reads the real time", S.sun_hours, S.sun_time)
    S.sun_time = 13.30
    assert abs(S.sun_hours - 13.5) < 1e-4, "an edit on the slider sets the time"
    sc.frame_set(21)
    S.sun_time = 18.45
    S.keyframe_insert("sun_time", frame=21)
    nodes_builder.mirror_animation(sc)
    assert curve("sun_hours")[-1] == (21.0, 18.75), "a key on the slider keys the time"
    S.keyframe_delete("sun_time", frame=21)
    nodes_builder.mirror_animation(sc)
    assert curve("sun_hours") == [(1.0, 9.0), (11.0, 15.0)], "deleting it on the slider too"
    assert curve("sun_time") == [(1.0, 9.0), (11.0, 15.0)]
    sc.frame_start, sc.frame_end = 1, 11
    cfg.bake_kind, cfg.bake_format, cfg.bake_animation = "IMAGE", "OPEN_EXR", True
    cfg.bake_path, cfg.bake_width, cfg.bake_samples, cfg.bake_load = os.path.join(
        tmp, "keybake", "sky"), 72, 1, False
    sc.frame_set(5)
    pkg.bake.bake(sc)
    b1 = peak_x(os.path.join(tmp, "keybake", "sky_0001.exr"))
    b11 = peak_x(os.path.join(tmp, "keybake", "sky_0011.exr"))
    assert cd(b1, x1) < 0.03 and cd(b11, x11) < 0.03, "the bake follows keyframes: %s" % (
        (b1, x1, b11, x11),)
    sc.frame_set(1)
    seq = pkg.operators.load_panorama(sc, os.path.join(tmp, "keybake", "sky_0001.exr"), True, 11)
    nodes_builder.refresh(sc)
    sc.frame_set(1)
    s1 = peak_x()
    sc.frame_set(11)
    s11 = peak_x()
    assert cd(s1, x1) < 0.03 and cd(s11, x11) < 0.03, "the baked sequence plays in step"
    assert sc.sync_mode == "FRAME_DROP"
    msg = "EXR sequence"
    if bpy.app.build_options.codec_ffmpeg:
        cfg.layers.remove(list(cfg.layers).index(seq))
        for L in cfg.layers:
            L.enabled = L.name in ("KeyBase", "KeySun")
        cfg.bake_kind, cfg.bake_video, cfg.bake_width = "VIDEO", "MP4_H264", 256
        cfg.bake_path = os.path.join(tmp, "keybake", "video")
        path, animated, frames = pkg.bake.bake(sc)
        mov = pkg.operators.load_panorama(sc, path, animated, frames)
        nodes_builder.refresh(sc)
        sc.frame_set(1)
        v1 = peak_x()
        sc.frame_set(11)
        v11 = peak_x()
        assert cd(v1, x1) < 0.03 and cd(v11, x11) < 0.03, "the baked video plays in step"
        cfg.layers.remove(list(cfg.layers).index(mov))
        msg += " and H.264 video"
    for L in cfg.layers:
        L.enabled = L.name in ("KeyBase", "KeySun")
    nodes_builder.refresh(sc)
    blend = os.path.join(tmp, "keys.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True)
    script = ("import bpy; sc = bpy.context.scene; sc.frame_set(11); "
              "print('FARM', sc.world.node_tree.nodes['DG_Sky'].inputs['KeySun Time'].default_value)")
    proc = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", blend,
                           "--python-expr", script], capture_output=True, text=True, timeout=300)
    assert "FARM 15.0" in proc.stdout, "keyframes play without the extension: " + proc.stdout[-500:] + proc.stderr[-800:]
    sc.animation_data_clear()
    for name in ("KeySun", "KeyBase", "Baked Sky"):
        while name in cfg.layers:
            cfg.layers.remove(cfg.layers.find(name))
    print("keyframes OK: time of day and opacity animate (frames, render, bake, without the "
          "extension); baked %s play in step with the scene's frames" % msg)


def group_of(scene):
    return group(scene)


def animation_paths_test(pkg):
    nb, ops = pkg.nodes_builder, pkg.operators
    sc = bpy.data.scenes.new("anim_paths")
    sc.world = bpy.data.worlds.new("anim_paths")
    cfg = sc.dangus
    with bpy.context.temp_override(scene=sc):
        sky = ops._new_layer(sc, "SKY")
        sky.items.add().image = make_image("anim_sky", (0.2, 0.2, 0.2))
        sun = ops._new_layer(sc, "SUN")
        sun.items.add().kind = "SUN_COLOR"
        sun.sun_mode = "TIME"
        nb.build_sky(sc)
        for f, v in ((1, 0.2), (30, 0.9)):
            sky.opacity = v
            sc.keyframe_insert("dangus.layers[0].opacity", frame=f)
        for f, t in ((1, 8.0), (20, 10.0), (40, 12.0), (60, 14.0)):
            sun.sun_hours = t
            sc.keyframe_insert("dangus.layers[1].sun_hours", frame=f)
        nb.mirror_animation(sc)
        bag = nb.channelbag(sc)

        def curve(path):
            fc = bag.fcurves.find(path)
            return [(k.co[0], round(k.co[1], 4)) for k in fc.keyframe_points] if fc else []

        shown = bag.fcurves.find("dangus.layers[1].sun_time")
        for k in [k for k in shown.keyframe_points if k.co[0] in (20, 40)][::-1]:
            shown.keyframe_points.remove(k)
        nb.mirror_animation(sc)
        assert curve("dangus.layers[1].sun_hours") == [(1.0, 8.0), (60.0, 14.0)], (
            "deleting two slider keys deletes exactly those", curve("dangus.layers[1].sun_hours"))
        sc.frame_set(60)
        sun.sun_time = 15.30
        sun.keyframe_insert("sun_time", frame=60)
        nb.mirror_animation(sc)
        assert curve("dangus.layers[1].sun_hours") == [(1.0, 8.0), (60.0, 15.5)], "re-keying a key"
        cfg.active_layer = 1
        assert bpy.ops.dangus.move_layer(direction="UP") == {"FINISHED"}
        assert cfg.layers[0] == sun or cfg.layers[0].kind == "SUN"
        assert curve("dangus.layers[0].sun_hours") == [(1.0, 8.0), (60.0, 15.5)], "keys follow a move"
        assert curve("dangus.layers[1].opacity") == [(1.0, 0.2), (30.0, 0.9)]
        cfg.active_layer = 0
        assert bpy.ops.dangus.remove_layer() == {"FINISHED"}
        assert curve("dangus.layers[0].opacity") == [(1.0, 0.2), (30.0, 0.9)], "keys follow a removal"
        assert not [fc for fc in bag.fcurves if "sun_" in fc.data_path], "the removed layer's keys go"
        assert bpy.ops.dangus.duplicate_layer() == {"FINISHED"}
        assert curve("dangus.layers[0].opacity") and not curve("dangus.layers[1].opacity")
        cfg.layers[1].name = cfg.layers[0].name
        assert cfg.layers[1].name != cfg.layers[0].name, "layer names stay unique"
    bpy.data.scenes.remove(sc)
    print("animation paths OK: keys follow layer move / remove / duplicate, slider key sync, unique names")


def main():
    pkg = enable()
    live, nodes_builder = pkg.live, pkg.nodes_builder
    sc = bpy.context.scene
    cfg = sc.dangus
    tree = sc.world.node_tree
    user_node = tree.nodes.new("ShaderNodeRGB")
    user_node.name = "MyNode"
    tmp = tempfile.mkdtemp(prefix="dangus_test_")
    skies = [write_png(os.path.join(tmp, "sky_%d.png" % n),
                       ((n % 3) / 2, (n % 5) / 4, n % 2)) for n in range(4)]
    clouds = [write_png(os.path.join(tmp, "cloud_%d.png" % n), (1, 1, 1), alpha=True)
              for n in range(3)]

    bpy.ops.dangus.add_layer()
    try:
        bpy.ops.dangus.build()
        raise AssertionError("build with no images should fail")
    except RuntimeError as e:
        assert "at least one image" in str(e), e

    L1 = cfg.layers[0]
    add_images(tmp, skies, "SKY")
    add_images(tmp, clouds[:2], "DECAL")
    assert [it.kind for it in L1.items] == ["SKY"] * 4 + ["DECAL"] * 2
    d1, d2 = L1.items[4], L1.items[5]
    assert d1.decal_object and d2.decal_object, "adding a cloud must create its Empty"
    assert d1.decal_object.rotation_euler != d2.decal_object.rotation_euler
    L1.active_item = 0
    bpy.ops.dangus.add_mask(target="ITEM", kind="NOISE")
    L1.items[0].masks[0].low, L1.items[0].masks[0].high = 0.3, 0.7
    d1.blend = "ADD"
    d1.masks.add().kind = "VORONOI"
    L1.sky_spin = math.radians(0.5)
    bpy.ops.dangus.add_mask(target="LAYER", kind="GRADIENT")

    bpy.ops.dangus.add_layer()
    L2 = cfg.layers[1]
    add_images(tmp, clouds[2:], "DECAL")
    d3 = L2.items[0]
    L2.opacity, L2.blend = 0.6, "SCREEN"
    m = L2.masks.add()
    m.kind, m.image = "IMAGE", make_image("layer_mask", (0.5, 0.5, 0.5))

    t = time.perf_counter()
    bpy.ops.dangus.build()
    t_build = time.perf_counter() - t
    check_world(tree)
    assert "MyNode" in tree.nodes
    g = group()
    check_connected(g)
    nodes1, drivers1 = len(g.nodes), len(g.drivers)
    assert drivers1 == 3 * 8 + 1, "expected 8 drivers per cloud + 1 spin, got %d" % drivers1
    assert d1.decal_object.name == "cloud_0_cloud", d1.decal_object.name
    for d, L in ((d1, L1), (d2, L1), (d3, L2)):
        ob = d.decal_object
        assert [c.name for c in ob.users_collection] == ["%s_cloud" % L.name]
        assert ob.users_collection[0].name in bpy.data.collections["dangus_Scene"].children
        assert ob.users_collection[0].color_tag == "COLOR_05", "layer collection must be blue"
        assert abs(ob.location.length - pkg.decals.DOME_RADIUS) < 1e-4
        assert ob.empty_display_type == "IMAGE" and ob.data == d.image
    root = sc.collection.children["dangus_%s" % sc.name]
    assert root.color_tag == "COLOR_05", "root collection must be blue"
    assert [L.name for L in cfg.layers] == ["Scene", "Scene.001"], "layers named after the scene"

    ob = d1.decal_object
    assert abs(d1.cloud_scale - 1.0) < 1e-6
    d1.cloud_scale = 1.5
    assert abs(ob.scale.x - 1.5 * 0.5 * pkg.decals.DOME_RADIUS) < 1e-4 and ob.scale.x == ob.scale.y
    ob.location *= 2.0
    assert abs(d1.cloud_scale - 0.75) < 1e-6
    ob.location /= 2.0
    d1.cloud_scale = 1.0
    assert count(g, "DG_SkyImg") == 4 and count(g, "DG_CloudImg") == 3
    names1 = sorted(n.name for n in g.nodes)
    print("built: %d nodes, %d drivers in %.1f ms" % (nodes1, drivers1, t_build * 1000))
    names = {t.name for t in g.trees}
    assert {"dangus Direction", "dangus Cloud Plane"} <= names, names
    assert L1.tree and L2.tree and L1.tree != L2.tree and {L1.tree, L2.tree} <= set(g.trees)
    for t in (g.top, L1.tree, L2.tree):
        frames = [n for n in t.nodes if n.bl_idname == "NodeFrame"]
        assert frames and all(f.label for f in frames), t.name
        loose = [n.name for n in t.nodes if n.parent is None and n.bl_idname not in
                 ("NodeFrame", "NodeGroupInput", "NodeGroupOutput")]
        assert not loose, (t.name, loose)
    assert sc.world.cycles.sampling_method == "MANUAL" and sc.world.cycles.sample_map_resolution == 1024
    cfg.light_map = "AUTO"
    assert nodes_builder.refresh(sc) == "sync" and sc.world.cycles.sampling_method == "AUTOMATIC"
    cfg.light_map = "2048"
    nodes_builder.refresh(sc)
    assert sc.world.cycles.sample_map_resolution == 2048
    cfg.light_map = "1024"
    nodes_builder.refresh(sc)
    print("subgroups OK: %s" % sorted(names))

    bpy.ops.dangus.build()
    g = group()
    assert sorted(n.name for n in g.nodes) == names1 and len(g.links) > 0
    check_world(tree)
    assert "MyNode" in tree.nodes

    tag(g)
    d1.opacity, d1.softness, d1.blend, d1.tint = 0.3, 0.5, "SCREEN", (1.0, 0.8, 0.6)
    d2.blend = "MULTIPLY"
    L1.items[0].blend_width = 0.5
    L1.sky_spin = math.radians(1.0)
    L1.opacity, L1.blend, L1.name = 0.7, "OVERLAY", "Renamed"
    L1.items[0].masks[0].intensity = 0.5
    cfg.noise_scale, cfg.edge_softness, cfg.strength, cfg.sky_rotation = 7.0, 0.8, 2.0, 0.3
    cfg.edge_detail, cfg.edge_low, cfg.edge_high = 4.0, 0.25, 0.75
    cfg.sky_bottom = math.radians(-20)
    t = time.perf_counter()
    live._run(sc.name)
    t_sync = time.perf_counter() - t
    g = group()
    assert survived(g), "value changes caused a rebuild instead of a live sync"
    assert len(g.nodes) == nodes1 and len(g.drivers) == drivers1
    modes = {n.blend_type for n in g.nodes if n.bl_idname == "ShaderNodeMix"}
    assert {"SCREEN", "MULTIPLY", "OVERLAY"} <= modes, modes
    assert abs(tree.nodes["DG_Background"].inputs["Strength"].default_value - 2.0) < 1e-6
    assert abs(tree.nodes["DG_Sky"].inputs[0].default_value - 0.7) < 1e-6
    socks = [s for s in g.interface.items_tree if s.item_type == "SOCKET" and s.in_out == "INPUT"]
    assert socks[0].name == "Renamed Opacity", socks[0].name
    noise = next(n for n in g.nodes if n.name.startswith("DG_SeamNoise"))
    assert abs(noise.inputs["Scale"].default_value - 7.0) < 1e-6
    spin = [fc.driver.expression for fc in g.drivers if "frame" in fc.driver.expression]
    assert len(spin) == 1 and "%.8f" % (math.radians(1.0) * 4 / (2 * math.pi)) in spin[0], spin
    tints = [tuple(round(x, 3) for x in n.inputs[1].default_value) for n in g.nodes
             if n.name.startswith("DG_Tint")]
    assert (1.0, 0.8, 0.6) in tints, tints
    assert abs(noise.inputs["Detail"].default_value - 4.0) < 1e-6
    print("live sync in %.1f ms" % (t_sync * 1000))

    L2.name = "Storm"
    assert nodes_builder.refresh(sc) == "sync"
    assert [c.name for c in d3.decal_object.users_collection] == ["Storm_cloud"]

    presets = pkg.presets
    for kind, node in (("VORONOI", "DG_SeamVoronoi"), ("WAVE", "DG_SeamWave"),
                       ("MAGIC", "DG_SeamMagic"), ("GABOR", "DG_SeamGabor"),
                       ("NOISE", "DG_SeamNoise")):
        cfg.edge_noise = kind
        assert nodes_builder.refresh(sc) == "rebuild"
        assert count(group(), node) == 1, kind
        check_connected(group())
    for name in presets.BUILTIN:
        assert bpy.ops.dangus.edge_preset_apply(name=name) == {"FINISHED"}
        nodes_builder.refresh(sc)
        check_connected(group())
    assert cfg.edge_noise == "NOISE" and abs(cfg.edge_low - 0.49) < 1e-6
    cfg.noise_scale = 5.5
    pname = "zz test preset %d" % os.getpid()
    presets.save(cfg, pname)
    cfg.noise_scale = 1.0
    assert pname in presets.user_presets() and presets.apply(cfg, pname)
    assert abs(cfg.noise_scale - 5.5) < 1e-6
    assert presets.remove(pname) and pname not in presets.user_presets()
    bpy.ops.dangus.edge_preset_apply(name="Soft Clouds")

    tag(group())
    m = d1.masks.add()
    m.kind = "NOISE"
    assert nodes_builder.refresh(sc) == "rebuild"
    g = group()
    assert not survived(g), "adding a mask should rebuild"
    assert not any(n.get("probe") for n in L1.tree.nodes), "the edited layer must rebuild"
    assert any(n.get("probe") for n in L2.tree.nodes), "other layers must be left alone"
    assert any(n.get("probe") for n in g.top.nodes), "the top group must be left alone"
    check_world(tree)
    check_connected(g)
    tag(g)
    assert nodes_builder.refresh(sc) == "sync"
    assert survived(group())

    cfg.vertical_mode, cfg.sky_horizon = "ASPECT", 0.1
    for mode in ("SCREEN", "MULTIPLY", "OVERLAY", "SOFT_LIGHT", "LIGHTEN", "DARKEN", "MIX"):
        d1.blend = mode
        L1.blend = mode
        bpy.ops.dangus.refresh()
        check_connected(group())
    L1.blend, d1.blend = "MIX", "ADD"
    for kind in ("IMAGE", "NOISE", "VORONOI", "GRADIENT"):
        for owner in (L1.items[1], d2, L2):
            mk = owner.masks.add()
            mk.kind, mk.invert = kind, True
            if kind == "IMAGE":
                mk.image = make_image("m_" + kind, (0.3, 0.6, 0.9))
    assert nodes_builder.refresh(sc) == "rebuild"
    check_connected(group())

    d2.decal_object.rotation_euler[2] += 0.5
    d2.decal_object.scale = (0.3, 0.2, 0.3)
    sc.frame_set(2)

    cfg.cull_camera = True
    cfg.cull_margin = math.radians(10)
    _, stats = nodes_builder.build_sky(sc)
    assert stats["culled"] == 0 and "spins" in (stats["note"] or ""), stats
    L1.sky_spin = 0.0
    _, stats = nodes_builder.build_sky(sc)
    g = group()
    check_connected(g)
    n_tex = count(g, "DG_SkyImg")
    print("culling: %d of 4 sky images textured" % n_tex)
    assert 1 <= n_tex < 4 and stats["culled"] == 4 - n_tex, "camera culling removed nothing"
    assert count(g, "DG_CloudImg") == 3, "clouds must never be culled"
    tag(g)
    d1.opacity = 0.9
    assert nodes_builder.refresh(sc) == "sync"
    assert survived(group()) and count(group(), "DG_SkyImg") == n_tex
    cfg.cull_camera = False
    L1.sky_spin = math.radians(2.0)

    sky3 = L1.items[3]
    cfg.active_layer, L1.active_item = 0, 3
    assert bpy.ops.dangus.set_kind(kind="DECAL") == {"FINISHED"}
    assert nodes_builder.refresh(sc) == "rebuild" and sky3.decal_object is not None
    ob_name = sky3.decal_object.name
    assert bpy.ops.dangus.set_kind(kind="SKY") == {"FINISHED"}
    assert ob_name not in bpy.data.objects and sky3.decal_object is None
    bpy.ops.dangus.set_kind(kind="DECAL")
    ob_name = sky3.decal_object.name
    bpy.ops.dangus.remove_image()
    assert ob_name not in bpy.data.objects, "removing a cloud must delete its Empty"
    nodes_builder.refresh(sc)
    check_connected(group())

    sky0 = L1.items[0]
    tag(group())
    sky0.tex_interpolation, sky0.tex_extension, sky0.tex_projection = "Closest", "MIRROR", "FLAT"
    sky0.frame_offset, sky0.use_cyclic = 3, True
    assert nodes_builder.refresh(sc) == "sync" and survived(group())
    img_node = next(n for n in group().nodes if n.bl_idname == "ShaderNodeTexImage"
                    and n.image == sky0.image and n.name.startswith("DG_SkyImg"))
    assert (img_node.interpolation, img_node.extension) == ("Closest", "MIRROR")
    assert img_node.image_user.frame_offset == 3 and img_node.image_user.use_cyclic
    sky0.tex_interpolation, sky0.tex_extension = "Linear", "AUTO"
    nodes_builder.refresh(sc)
    assert img_node.extension == "EXTEND", "Auto must give a sky ring Extend"
    cloud_node = next(n for n in group().nodes if n.name.startswith("DG_CloudImg"))
    assert cloud_node.extension == "CLIP", "Auto must give a cloud Clip"
    sky0.frame_offset, sky0.use_cyclic = 0, False

    cfg.active_layer = 0
    n_items = len(L1.items)
    assert bpy.ops.dangus.new_image(kind="DECAL", name="Painted", width=64, height=32,
                                    alpha=True, color=(1, 1, 1, 0)) == {"FINISHED"}
    new = L1.items[L1.active_item]
    assert len(L1.items) == n_items + 1 and new.kind == "DECAL" and new.decal_object
    assert new.image.source == "GENERATED" and tuple(new.image.size) == (64, 32)
    assert bpy.ops.dangus.new_image(kind="SKY", name="PaintedSky", width=128, height=64) == {"FINISHED"}
    assert L1.items[L1.active_item].kind == "SKY"
    for _ in range(2):
        bpy.ops.dangus.remove_image()
    nodes_builder.refresh(sc)
    check_connected(group())

    d1.alpha_source = "LUMINANCE"
    assert nodes_builder.refresh(sc) == "rebuild"
    assert nodes_builder.ramp_node(group(), d1.uid) is None, "the luminance ramp is gone"
    lum = d1.modifiers.add()
    lum.kind, lum.uid = "RAMP", "lumramp"
    nodes_builder.refresh(sc)
    ramp = nodes_builder.ramp_node(group(), lum.uid)
    ramp.color_ramp.elements[0].position = 0.4
    ramp.color_ramp.elements.new(0.7).color = (0.5, 0.5, 0.5, 1.0)
    nodes_builder.build_sky(sc)
    ramp = nodes_builder.ramp_node(group(), lum.uid)
    assert len(ramp.color_ramp.elements) == 3 and abs(ramp.color_ramp.elements[0].position - 0.4) < 1e-6
    check_connected(group())

    rm = d1.modifiers.add()
    rm.kind, rm.uid = "RAMP", "dupramp"
    nodes_builder.refresh(sc)
    nodes_builder.ramp_node(group(), rm.uid).color_ramp.elements[0].position = 0.3
    L1.active_item = list(L1.items).index(d1)
    bpy.ops.dangus.duplicate_image()
    dup_mod = L1.items[L1.active_item].modifiers[-1]
    assert dup_mod.uid and dup_mod.uid != rm.uid
    nodes_builder.refresh(sc)
    assert abs(nodes_builder.ramp_node(group(), dup_mod.uid).color_ramp.elements[0].position
               - 0.3) < 1e-6, "duplicated modifier ramp lost its edit"
    bpy.ops.dangus.remove_image()
    d1.modifiers.clear()
    nodes_builder.refresh(sc)

    L1.active_item = list(L1.items).index(d1)
    n_items = len(L1.items)
    assert bpy.ops.dangus.duplicate_image() == {"FINISHED"}
    dup = L1.items[L1.active_item]
    assert len(L1.items) == n_items + 1 and dup.uid != d1.uid and dup.kind == "DECAL"
    assert dup.decal_object not in (None, d1.decal_object)
    assert dup.decal_object.matrix_world == d1.decal_object.matrix_world
    assert len(dup.masks) == len(d1.masks) and dup.blend == d1.blend
    nodes_builder.refresh(sc)
    assert len(dup.modifiers) == len(d1.modifiers)
    bpy.ops.dangus.remove_image()
    n_layers = len(cfg.layers)
    cfg.active_layer = 1
    assert bpy.ops.dangus.duplicate_layer() == {"FINISHED"}
    L3 = cfg.layers[2]
    assert len(cfg.layers) == n_layers + 1 and L3.name == "Storm.001" and cfg.active_layer == 2
    assert L3.items[0].decal_object is not L2.items[0].decal_object
    nodes_builder.refresh(sc)
    assert [c.name for c in L3.items[0].decal_object.users_collection] == ["Storm.001_cloud"]
    assert bpy.data.collections["Storm.001_cloud"].color_tag == "COLOR_05"
    check_connected(group())
    assert bpy.ops.dangus.remove_layer() == {"FINISHED"}
    assert "Storm.001_cloud" not in bpy.data.collections, "layer collection must go with it"
    nodes_builder.refresh(sc)

    render("CYCLES", tmp)
    render(eevee_id(), tmp)

    setup_render(sc, "CYCLES")
    sc.frame_set(37)
    ref = os.path.join(tmp, "with.png")
    mean_with = render_mean(ref)
    for img in bpy.data.images:
        if img.source in {"FILE", "GENERATED"} and img.users and not img.packed_file:
            img.pack()
    blend = os.path.join(tmp, "farm.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True)
    out = os.path.join(tmp, "without.png")
    script = ("import bpy,sys; assert not hasattr(bpy.types.Scene,'dangus'); "
              "bpy.context.scene.frame_set(37); bpy.ops.render.render(); "
              "bpy.data.images['Render Result'].save_render(%r)" % out)
    proc = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", blend,
                           "--python-exit-code", "1", "--python-expr", script],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0 and os.path.exists(out), proc.stdout[-2000:] + proc.stderr[-2000:]
    a = bpy.data.images.load(ref, check_existing=False)
    b = bpy.data.images.load(out, check_existing=False)
    diff = max(abs(x - y) for x, y in zip(a.pixels[:], b.pixels[:]))
    assert diff < 0.02, "render without the extension differs (max diff %.4f)" % diff
    print("render without extension OK (mean %.3f, max diff %.4f)" % (mean_with, diff))

    bpy.ops.dangus.clear()
    assert not any(n.name.startswith("DG_") for n in tree.nodes)
    assert "MyNode" in tree.nodes
    assert not [x for x in bpy.data.node_groups if x.name.startswith("dangus")]
    n_empties = sum(1 for o in bpy.data.objects if o.get("dangus_decal"))
    bpy.ops.dangus.build()
    assert sum(1 for o in bpy.data.objects if o.get("dangus_decal")) == n_empties == 3

    cloud_geometry_test(pkg, tmp)
    fog_test(pkg, tmp)
    sun_test(pkg, tmp)
    modifiers_test(pkg, tmp)
    image_test(pkg, tmp)
    bake_test(pkg, tmp)
    keyframe_test(pkg, tmp)
    animation_paths_test(pkg)
    print("DANGUS SMOKE TEST PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
