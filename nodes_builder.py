import hashlib
import math
import re
from contextlib import contextmanager

import bpy

from . import culling, decals
from .utils import BLEND_OCCLUSION, PREFIX, SUN_KINDS, scene_fps, sk

TOP_NAME = "dangus"
SKY_NODE = PREFIX + "Sky"
TWO_PI = 2.0 * math.pi
VERSION = 1
FLAG = "dangus"
TAG = "dangus_uid"
UI_ONLY = {"rna_type", "show_expanded", "map_scale_lock", "active_item", "active_mask", "active_layer",
           "sun_active_mask", "tree", "collection"}
BAKE_SETTINGS = ("bake_path", "bake_format", "bake_width", "bake_samples", "bake_animation",
                 "bake_load")
AVERAGES = {}
LIGHT_MAP = {"AUTO": None, "512": 512, "1024": 1024, "2048": 2048, "4096": 4096}
SYNC_ERRORS = (KeyError, IndexError, RuntimeError, TypeError, AttributeError)


READABLE = {
    "Abs": "", "Contrib": "Weight", "DayTint": "Daylight tint", "Flat": "", "FogTint": "Fog tint",
    "Len": "Distance", "MaskMap": "Mask levels", "Mix": "", "MixF": "", "Math": "", "Vec": "",
    "Normalize": "", "Range": "", "In": "", "Out": "", "Frame": "", "Q": "Image coordinates",
    "CloudEdge": "Cloud edge", "Plane": "Cloud plane", "Ray": "View ray", "Relit": "Relit sky",
    "ModMath": "Modifier: Math", "ModMultiply": "Modifier: Multiply",
    "ModBrightContrast": "Modifier: Brightness / Contrast", "ModGamma": "Modifier: Gamma",
    "ModRamp": "Modifier: Color Ramp", "SunImgFit": "Fit the image to the disc",
    "SunImgGlow": "Image glow", "SunImgLight": "Image light", "SunImgTint": "Image tint",
    "SunLight": "Sunlight", "SunPlane": "Sun image plane", "SunTransmit": "Through the air",
    "SunsetSun": "Sunset colour", "SunDisc": "Sun disc", "UV": "Image UV",
    "Unpremult": "Undo premultiplied alpha", "DirSep": "Split the direction",
    "DirZ": "Direction depth", "OriginZ": "Origin depth", "FogUV": "Fog image UV",
    "QSep": "Split image coordinates", "SunColor": "Sun colour", "SunLocal": "Sun-space direction",
    "TexMap": "Mapping", "SunMix": "", "Lum": "Luminance", "ModMix": "Modifier mix",
    "FogFade": "Fog fade", "MaskNoise": "Mask: Noise", "MaskCells": "Mask: Cells",
    "SunLayer": "Sun layer", "Edge": "Edge", "Coverage": "Coverage", "EdgeSep": "Split the offset",
    "FromCentre": "Offset from the centre", "Feather": "Feather the edges (Softness)",
    "GlowReach": "Along this direction", "GlowImg": "Glow sample", "GlowSum": "Add up the glow",
    "SunImgGlowTint": "Glow colour", "GlowSide": "Sideways", "GlowRay": "Glow direction",
    "SunPlaneSep": "Split the sun plane",
}


GLOW_TURNS = (-0.6, -0.3, 0.0, 0.3, 0.6)
GLOW_STEPS = 3
GLOW_SAMPLES = len(GLOW_TURNS) * GLOW_STEPS


class Mismatch(Exception):
    pass


def _set(obj, attr, value):
    cur = getattr(obj, attr)
    if isinstance(cur, float):
        same = abs(cur - value) < 1e-9
    elif hasattr(cur, "__len__") and not isinstance(cur, str):
        same = len(cur) == len(value) and all(abs(a - b) < 1e-9 for a, b in zip(cur, value))
    else:
        same = cur == value
    if not same:
        setattr(obj, attr, value)


def effective(masks):
    return [m for m in masks if m.enabled and not (m.kind == "IMAGE" and m.image is None)]


def skies(L):
    return [] if L.kind == "SUN" else [it for it in L.items if it.kind == "SKY" and it.image]


def overlays(L):
    if L.kind == "SUN":
        return []
    return [it for it in L.items if it.kind == "FOG" or (
        it.kind == "DECAL" and it.image is not None and it.decal_object is not None)]


def sun_items(L):
    if L.kind != "SUN":
        return []
    return [it for it in L.items
            if it.kind in SUN_KINDS and (it.kind != "SUN_IMAGE" or it.image is not None)]


def layers(cfg):
    return [L for L in cfg.layers if L.enabled and (L.kind == "SUN" or skies(L) or overlays(L))]


def alpha_source(it):
    return "NONE" if it.alpha_source == "IMAGE" and it.alpha_image is None else it.alpha_source


def _asig(it):
    return alpha_source(it), _modsig(it.alpha_modifiers)


def fog_kind(it):
    return "TEXTURE" if it.fog_mode == "TEXTURE" and it.image is not None else "GRADIENT"


def _modsig(mods):
    return tuple((x.kind, x.uid if x.kind in ("RAMP", "CURVES") else "") for x in mods)


def _msig(masks):
    return tuple((m.kind, _modsig(m.modifiers)) for m in effective(masks))


def _hash(obj):
    return hashlib.md5(repr(obj).encode()).hexdigest()


def layer_signature(cfg, L):
    sig = [VERSION, L.kind, _msig(L.masks)]
    if L.kind == "SUN":
        sig += [L.sun_mode, L.sun_relight_sky, L.sun_relight_disc,
                tuple((it.kind, _modsig(it.modifiers), _asig(it)) for it in sun_items(L))]
        return _hash(sig)
    sk_ = skies(L)
    sig += [len(sk_), L.sky_spin != 0.0, cfg.vertical_mode, len(sk_) > 1 and cfg.edge_noise,
            cfg.cull_camera and (round(cfg.cull_margin, 4), cfg.cull_animation),
            len(sk_) == 1 and sk_[0].panorama]
    sig += [(_msig(s.masks), _modsig(s.modifiers), _asig(s)) for s in sk_]
    for d in overlays(L):
        if d.kind == "FOG":
            sig.append(("F", fog_kind(d), _msig(d.masks), _modsig(d.modifiers), _asig(d)))
        else:
            sig.append(("D", d.decal_object.session_uid, d.uid, _msig(d.masks),
                        _modsig(d.modifiers), _asig(d)))
    return _hash(sig)


def _values(pg, out, skip=()):
    for p in pg.bl_rna.properties:
        k = p.identifier
        if k in UI_ONLY or k in skip:
            continue
        v = getattr(pg, k)
        if p.type == "COLLECTION":
            for x in v:
                _values(x, out)
        elif p.type == "POINTER":
            if isinstance(v, bpy.types.PropertyGroup):
                _values(v, out)
            else:
                out.append(None if v is None else (v.session_uid, tuple(getattr(v, "size", ()))))
        else:
            out.append(tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v)
    return out


def global_values(scene):
    return _values(scene.dangus, [scene_fps(scene)], skip=("layers",) + BAKE_SETTINGS)


def tagged_node(tree, uid, idname):
    if tree is None or not uid:
        return None
    return next((n for n in tree.nodes if n.bl_idname == idname and n.get(TAG) == uid), None)


def ramp_node(tree, uid):
    return tagged_node(tree, uid, "ShaderNodeValToRGB")


def curves_node(tree, uid):
    return tagged_node(tree, uid, "ShaderNodeRGBCurve")


def dangus_groups():
    return [g for g in bpy.data.node_groups if g.get(FLAG)]


def snapshot_ramps(scene):
    out = {}
    for g in dangus_groups():
        for n in g.nodes:
            uid = n.get(TAG)
            if not uid:
                continue
            if n.bl_idname == "ShaderNodeValToRGB":
                cr = n.color_ramp
                out[uid] = {"interp": cr.interpolation, "mode": cr.color_mode,
                            "hue": cr.hue_interpolation,
                            "stops": [(e.position, tuple(e.color)) for e in cr.elements]}
            elif n.bl_idname == "ShaderNodeRGBCurve":
                m = n.mapping
                out[uid] = {"clip": m.use_clip, "black": tuple(m.black_level),
                            "white": tuple(m.white_level),
                            "curves": [[(tuple(p.location), p.handle_type) for p in c.points]
                                       for c in m.curves]}
    for dst, src in dict(scene.get("dangus_alias", {})).items():
        if dst not in out and src in out:
            out[dst] = out[src]
    return out


def restore(node, data):
    if not data:
        return
    if "stops" in data and node.bl_idname == "ShaderNodeValToRGB":
        cr = node.color_ramp
        cr.color_mode, cr.interpolation, cr.hue_interpolation = data["mode"], data["interp"], data["hue"]
        while len(cr.elements) < len(data["stops"]):
            cr.elements.new(0.5)
        while len(cr.elements) > len(data["stops"]):
            cr.elements.remove(cr.elements[-1])
        for e, (pos, col) in zip(cr.elements, data["stops"]):
            e.position, e.color = pos, col
    elif "curves" in data and node.bl_idname == "ShaderNodeRGBCurve":
        m = node.mapping
        m.use_clip, m.black_level, m.white_level = data["clip"], data["black"], data["white"]
        for c, pts in zip(m.curves, data["curves"]):
            while len(c.points) < len(pts):
                c.points.new(0.5, 0.5)
            while len(c.points) > len(pts):
                c.points.remove(c.points[-1])
            for p, (loc, handle) in zip(c.points, pts):
                p.location, p.handle_type = loc, handle
        m.update()


def alias_ramp(scene, dst_uid, src_uid):
    alias = dict(scene.get("dangus_alias", {}))
    alias[dst_uid] = src_uid
    scene["dangus_alias"] = alias


class Tree:
    def __init__(self, scene, g, sync=False, ramps=None, culled=()):
        self.scene, self.cfg, self.g, self.sync = scene, scene.dangus, g, sync
        self.fps = scene_fps(scene)
        self.ramps = ramps or {}
        self.n = 0
        self.cache = {}
        self.frame = None
        self.culled_set, self.culled_keys = set(culled), []
        self.vis = self.note = None
        self.iface = False
        self.ring_images = self.culled = 0

    def node(self, idname, name, label=None):
        self.n += 1
        nm = "%s%s_%d" % (PREFIX, name, self.n)
        if self.sync:
            n = self.g.nodes.get(nm)
            if n is None or n.bl_idname != idname:
                raise Mismatch()
            if label and idname == "NodeFrame":
                _set(n, "label", label)
            return n
        n = self.g.nodes.new(idname)
        n.name, n.label = nm, label or READABLE.get(name, name)
        if self.frame is not None and idname != "NodeFrame":
            n.parent = self.frame
        return n

    @contextmanager
    def section(self, label):
        outer = self.frame
        self.frame = self.node("NodeFrame", "Frame", label)
        if not self.sync:
            self.frame.label_size, self.frame.shrink = 20, True
        yield
        self.frame = outer

    def put(self, sock, value):
        if value is None:
            return
        if isinstance(value, bpy.types.NodeSocket):
            if not self.sync:
                self.g.links.new(value, sock)
        else:
            _set(sock, "default_value", value)

    def M(self, op, a, b=0.0, c=None, clamp=False, label=None):
        n = self.node("ShaderNodeMath", "Math", label)
        _set(n, "operation", op)
        _set(n, "use_clamp", clamp)
        for s, v in zip(n.inputs, (a, b, c)):
            self.put(s, v)
        return n.outputs[0]

    def V(self, op, a, b=None, c=None, name="Vec", out="Vector"):
        n = self.node("ShaderNodeVectorMath", name)
        _set(n, "operation", op)
        self.put(n.inputs[0], a)
        if op == "SCALE":
            self.put(sk(n.inputs, "Scale"), b)
        else:
            self.put(n.inputs[1], b)
            self.put(n.inputs[2], c)
        return n.outputs[out]

    def MR(self, x, f0, f1, t0, t1, interp="LINEAR", name="Range"):
        n = self.node("ShaderNodeMapRange", name)
        if not self.sync:
            n.data_type, n.clamp = "FLOAT", True
        _set(n, "interpolation_type", interp)
        for s, v in zip(n.inputs, (x, f0, f1, t0, t1)):
            self.put(s, v)
        return n.outputs["Result"]

    def MIX(self, fac, a, b, blend="MIX", name="Mix"):
        n = self.node("ShaderNodeMix", name)
        _set(n, "data_type", "RGBA")
        _set(n, "blend_type", blend)
        self.put(sk(n.inputs, "Factor_Float"), fac)
        self.put(sk(n.inputs, "A_Color"), a)
        self.put(sk(n.inputs, "B_Color"), b)
        return sk(n.outputs, "Result_Color")

    def mixf(self, fac, a, b, name="MixF"):
        n = self.node("ShaderNodeMix", name)
        if not self.sync:
            n.data_type = "FLOAT"
        for key, v in (("Factor_Float", fac), ("A_Float", a), ("B_Float", b)):
            self.put(sk(n.inputs, key), v)
        return sk(n.outputs, "Result_Float")

    def group(self, tree, name):
        n = self.node("ShaderNodeGroup", name, tree.name)
        _set(n, "node_tree", tree)
        _set(n, "label", tree.name)
        return n

    def drive(self, sock, index, expr, ob=None, channel=None):
        path = sock.path_from_id("default_value")
        if self.sync:
            ad = self.g.animation_data
            fc = ad.drivers.find(path, index=index or 0) if ad else None
            if fc is None:
                raise Mismatch()
            if fc.driver.expression != expr:
                fc.driver.expression = expr
            return
        fc = sock.driver_add("default_value") if index is None else sock.driver_add(
            "default_value", index)
        for m in list(fc.modifiers):
            fc.modifiers.remove(m)
        fc.driver.type, fc.driver.expression = "SCRIPTED", expr
        if ob is not None:
            var = fc.driver.variables.new()
            var.name, var.type = "v", "TRANSFORMS"
            t = var.targets[0]
            t.id, t.transform_type = ob, channel
            t.transform_space, t.rotation_mode = "WORLD_SPACE", "XYZ"

    def lazy(self, key, fn):
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]

    @property
    def softness(self):
        return max(self.cfg.edge_softness, 0.05)

    def seam_noise(self):
        def make():
            cfg = self.cfg
            k = cfg.edge_noise
            spec = {"VORONOI": ("ShaderNodeTexVoronoi", "Distance",
                                (("Detail", cfg.edge_detail), ("Roughness", cfg.edge_roughness),
                                 ("Randomness", cfg.edge_randomness))),
                    "WAVE": ("ShaderNodeTexWave", "Fac",
                             (("Distortion", cfg.edge_distortion), ("Detail", cfg.edge_detail),
                              ("Detail Roughness", cfg.edge_roughness))),
                    "MAGIC": ("ShaderNodeTexMagic", "Fac", (("Distortion", cfg.edge_distortion),)),
                    "GABOR": ("ShaderNodeTexGabor", "Value",
                              (("Frequency", cfg.edge_frequency),
                               ("Anisotropy", cfg.edge_anisotropy))),
                    "NOISE": ("ShaderNodeTexNoise", "Fac",
                              (("Detail", cfg.edge_detail), ("Roughness", cfg.edge_roughness),
                               ("Distortion", cfg.edge_distortion)))}[k]
            n = self.node(spec[0], "Seam" + k.title(), "Seam pattern: " + k.title())
            if k == "GABOR":
                _set(n, "gabor_type", "3D")
            elif k == "NOISE":
                _set(n, "noise_dimensions", "3D")
            for key, v in spec[2]:
                _set(sk(n.inputs, key), "default_value", v)
            self.put(sk(n.inputs, "Vector"), self.gen)
            _set(sk(n.inputs, "Scale"), "default_value", cfg.noise_scale)
            hi = cfg.edge_low + max(cfg.edge_high - cfg.edge_low, 1e-3)
            return self.MR(sk(n.outputs, spec[1]), cfg.edge_low, hi, 0.0, -1.0 / self.softness)
        return self.lazy("seam", make)

    def equi_uv(self):
        def make():
            c = self.node("ShaderNodeCombineXYZ", "EquiUV", "Panorama UV")
            self.put(c.inputs["X"], self.M("MULTIPLY_ADD", self.az, -1.0 / TWO_PI,
                                           0.5 + self.cfg.sky_rotation / TWO_PI))
            self.put(c.inputs["Y"], self.M("MULTIPLY_ADD", self.el, 1.0 / math.pi, 0.5))
            return c.outputs["Vector"]
        return self.lazy("equi", make)

    def item_v(self, it, i, N, ws):
        cfg = self.cfg
        if cfg.vertical_mode == "ASPECT":
            iw, ih = it.image.size
            aspect = ih / iw if iw > 0 and ih > 0 else 1.0
            hspan = TWO_PI if N == 1 else (TWO_PI / N) * (1.0 + ws[i] / 2 + ws[(i + 1) % N] / 2)
            span = max(hspan * aspect * cfg.z_scale, 0.05)
            horizon = max(cfg.sky_horizon, 0.0)
            return self.M("MULTIPLY_ADD", self.el, 1.0 / span, -horizon / span, clamp=True)
        bottom = max(cfg.sky_bottom, 0.0)
        span = max(cfg.sky_top - bottom, 1e-3)
        return self.lazy("vfull", lambda: self.M("MULTIPLY_ADD", self.el, 1.0 / span,
                                                 -bottom / span, clamp=True, label="V"))

    def tex(self, it, name, coords, auto_ext, center=False, image=None):
        img = image or it.image
        n = self.node("ShaderNodeTexImage", name, img.name)
        _set(n, "image", img)
        _set(n, "interpolation", it.tex_interpolation)
        _set(n, "projection", it.tex_projection)
        _set(n, "projection_blend", it.tex_projection_blend)
        _set(n, "extension", auto_ext if it.tex_extension == "AUTO" else it.tex_extension)
        for k in ("frame_duration", "frame_start", "frame_offset", "use_cyclic", "use_auto_refresh"):
            _set(n.image_user, k, getattr(it, k))
        if image is not None:
            self.put(n.inputs["Vector"], coords)
            return n, coords
        mp = self.node("ShaderNodeMapping", "TexMap", "Mapping")
        _set(mp, "vector_type", it.map_type)
        loc = tuple(it.map_location)
        for key, v in (("Location", (loc[0] + 0.5, loc[1] + 0.5, loc[2]) if center else loc),
                       ("Rotation", tuple(it.map_rotation)), ("Scale", tuple(it.map_scale))):
            _set(mp.inputs[key], "default_value", v)
        self.put(mp.inputs["Vector"], coords)
        self.put(n.inputs["Vector"], mp.outputs["Vector"])
        return n, mp.outputs["Vector"]

    def bw(self, color, label):
        n = self.node("ShaderNodeRGBToBW", "Lum", label)
        self.put(n.inputs["Color"], color)
        return n.outputs["Val"]

    def texture(self, it, name, coords, auto_ext, alpha=True, center=False):
        img, uv = self.tex(it, name, coords, auto_ext, center)
        return self.texture_out(it, img, uv, auto_ext, alpha)

    def texture_out(self, it, img, uv, auto_ext, alpha=True):
        color, a, src = img.outputs["Color"], None, alpha_source(it)
        if alpha:
            if src == "CHANNEL":
                a = img.outputs["Alpha"]
            elif src == "LUMINANCE":
                a = self.bw(img.outputs["Color"], "Luminance alpha")
            elif src == "IMAGE":
                a = self.bw(self.tex(it, "AlphaImg", uv, auto_ext, image=it.alpha_image)[0]
                            .outputs["Color"], "Alpha image")
            else:
                a = 1.0
            if len(it.alpha_modifiers):
                if not isinstance(a, bpy.types.NodeSocket):
                    a = self.M("ADD", a, 0.0, label="Opaque")
                a = self.modifiers(it.alpha_modifiers, a)
        return self.modifiers(it.modifiers, color), a

    def panorama(self, it):
        uv = self.node("ShaderNodeCombineXYZ", "PanoUV", "Panorama UV")
        self.put(uv.inputs["X"], self.M("MULTIPLY_ADD", self.az, -1.0 / TWO_PI, 0.5))
        self.put(uv.inputs["Y"], self.M("MULTIPLY_ADD", self.el, 1.0 / math.pi, 0.5))
        color, a = self.texture(it, "SkyImg", uv.outputs["Vector"], "REPEAT")
        mk = self.mask_value(it.masks, uv.outputs["Vector"], uv.outputs["Vector"], "REPEAT")
        self.ring_images += 1
        return color, (a if mk is None else self.M("MULTIPLY", a, mk))

    def mask_value(self, masks, img_coords, proc_coords, ext):
        acc = None
        for m in effective(masks):
            lo, hi = (1.0, 1.0 - m.intensity) if m.invert else (1.0 - m.intensity, 1.0)
            if m.kind == "GRADIENT":
                x, f0, f1 = self.el, m.angle_from, m.angle_to
                f1 = f1 if abs(f1 - f0) >= 1e-3 else f0 + 1e-3
            else:
                if m.kind == "IMAGE":
                    t = self.node("ShaderNodeTexImage", "MaskImg", "Mask: " + m.image.name)
                    _set(t, "image", m.image)
                    _set(t, "extension", ext)
                    uv = img_coords() if callable(img_coords) else img_coords
                    self.put(t.inputs["Vector"], self.V("SCALE", uv, m.scale, name="MaskMap"))
                    x = t.outputs["Color"]
                else:
                    idname = "ShaderNodeTexNoise" if m.kind == "NOISE" else "ShaderNodeTexVoronoi"
                    t = self.node(idname, "MaskNoise" if m.kind == "NOISE" else "MaskCells",
                                  "Mask: " + m.kind.title())
                    if m.kind == "NOISE":
                        _set(t, "noise_dimensions", "3D")
                        _set(sk(t.inputs, "Detail"), "default_value", 2.0)
                    self.put(sk(t.inputs, "Vector"), proc_coords)
                    _set(sk(t.inputs, "Scale"), "default_value", m.scale)
                    x = sk(t.outputs, "Fac") if m.kind == "NOISE" else t.outputs["Distance"]
                f0, f1 = m.low, m.low + max(m.high - m.low, 1e-3)
            x = self.modifiers(m.modifiers, self.MR(x, f0, f1, lo, hi))
            acc = x if acc is None else self.M(m.blend, acc, x, clamp=m.blend != "MULTIPLY")
        return acc

    def modifiers(self, mods, color):
        for m in mods:
            f = m.factor if m.enabled else 0.0
            k = m.kind
            if k in ("INVERT", "HUE_SAT", "CURVES"):
                n = self.node({"INVERT": "ShaderNodeInvert", "HUE_SAT": "ShaderNodeHueSaturation",
                               "CURVES": "ShaderNodeRGBCurve"}[k], "Mod" + k.title().replace("_", ""))
                self.put(n.inputs["Fac"], f)
                self.put(n.inputs["Color"], color)
                if k == "HUE_SAT":
                    for key in ("Hue", "Saturation", "Value"):
                        self.put(n.inputs[key], getattr(m, key.lower()))
                elif k == "CURVES" and not self.sync:
                    n[TAG] = m.uid
                    restore(n, self.ramps.get(m.uid))
                color = n.outputs["Color"]
                continue
            if k == "BRIGHT_CONTRAST":
                n = self.node("ShaderNodeBrightContrast", "ModBrightContrast")
                for key, v in (("Color", color), ("Bright", m.bright), ("Contrast", m.contrast)):
                    self.put(n.inputs[key], v)
                out = n.outputs["Color"]
            elif k == "GAMMA":
                n = self.node("ShaderNodeGamma", "ModGamma")
                self.put(n.inputs["Color"], color)
                self.put(n.inputs["Gamma"], m.gamma)
                out = n.outputs["Color"]
            elif k == "RAMP":
                n = self.node("ShaderNodeValToRGB", "ModRamp")
                if not self.sync:
                    n[TAG] = m.uid
                    restore(n, self.ramps.get(m.uid))
                self.put(n.inputs["Fac"], color)
                out = n.outputs["Color"]
            elif k == "MATH":
                out = self.V(m.math_op, color, (m.math_value,) * 3, name="ModMath")
            else:
                out = self.V("MULTIPLY", color, (m.multiplier,) * 3, name="ModMultiply")
            color = self.MIX(f, color, out, "MIX", "ModMix")
            _set(color.node, "clamp_result", k == "MATH" and m.math_clamp)
        return color

    def visible(self, i, N, ws, spin):
        key = str(i)
        if self.sync:
            return key not in self.culled_set
        if self.vis is None or N == 1 or spin != 0.0:
            return True
        rot_p = self.cfg.sky_rotation * N / TWO_PI
        phi0 = math.pi - TWO_PI * ((i - ws[i] / 2) - rot_p) / N
        phi1 = math.pi - TWO_PI * ((i + 1 + ws[(i + 1) % N] / 2) - rot_p) / N
        center, half = (phi0 + phi1) / 2, (phi0 - phi1) / 2
        ok = any(culling.angular_distance(center, c) <= half + h for c, h in self.vis["arcs"])
        if not ok:
            self.culled_keys.append(key)
        return ok

    def ring(self, items, spin):
        if len(items) == 1 and items[0].panorama:
            return self.panorama(items[0])
        N = len(items)
        ws = [max(it.blend_width, 0.01) for it in items]
        k = self.softness
        offset = 0.5 * N + self.cfg.sky_rotation * N / TWO_PI
        pn = self.node("ShaderNodeMath", "Ring", "Ring position")
        _set(pn, "operation", "MULTIPLY_ADD")
        self.put(pn.inputs[0], self.az)
        _set(pn.inputs[1], "default_value", -N / TWO_PI)
        if spin != 0.0:
            self.drive(pn.inputs[2], None, "%.8f-%.8f*frame/%.6f" % (offset, spin * N / TWO_PI, self.fps))
        else:
            _set(pn.inputs[2], "default_value", offset)
        p = pn.outputs[0]
        self.ring_images += N
        us, weights = [p], [1.0]
        if N == 2:
            nk = self.seam_noise()
            us = []
            for i in range(2):
                d = self.M("WRAP", p, i + 1.5, i - 0.5)
                D = 1.0 + ws[i] / 2 + ws[1 - i] / 2
                us.append(self.M("MULTIPLY_ADD", d, 1.0 / D, (ws[i] / 2 - i) / D))
            D1 = 1.0 + ws[1] / 2 + ws[0] / 2
            s1 = self.M("MULTIPLY_ADD", us[1], D1 * (1.0 + k) / (k * ws[1]), nk, clamp=True)
            d1 = self.M("MULTIPLY_ADD", us[1], D1, 1.0 - ws[1] / 2 - 2.0 + ws[0] / 2)
            s0 = self.M("MULTIPLY_ADD", d1, (1.0 + k) / (k * ws[0]), nk, clamp=True)
            w1 = self.mixf(s0, s1, 0.0, "Weight")
            weights = [self.M("SUBTRACT", 1.0, w1), w1]
        elif N > 2:
            us, seam = [], []
            for i in range(N):
                d = self.M("WRAP", p, i + N / 2, i - N / 2)
                D = 1.0 + ws[i] / 2 + ws[(i + 1) % N] / 2
                u = self.M("MULTIPLY_ADD", d, 1.0 / D, (ws[i] / 2 - i) / D)
                us.append(u)
                seam.append(self.M("MULTIPLY_ADD", u, D * (1.0 + k) / (k * ws[i]),
                                   self.seam_noise(), clamp=True))
            weights = [self.mixf(seam[(i + 1) % N], seam[i], 0.0, "Weight") for i in range(N)]
        use_alpha = any(effective(it.masks) or alpha_source(it) != "NONE" for it in items)
        acc = acc_a = None
        for i, it in enumerate(items):
            with self.section("Sky image %d: %s  (Images › Sky)" % (i + 1, it.image.name)):
                shown = self.visible(i, N, ws, spin)
                if shown:
                    uv = self.node("ShaderNodeCombineXYZ", "UV")
                    self.put(uv.inputs["X"], us[i])
                    self.put(uv.inputs["Y"], self.item_v(it, i, N, ws))
                    color, a = self.texture(it, "SkyImg", uv.outputs["Vector"],
                                            "REPEAT" if N == 1 else "EXTEND", use_alpha)
                    mk = self.mask_value(it.masks, uv.outputs["Vector"], uv.outputs["Vector"], "EXTEND")
                    a = mk if a is None else a if mk is None else self.M("MULTIPLY", a, mk)
                else:
                    self.culled += 1
                    avg = None if self.sync else culling.average_color(it.image)
                    color, a = (None, None) if self.sync else (
                        avg[:3], avg[3] if alpha_source(it) == "CHANNEL" else 1.0)
                if N == 1:
                    return color, (a if use_alpha else None)
                w = weights[i]
                if use_alpha and (not shown or a is not None):
                    w = self.M("MULTIPLY", w, a)
                acc = self.V("MULTIPLY_ADD", color, w, (0.0, 0.0, 0.0) if acc is None else acc,
                             name="Contrib")
                if use_alpha:
                    acc_a = w if acc_a is None else self.M("ADD", acc_a, w)
        if use_alpha:
            return self.V("DIVIDE", acc, self.M("MAXIMUM", acc_a, 1e-4), name="Unpremult"), acc_a
        return acc, None

    def cloud(self, it):
        ob = it.decal_object
        plane = self.group(static_group("dangus Cloud Plane", build_cloud_plane), "CloudPlane")
        self.put(plane.inputs["Direction"], self.gen)
        for key, chans, expr in (("Location", ("LOC_X", "LOC_Y", "LOC_Z"), "v"),
                                 ("Rotation", ("ROT_X", "ROT_Y", "ROT_Z"), "v"),
                                 ("Scale", ("SCALE_X", "SCALE_Y"), "max(abs(v),0.001)")):
            for axis, ch in enumerate(chans):
                self.drive(plane.inputs[key], axis, expr, ob, ch)
        if not self.sync:
            plane.inputs["Scale"].default_value[2] = 1.0
        ex, ey = decals.extents(it.image)
        _set(plane.inputs["Extent"], "default_value", (1.0 / ex, 1.0 / ey, 0.0))
        uv = plane.outputs["UV"]
        color, alpha = self.texture(it, "CloudImg", uv, "CLIP")
        edge = self.MR(plane.outputs["Edge"], 1.0, 1.0 - max(it.softness, 1e-4), 0.0, it.opacity,
                       name="CloudEdge")
        a = self.M("MULTIPLY", alpha, edge)
        mk = self.mask_value(it.masks, uv, uv, "CLIP")
        if mk is not None:
            a = self.M("MULTIPLY", a, mk)
        return self.V("MULTIPLY", color, tuple(it.tint), name="Tint"), a

    def fog(self, it):
        bottom, top = 0.0, max(it.fog_top, 1e-3)
        start = max(top - it.fog_fade, bottom)
        start = start if top - start >= 1e-3 else top - 1e-3
        alpha = self.MR(self.el, start, top, it.fog_density * it.opacity, 0.0, it.fog_falloff, "FogFade")
        coords = self.equi_uv
        if fog_kind(it) == "TEXTURE":
            iw, ih = it.image.size
            band = top - bottom
            tile = band if it.fog_fit != "REPEAT" else it.fog_tile
            n_u = 1 if it.fog_fit == "STRETCH" else max(
                1, round(TWO_PI / (tile * (iw / ih if iw > 0 and ih > 0 else 1.0))))
            uv = self.node("ShaderNodeCombineXYZ", "FogUV")
            self.put(uv.inputs["X"], self.M("MULTIPLY_ADD", self.az, -n_u / TWO_PI,
                                            n_u * (0.5 + it.fog_rotation / TWO_PI)))
            self.put(uv.inputs["Y"], self.M("MULTIPLY_ADD", self.el, 1.0 / tile, -bottom / tile))
            coords = uv.outputs["Vector"]
            color, talpha = self.texture(it, "FogImg", coords, "REPEAT")
            color = self.V("MULTIPLY", color, tuple(it.fog_color), name="FogTint")
            alpha = self.M("MULTIPLY", alpha, talpha)
        else:
            rgb = self.node("ShaderNodeRGB", "FogColor", "Fog colour")
            _set(rgb.outputs[0], "default_value", tuple(it.fog_color) + (1.0,))
            color = self.modifiers(it.modifiers, rgb.outputs[0])
        mk = self.mask_value(it.masks, coords, self.gen, "REPEAT")
        return color, (alpha if mk is None else self.M("MULTIPLY", alpha, mk))

    def layer_fac(self, L, fac):
        if not effective(L.masks):
            return fac
        with self.section("Where this layer shows  (Layer Masks)"):
            return self.M("MULTIPLY", fac, self.mask_value(L.masks, self.equi_uv, self.gen, "REPEAT"))

    def sky_layer(self, L):
        sk_, ov = skies(L), overlays(L)
        result, fac = self.below, self.opacity
        if sk_:
            if L.sky_spin != 0.0 and self.vis is not None and len(sk_) > 1:
                self.note = "'%s' spins, so its images are never culled" % L.name
            with self.section("%d sky image%s around the dome, blended at the seams  (Edge Mix: %s)" % (
                    len(sk_), "s" * (len(sk_) > 1), self.cfg.edge_noise.title())):
                result, sky_alpha = self.ring(sk_, L.sky_spin)
                if sky_alpha is not None:
                    fac = self.M("MULTIPLY", fac, sky_alpha)
        cover = None
        for it in ov:
            label = ("Fog rising from the horizon  (Images › Fog)" if it.kind == "FOG" else
                     "Cloud %s, placed by the Empty %s  (Images › Cloud)" % (
                         it.image.name, it.decal_object.name))
            with self.section(label):
                color, a = self.fog(it) if it.kind == "FOG" else self.cloud(it)
                result = self.MIX(a, result, color, it.blend)
                cover = a if cover is None else self.mixf(a, cover, 1.0, "Coverage")
        fac = self.layer_fac(L, fac)
        with self.section("Mix onto the layers below  (Blend: %s, Opacity)" % blend_name(L)):
            out = self.MIX(fac, self.below, result, L.blend if sk_ else "MIX", "Layer")
            clouds = None if cover is None else self.M("MULTIPLY", fac, cover, label="Cloud coverage")
            return out, (fac if sk_ else None), clouds

    def sun_position(self, L):
        if L.sun_mode == "TIME":
            A, B, C, D, cd, off = sun_terms(L)
            h = self.M("MULTIPLY_ADD", self.sun_in["Sun Time"], math.pi / 12.0, -math.pi,
                       label="Hour angle")
            c, s = self.M("COSINE", h), self.M("SINE", h)
            zs = self.M("MULTIPLY_ADD", c, D, C, label="Sun height")
            az = self.M("ADD", self.M("ARCTAN2", self.M("MULTIPLY_ADD", c, -B, A),
                                      self.M("MULTIPLY", s, -cd)), off, label="Sun azimuth")
            tilt = self.M("ARCCOSINE", zs, label="From the zenith")
        else:
            el, az = self.sun_in["Sun Elevation"], self.sun_in["Sun Azimuth"]
            zs, tilt = self.M("SINE", el, label="Sun height"), self.M("SUBTRACT", math.pi / 2, el)
        rot = self.node("ShaderNodeCombineXYZ", "SunRotation", "Sun rotation")
        for key, v in (("X", 0.0), ("Y", tilt), ("Z", az)):
            self.put(rot.inputs[key], v)
        return zs, rot.outputs["Vector"]

    def sun_texture(self, it, a, z, t_disc):
        fit = self.V("SCALE", self.sun_plane(a, z), 0.5 / t_disc, name="SunImgFit")
        c, al = self.texture(it, "SunImg", fit, "CLIP", center=True)
        return c, self.M("MULTIPLY", self.M("MULTIPLY", al, self.M("GREATER_THAN", z, 0.0)),
                         it.opacity, label="Image coverage")

    def sun_picture(self, it, a, z, L):
        w, h = it.image.size
        m = float(max(w, h, 1))
        t = math.tan(min(L.sun_size / 2.0, 1.5))
        fit_scale = (0.5 * m / (t * max(w, 1)), 0.5 * m / (t * max(h, 1)), 0.0)
        flat = self.sun_plane(a, z)
        img, uv = self.tex(it, "SunImg", self.V("MULTIPLY", flat, fit_scale, name="SunImgFit"),
                           "CLIP", center=True)
        c, al = self.texture_out(it, img, uv, "CLIP")
        off = self.V("ABSOLUTE", self.V("SUBTRACT", uv, (0.5, 0.5, 0.0), name="FromCentre"),
                     name="Abs")
        sep = self.node("ShaderNodeSeparateXYZ", "EdgeSep")
        self.put(sep.inputs[0], off)
        edge = self.M("MAXIMUM", sep.outputs["X"], sep.outputs["Y"], label="Distance to the edge")
        feather = self.MR(edge, 0.5, 0.5 * (1.0 - min(L.sun_softness, 1.0)) - 1e-4, 0.0, 1.0,
                          "SMOOTHSTEP", "Feather")
        cover = self.M("MULTIPLY", self.M("MULTIPLY", al, feather),
                       self.M("MULTIPLY", self.M("GREATER_THAN", z, 0.0), it.opacity),
                       label="Picture coverage")
        direction = self.V("NORMALIZE", flat, name="Normalize")
        side = self.V("CROSS_PRODUCT", (0.0, 0.0, 1.0), direction, name="GlowSide")
        acc = (0.0, 0.0, 0.0)
        for turn in GLOW_TURNS:
            ray = self.V("MULTIPLY_ADD", side, (math.sin(turn),) * 3, self.V(
                "SCALE", direction, math.cos(turn), name="GlowRay"), name="GlowRay")
            for k in range(GLOW_STEPS):
                reach = (k + 0.5) / GLOW_STEPS * t
                at = self.V("MULTIPLY", self.V("SCALE", ray, reach, name="GlowReach"),
                            fit_scale, name="SunImgFit")
                sample, _uv = self.tex(it, "GlowImg", at, "CLIP", center=True)
                acc = self.V("MULTIPLY_ADD", sample.outputs["Color"], sample.outputs["Alpha"],
                             acc, name="GlowSum")
        return c, cover, acc

    def sun_plane(self, a, z):
        q = self.V("DIVIDE", a, self.M("MAXIMUM", z, 1e-6), name="SunPlane")
        sep = self.node("ShaderNodeSeparateXYZ", "SunPlaneSep")
        self.put(sep.inputs[0], q)
        up = self.node("ShaderNodeCombineXYZ", "SunImageUp", "Picture upright")
        self.put(up.inputs["X"], self.M("MULTIPLY", sep.outputs["Y"], -1.0))
        self.put(up.inputs["Y"], self.M("MULTIPLY", sep.outputs["X"], -1.0))
        return up.outputs["Vector"]

    def sun_layer(self, L):
        with self.section("Where the sun is  (Position › %s)" % (
                "Time of Day" if L.sun_mode == "TIME" else "Angles")):
            zs, rot = self.sun_position(L)
        with self.section("Sun disc and its glow  (Disc & Glow)"):
            mp = self.node("ShaderNodeMapping", "SunFrame", "Into sun space")
            _set(mp, "vector_type", "TEXTURE")
            self.put(mp.inputs["Vector"], self.ndir)
            self.put(mp.inputs["Rotation"], rot)
            a = mp.outputs["Vector"]
            sep = self.node("ShaderNodeSeparateXYZ", "SunLocal")
            self.put(sep.inputs[0], a)
            z = sep.outputs["Z"]
            r = L.sun_size / 2.0
            r_out = min(r * (1.0 + L.sun_softness) + 1e-4, math.pi)
            disc = self.MR(z, math.cos(r_out), math.cos(r), 0.0, 1.0, "SMOOTHSTEP", "SunDisc")
            k = math.log(0.5) / math.log(max(math.cos(L.sun_glow_size), 1e-6))
            halo = self.M("POWER", self.M("MAXIMUM", z, 0.0), k, label="Glow")
        below, tsun = self.below, None
        if L.sun_relight_sky or L.sun_relight_disc:
            with self.section("Colour of sunlight through the air  (Relight › Haze)"):
                air = self.M("SUBTRACT", self.M("DIVIDE", 1.0, self.M("ADD", self.M(
                    "MAXIMUM", zs, 0.0), 0.025)), 1.0, label="Air mass - 1")
                tsun = self.V("POWER", extinction(L), air, name="SunTransmit")
        if L.sun_relight_sky:
            with self.section("Sunset colour and night on the layers below  (Relight Sky)"):
                day = self.MR(zs, -0.1, 0.1, L.sun_night, 1.0, "SMOOTHSTEP", "Daylight")
                toward = self.M("POWER", self.M("MULTIPLY_ADD", z, 0.5, 0.5), 3.0,
                                label="Towards the sun")
                tint = self.V("SCALE", self.MIX(toward, (1.0, 1.0, 1.0, 1.0), tsun, "MIX", "SkyTint"),
                              day, name="DayTint")
                below = self.MIX(L.sun_relight_amount, below,
                                 self.V("MULTIPLY", below, tint, name="Relit"), "MIX", "Relight")
        with self.section("How bright the sun is: sky layers above hide it, clouds dim it  (Brightness)"):
            oc = self.cloud_occ
            trans = self.M("POWER", L.sun_cloud_strength / max(L.sun_strength, 1e-6), oc,
                           label="Through clouds")
            thin = self.M("MULTIPLY", oc, self.M("SUBTRACT", 1.0, oc), label="Thin cloud")
            glow = self.M("MULTIPLY_ADD", thin, 4.0 * L.sun_scatter * L.sun_strength, L.sun_glow,
                          label="Glow and scatter")
            seen = self.M("MULTIPLY", trans, self.M("SUBTRACT", 1.0, self.sky_occ, label="Open sky"),
                          label="Seen")
            items = sun_items(L)
            image_only = bool(items) and not any(it.kind == "SUN_COLOR" for it in items)
            if not image_only:
                lit = self.M("MULTIPLY_ADD", halo, glow, self.M("MULTIPLY", disc, L.sun_strength))
                lit = self.M("MULTIPLY", lit, seen, label="Light")
        t_disc = math.tan(min(r_out, 1.5))
        if image_only:
            with self.section("Sun image brightness: the image is the sun  (Brightness)"):
                bright = self.M("MULTIPLY", seen, L.sun_strength, label="Image brightness")
                halo_amt = self.M("MULTIPLY", self.M("MULTIPLY", halo, glow), seen, label="Image glow")
            light = (0.0, 0.0, 0.0)
            for it in items:
                with self.section("Sun picture %s, sized by Disc & Glow › Size; it glows "
                                  "where it is bright  (Images › Sun)" % it.image.name):
                    c, cover, glow_sum = self.sun_picture(it, a, z, L)
                    tint = tuple(x * it.emission for x in it.tint)
                    glow_tint = tuple(x * it.opacity / GLOW_SAMPLES for x in tint)
                    glow_col = self.V("MULTIPLY", glow_sum, glow_tint, name="SunImgGlowTint")
                    light = self.V("MULTIPLY_ADD", glow_col, halo_amt, light, name="SunImgGlow")
                    light = self.MIX(cover, light, self.V("SCALE", self.V(
                        "MULTIPLY", c, tint, name="SunImgTint"), bright, name="SunImgLight"),
                        it.blend, "SunMix")
        else:
            color = (0.0, 0.0, 0.0, 0.0)
            for it in items:
                if it.kind == "SUN_COLOR":
                    with self.section("Sun colour  (Images › Solid Color)"):
                        rgb = self.node("ShaderNodeRGB", "SunColor")
                        _set(rgb.outputs[0], "default_value", tuple(it.solid_color) + (1.0,))
                        c = self.modifiers(it.modifiers, rgb.outputs[0])
                        color = self.MIX(it.opacity, color, c, it.blend, "SunMix")
                    continue
                with self.section("Sun image %s, fitted to the disc  (Images › Sun)" % it.image.name):
                    c, al = self.sun_texture(it, a, z, t_disc)
                    tint = tuple(x * it.emission for x in it.tint)
                    avg = average_rgb(it.image)
                    color = self.MIX(it.opacity, color, tuple(x * t for x, t in zip(avg, tint)) +
                                     (1.0,), it.blend, "SunGlowTint")
                    color = self.MIX(al, color, self.V("MULTIPLY", c, tint, name="SunImgTint"),
                                     it.blend, "SunMix")
            light = self.V("SCALE", color, lit, name="SunLight")
        fac = self.layer_fac(L, self.opacity)
        with self.section("Mix onto the layers below  (Blend: %s, Opacity)" % blend_name(L)):
            if L.sun_relight_disc:
                light = self.MIX(L.sun_relight_disc_amount, light,
                                 self.V("MULTIPLY", light, tsun, name="SunsetSun"), "MIX", "Sunset")
            additive = 0.0 if L.blend in ("ADD", "SCREEN", "LIGHTEN") else 1.0
            fac = self.mixf(additive, fac, self.M("MULTIPLY", fac, self.M(
                "MINIMUM", self.bw(light, "Sun brightness"), 1.0)), "SunFac")
            return self.MIX(fac, below, light, L.blend, "SunLayer"), None, None


def average_rgb(img):
    key = (img.name, tuple(img.size), img.source, img.filepath, img.is_dirty)
    if key not in AVERAGES:
        AVERAGES[key] = tuple(culling.average_color(img)[:3])
    return AVERAGES[key]


def blend_name(L):
    return L.bl_rna.properties["blend"].enum_items[L.blend].name


def channelbag(id_data):
    ad = id_data.animation_data
    if not ad or not ad.action:
        return None
    from bpy_extras import anim_utils
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)


TIME_TRACK = "dangus Time slider colours"
TIME_PATH = re.compile(r'dangus\.layers\[(\d+)\]\.(sun_hours|sun_time)')


def time_display_value(layer):
    scene = layer.id_data
    bag = channelbag(scene)
    fc = bag.fcurves.find(layer.path_from_id("sun_time")) if bag else None
    return fc.evaluate(scene.frame_current_final) if fc and len(fc.keyframe_points) else None


def _set_keys(fc, keys):
    if [(k.co[0], round(k.co[1], 5), k.interpolation) for k in fc.keyframe_points] == [
            (f, round(v, 5), i) for f, v, i in keys]:
        return
    fc.keyframe_points.clear()
    for f, v, interp in keys:
        fc.keyframe_points.insert(f, v, options={"FAST"}).interpolation = interp
    fc.update()


def sync_time_keys(scene):
    from .utils import hm_to_hours, hours_to_hm
    cfg, bag = scene.dangus, channelbag(scene)
    curves = {}
    for fc in bag.fcurves if bag else ():
        m = TIME_PATH.fullmatch(fc.data_path)
        if m and int(m.group(1)) < len(cfg.layers):
            curves.setdefault(int(m.group(1)), {})[m.group(2)] = fc
    for i, pair in curves.items():
        L = cfg.layers[i]
        hours, shown = pair.get("sun_hours"), pair.get("sun_time")
        flat = list(L.get("dangus_time_keys", ()))
        before = dict(zip(flat[0::2], flat[1::2]))
        hk = {k.co[0] for k in hours.keyframe_points} if hours else set()
        sk_ = {k.co[0]: k.co[1] for k in shown.keyframe_points} if shown else {}
        removed = {f for f in hk if f in before and f not in sk_}
        changed = {f: v for f, v in sk_.items()
                   if f in hk and f in before and abs(v - before[f]) > 1e-4}
        added = {f: v for f, v in sk_.items() if f not in hk and f not in before}
        if hours is not None:
            points = hours.keyframe_points
            for j in reversed([j for j, k in enumerate(points) if k.co[0] in removed]):
                points.remove(points[j], fast=True)
            for k in points:
                if k.co[0] in changed:
                    d = hm_to_hours(changed[k.co[0]]) - k.co[1]
                    k.co[1] += d
                    k.handle_left[1] += d
                    k.handle_right[1] += d
        if added and hours is None:
            hours = bag.fcurves.new(L.path_from_id("sun_hours"))
        for f, v in added.items():
            hours.keyframe_points.insert(f, hm_to_hours(v), options={"FAST"})
        if hours is not None and len(hours.keyframe_points) == 0:
            bag.fcurves.remove(hours)
            hours = None
        if hours is None:
            if shown is not None:
                bag.fcurves.remove(shown)
            if "dangus_time_keys" in L:
                del L["dangus_time_keys"]
            continue
        hours.mute = True
        hours.update()
        if shown is None:
            shown = bag.fcurves.new(L.path_from_id("sun_time"))
        keys = [(k.co[0], hours_to_hm(k.co[1]), k.interpolation) for k in hours.keyframe_points]
        _set_keys(shown, keys)
        flat = [x for f, v, _i in keys for x in (f, v)]
        if [round(x, 5) for x in L.get("dangus_time_keys", ())] != [round(x, 5) for x in flat]:
            L["dangus_time_keys"] = flat
    ad = scene.animation_data
    if ad is None:
        return
    shown_any = bag is not None and any(fc.data_path.endswith(".sun_time") for fc in bag.fcurves)
    track = ad.nla_tracks.get(TIME_TRACK)
    if shown_any and track is None:
        track = ad.nla_tracks.new()
        track.name, track.mute = TIME_TRACK, True
    elif not shown_any and track is not None and not track.strips:
        ad.nla_tracks.remove(track)


def apply_time_keys(scene, frame=None):
    bag = channelbag(scene)
    frame = scene.frame_current_final if frame is None else frame
    for fc in bag.fcurves if bag else ():
        m = TIME_PATH.fullmatch(fc.data_path)
        if m and m.group(2) == "sun_hours" and fc.mute and int(m.group(1)) < len(scene.dangus.layers):
            L = scene.dangus.layers[int(m.group(1))]
            value = fc.evaluate(frame)
            if abs(L.sun_hours - value) > 1e-6:
                L.sun_hours = value


def remap_paths(scene, coll_path, mapping):
    bag = channelbag(scene)
    mapping = {i: j for i, j in mapping.items() if i != j}
    if bag is None or not mapping:
        return
    pattern = re.compile(re.escape(coll_path) + r'\[(\d+)\](.*)')
    moved = []
    for fc in list(bag.fcurves):
        m = pattern.fullmatch(fc.data_path)
        if m and int(m.group(1)) in mapping:
            j = mapping[int(m.group(1))]
            if j is None:
                bag.fcurves.remove(fc)
            else:
                moved.append((fc, "%s[%d]%s" % (coll_path, j, m.group(2))))
                fc.data_path = "dangus_remap_%d" % len(moved)
    for fc, path in moved:
        fc.data_path = path
    gn = scene.world.node_tree.nodes.get(SKY_NODE) if scene.world and scene.world.node_tree else None
    if gn is not None and "dangus_anim" in gn:
        del gn["dangus_anim"]


def mirror_animation(scene):
    sync_time_keys(scene)
    tree = scene.world.node_tree if scene.world else None
    gn = tree.nodes.get(SKY_NODE) if tree else None
    if gn is None or gn.node_tree is None or gn.node_tree.get(FLAG) != "top":
        return
    cfg, src, wanted = scene.dangus, channelbag(scene), {}
    for fc in src.fcurves if src else ():
        m = re.fullmatch(r'dangus\.layers\[(\d+)\]\.(\w+)', fc.data_path)
        if m and m.group(2) in ANIMATED and int(m.group(1)) < len(cfg.layers):
            name = "%s %s" % (cfg.layers[int(m.group(1))].name, ANIMATED[m.group(2)])
            if name in gn.inputs:
                wanted[name] = fc
    key = _hash([(n, fc.extrapolation, [(tuple(k.co), tuple(k.handle_left), tuple(k.handle_right),
                                         k.interpolation) for k in fc.keyframe_points])
                 for n, fc in sorted(wanted.items())] + [len(gn.inputs)])
    if gn.get("dangus_anim") == key:
        return
    prefix = 'nodes["%s"].inputs[' % SKY_NODE
    dst = channelbag(tree)
    for fc in [f for f in dst.fcurves if f.data_path.startswith(prefix)] if dst else ():
        dst.fcurves.remove(fc)
    for name, fc in wanted.items():
        sock = gn.inputs[name]
        keep = sock.default_value
        for k in fc.keyframe_points:
            sock.default_value = k.co[1]
            sock.keyframe_insert("default_value", frame=k.co[0])
        sock.default_value = keep
        out = channelbag(tree).fcurves.find(sock.path_from_id("default_value"))
        for a, b in zip(fc.keyframe_points, out.keyframe_points):
            b.interpolation, b.easing = a.interpolation, a.easing
            b.handle_left_type, b.handle_right_type = a.handle_left_type, a.handle_right_type
            b.handle_left, b.handle_right = a.handle_left, a.handle_right
        out.extrapolation = fc.extrapolation
    gn["dangus_anim"] = key


def keyed_settings(scene):
    bag = channelbag(scene)
    for fc in bag.fcurves if bag else ():
        m = re.fullmatch(r'dangus\.layers\[\d+\]\.(\w+)', fc.data_path)
        mirrored = m and (m.group(1) in ANIMATED or m.group(1) == "sun_time")
        if fc.data_path.startswith("dangus.") and not mirrored:
            return True
    return False


def apply_world_animation(scene, frame):
    tree = scene.world.node_tree if scene.world else None
    bag = channelbag(tree) if tree else None
    for fc in bag.fcurves if bag else ():
        owner, _, prop = fc.data_path.rpartition(".")
        try:
            target = tree.path_resolve(owner) if owner else tree
        except ValueError:
            continue
        value = fc.evaluate(frame)
        if hasattr(getattr(target, prop, None), "__len__"):
            getattr(target, prop)[fc.array_index] = value
        else:
            setattr(target, prop, value)


def sun_terms(L):
    dec = math.radians(23.44) * math.sin(TWO_PI * (L.sun_day - 81) / 365.0)
    lat = L.sun_latitude
    return (math.sin(dec) * math.cos(lat), math.cos(dec) * math.sin(lat),
            math.sin(dec) * math.sin(lat), math.cos(dec) * math.cos(lat), math.cos(dec),
            L.sun_north - math.pi / 2)


def extinction(L):
    return tuple(math.exp(-k * L.sun_haze) for k in (0.05, 0.11, 0.25))


LAYER_INPUTS = (("Below", "NodeSocketColor"), ("Opacity", "NodeSocketFloat"),
                ("Generated", "NodeSocketVector"), ("Direction", "NodeSocketVector"),
                ("Azimuth", "NodeSocketFloat"), ("Elevation", "NodeSocketFloat"))
DIRECTION = ("Generated", "Direction", "Azimuth", "Elevation")
LAYER_OUTPUTS = (("Color", "NodeSocketColor"), ("Coverage", "NodeSocketFloat"),
                 ("Cloud Coverage", "NodeSocketFloat"))


def sun_inputs(L):
    if L.kind != "SUN":
        return ()
    return (("Sky Occlusion", "NodeSocketFloat"), ("Cloud Occlusion", "NodeSocketFloat")) + tuple(
        ("Sun " + what, "NodeSocketFloat") for _L, what, _a, _lo, _hi, _st in sun_settings(L))


ANIMATED = {"opacity": "Opacity", "sun_hours": "Time", "sun_azimuth": "Azimuth",
            "sun_elevation": "Elevation"}


def sun_settings(L):
    if L.kind != "SUN":
        return []
    if L.sun_mode == "TIME":
        return [(L, "Time", "sun_hours", 0.0, 24.0, "NONE")]
    return [(L, "Azimuth", "sun_azimuth", -1e4, 1e4, "ANGLE"),
            (L, "Elevation", "sun_elevation", -math.pi / 2, math.pi / 2, "ANGLE")]


def top_inputs(ls):
    return [(L, "Opacity", "opacity", 0.0, 1.0, "FACTOR") for L in ls] + [
        x for L in ls for x in sun_settings(L)]


def _interface(g, inputs, outputs):
    want = [(n, "OUTPUT", k) for n, k in outputs] + [(n, "INPUT", k) for n, k in inputs]
    items = [s for s in g.interface.items_tree if s.item_type == "SOCKET"]
    if [(s.name, s.in_out, s.socket_type) for s in items] == want:
        return False
    pool = {}
    for s in items:
        pool.setdefault((s.name, s.in_out, s.socket_type), []).append(s)
    keep = [pool[w].pop(0) if pool.get(w) else
            g.interface.new_socket(name=w[0], in_out=w[1], socket_type=w[2]) for w in want]
    for s in [s for left in pool.values() for s in left]:
        g.interface.remove(s)
    for i, s in enumerate(keep):
        g.interface.move(s, i)
    return True


def _reset(g):
    if g.animation_data:
        for fc in list(g.animation_data.drivers):
            g.animation_data.drivers.remove(fc)
    g.nodes.clear()


def static_group(name, fill):
    g = bpy.data.node_groups.get(name)
    if g is not None and g.get(FLAG) == "static%d" % VERSION:
        return g
    g = g or bpy.data.node_groups.new(name, "ShaderNodeTree")
    _reset(g)
    g[FLAG] = "static%d" % VERSION
    fill(g, Tree(bpy.context.scene, g))
    layout(g)
    return g


def build_direction(g, t):
    _interface(g, (), [(k, "NodeSocketFloat" if k in ("Azimuth", "Elevation") else "NodeSocketVector")
                       for k in DIRECTION])
    go = t.node("NodeGroupOutput", "Out")
    with t.section("Which way the camera looks: around (azimuth) and up (elevation)"):
        gen = t.node("ShaderNodeTexCoord", "TexCoord", "View direction").outputs["Generated"]
        sep = t.node("ShaderNodeSeparateXYZ", "DirSep")
        t.put(sep.inputs[0], gen)
        flat = t.V("LENGTH", t.V("MULTIPLY", gen, (1.0, 1.0, 0.0), name="Flat"), name="Len", out="Value")
        for key, v in (("Generated", gen), ("Direction", t.V("NORMALIZE", gen, name="Normalize")),
                       ("Azimuth", t.M("ARCTAN2", sep.outputs["Y"], sep.outputs["X"], label="Azimuth")),
                       ("Elevation", t.M("ARCTAN2", sep.outputs["Z"], flat, label="Elevation"))):
            t.put(go.inputs[key], v)


def build_cloud_plane(g, t):
    _interface(g, [(k, "NodeSocketVector") for k in ("Direction", "Location", "Rotation", "Scale",
                                                     "Extent")],
               (("UV", "NodeSocketVector"), ("Edge", "NodeSocketFloat")))
    gi, go = t.node("NodeGroupInput", "In"), t.node("NodeGroupOutput", "Out")
    maps = []
    with t.section("The view seen from the cloud's Empty"):
        for name in ("CloudDir", "CloudOrigin"):
            mp = t.node("ShaderNodeMapping", name)
            mp.vector_type = "TEXTURE"
            for key in ("Rotation", "Scale") + (("Location",) if name == "CloudOrigin" else ()):
                t.put(mp.inputs[key], gi.outputs[key])
            if name == "CloudDir":
                t.put(mp.inputs["Vector"], gi.outputs["Direction"])
            else:
                mp.inputs["Vector"].default_value = (0.0, 0.0, 0.0)
            maps.append(mp.outputs["Vector"])
    with t.section("Where the view hits the cloud image"):
        a, nb = maps
        az, bz = t.node("ShaderNodeSeparateXYZ", "DirZ"), t.node("ShaderNodeSeparateXYZ", "OriginZ")
        t.put(az.inputs[0], a)
        t.put(bz.inputs[0], nb)
        s = t.M("DIVIDE", bz.outputs["Z"], az.outputs["Z"], label="-t")
        q = t.V("MULTIPLY", t.V("SUBTRACT", nb, t.V("SCALE", a, s, name="Ray"), name="Plane"),
                gi.outputs["Extent"], name="Q")
        t.put(go.inputs["UV"], t.V("MULTIPLY_ADD", q, (0.5, 0.5, 0.0), (0.5, 0.5, 0.0), name="UV"))
        qs = t.node("ShaderNodeSeparateXYZ", "QSep")
        t.put(qs.inputs[0], t.V("ABSOLUTE", q, name="Abs"))
        t.put(go.inputs["Edge"], t.M("MAXIMUM", t.M("MAXIMUM", qs.outputs["X"], qs.outputs["Y"]),
                                     t.M("GREATER_THAN", s, -1e-7, label="Behind"), label="Edge"))


def layout(g):
    nodes = [n for n in g.nodes if n.bl_idname != "NodeFrame"]
    feeds = {n.name: [] for n in nodes}
    for lk in g.links:
        feeds[lk.to_node.name].append(lk.from_node)
    depth = {}

    def d(n):
        if n.name not in depth:
            depth[n.name] = 0
            depth[n.name] = 1 + max((d(x) for x in feeds[n.name]), default=-1)
        return depth[n.name]
    blocks, widths = {}, {}
    for n in nodes:
        blocks.setdefault(n.parent.name if n.parent else "", []).append(n)
        widths[d(n)] = max(widths.get(d(n), 0.0), n.width)
    xs = [0.0]
    for k in range(max(widths) + 1):
        xs.append(xs[-1] + widths.get(k, 0.0) + 80.0)
    y0 = 0.0
    for key in sorted(blocks, key=lambda k: min(g.nodes.find(n.name) for n in blocks[k])):
        cols = {}
        for n in blocks[key]:
            col = cols.setdefault(d(n), [0.0])
            n.location_absolute = (xs[d(n)], y0 - col[0])
            col[0] += node_height(n) + 30.0
        y0 -= max(c[0] for c in cols.values()) + 140.0


EXTRA_ROWS = {"ShaderNodeMath": 2, "ShaderNodeVectorMath": 1, "ShaderNodeMapRange": 3,
              "ShaderNodeMix": 4, "ShaderNodeTexImage": 9, "ShaderNodeValToRGB": 6,
              "ShaderNodeRGBCurve": 11, "ShaderNodeRGB": 8, "ShaderNodeMapping": 1,
              "ShaderNodeTexNoise": 2, "ShaderNodeTexVoronoi": 3, "ShaderNodeTexWave": 3,
              "ShaderNodeTexGabor": 1, "ShaderNodeGroup": 1, "ShaderNodeHueSaturation": 0}


def node_height(n):
    rows = EXTRA_ROWS.get(n.bl_idname, 0) + sum(s.enabled for s in n.outputs)
    for s in n.inputs:
        if s.enabled and not s.hide:
            rows += 4 if s.type == "VECTOR" and not s.is_linked and not s.hide_value else 1
    return 40.0 + 22.0 * rows


def _layer_tree(L):
    g = L.tree
    if g is None or g.get(FLAG) != "layer":
        g = bpy.data.node_groups.new("dangus · " + L.name, "ShaderNodeTree")
        g[FLAG] = "layer"
        L.tree = g
    if not re.fullmatch(re.escape("dangus · " + L.name) + r"(\.\d{3})?", g.name):
        g.name = "dangus · " + L.name
    return g


def _run_layer(scene, L, g, sync, ramps, culled=()):
    t = Tree(scene, g, sync, ramps, culled)
    cfg = scene.dangus
    if not sync:
        t.iface = _interface(g, LAYER_INPUTS + sun_inputs(L), LAYER_OUTPUTS)
        if cfg.cull_camera and L.kind == "SKY":
            t.vis, t.note = culling.camera_visibility(scene, cfg.cull_margin, cfg.cull_animation)
    gi, go = t.node("NodeGroupInput", "In"), t.node("NodeGroupOutput", "Out")
    t.below, t.opacity = gi.outputs["Below"], gi.outputs["Opacity"]
    t.gen, t.ndir, t.az, t.el = (gi.outputs[k] for k in DIRECTION)
    if L.kind == "SUN":
        t.sky_occ, t.cloud_occ = gi.outputs["Sky Occlusion"], gi.outputs["Cloud Occlusion"]
        t.sun_in = {"Sun " + w: gi.outputs["Sun " + w] for _L, w, _a, _lo, _hi, _st in sun_settings(L)}
    for key, v in zip(("Color", "Coverage", "Cloud Coverage"),
                      (t.sun_layer if L.kind == "SUN" else t.sky_layer)(L)):
        t.put(go.inputs[key], v)
    if not sync:
        layout(g)
    return t


def _run_top(scene, g, ls, sync):
    t = Tree(scene, g, sync)
    ins = top_inputs(ls)
    names = ["%s %s" % (L.name, what) for L, what, _a, _lo, _hi, _st in ins]
    if sync:
        socks = [s for s in g.interface.items_tree if s.item_type == "SOCKET" and s.in_out == "INPUT"]
        if len(socks) != len(ins):
            raise Mismatch()
        for s, name, (L, _w, attr, _lo, _hi, _st) in zip(socks, names, ins):
            if s.name != name:
                s.name = name
            _set(s, "default_value", getattr(L, attr))
    else:
        _interface(g, [(n, "NodeSocketFloat") for n in names], (("Color", "NodeSocketColor"),))
        for s, (L, _w, attr, lo, hi, st) in zip(
                [s for s in g.interface.items_tree if s.in_out == "INPUT"], ins):
            s.subtype = st
            s.default_value, s.min_value, s.max_value = getattr(L, attr), lo, hi
    gi, go = t.node("NodeGroupInput", "In"), t.node("NodeGroupOutput", "Out")
    with t.section("View direction, used by every layer"):
        dn = t.group(static_group("dangus Direction", build_direction), "Direction")
    current, covers = (0.0, 0.0, 0.0, 1.0), []
    order = [(i, L) for i, L in enumerate(ls) if L.kind != "SUN"] + [
        (i, L) for i, L in enumerate(ls) if L.kind == "SUN"]
    for i, L in order:
        sun = L.kind == "SUN"
        label = "Layer %s: %s, %d%% opacity%s  (Sky Layers)" % (
            L.name, blend_name(L), round(L.opacity * 100),
            ", sky layers above hide it" if sun else "")
        with t.section(label):
            occ = {}
            if sun:
                for j, w, socks in covers:
                    for key, cov in socks:
                        if j > i and cov is not None:
                            b = t.M("MULTIPLY_ADD", cov, -w, 1.0, label="Not covered")
                            occ[key] = t.M("MULTIPLY", occ[key], b) if key in occ else b
                occ = {k: t.M("SUBTRACT", 1.0, v, label=k) for k, v in occ.items()}
            n = t.group(L.tree, "Layer")
            links = [("Below", current), ("Opacity", gi.outputs[i])] + [
                ("Sun " + x[1], gi.outputs[ins.index(x)]) for x in sun_settings(L)]
            if sun:
                links += [(k, occ.get(k)) for k in ("Sky Occlusion", "Cloud Occlusion")]
            for k, v in links:
                t.put(n.inputs[k], v)
            for key in DIRECTION:
                t.put(n.inputs[key], dn.outputs[key])
            current = n.outputs["Color"]
            if not sun:
                covers.append((i, BLEND_OCCLUSION.get(L.blend, 1.0) if skies(L) else 1.0,
                               [("Sky Occlusion", n.outputs["Coverage"] if skies(L) else None),
                                ("Cloud Occlusion", n.outputs["Cloud Coverage"] if overlays(L)
                                 else None)]))
    with t.section("Ground below the horizon  (Dome › Ground)"):
        below = t.MR(dn.outputs["Elevation"], 0.0, -max(t.cfg.ground_blur, 1e-4), 0.0, 1.0,
                     "SMOOTHSTEP", "BelowHorizon")
        current = t.MIX(below, current, tuple(t.cfg.ground_color) + (1.0,), "MIX", "Ground")
    t.put(go.inputs["Color"], current)
    if not sync:
        layout(g)
    return t


def _world_values(scene, tree):
    cfg = scene.dangus
    bg = tree.nodes.get(PREFIX + "Background")
    if bg is not None:
        _set(bg.inputs["Strength"], "default_value", cfg.strength)
    gn = tree.nodes[SKY_NODE]
    for i, (L, _w, attr, _lo, _hi, _st) in enumerate(top_inputs(layers(cfg))):
        if i < len(gn.inputs):
            _set(gn.inputs[i], "default_value", getattr(L, attr))
    wc = getattr(scene.world, "cycles", None)
    res = LIGHT_MAP.get(cfg.light_map)
    if wc is not None:
        _set(wc, "sampling_method", "AUTOMATIC" if res is None else "MANUAL")
        if res is not None:
            _set(wc, "sample_map_resolution", res)


def _cleanup(scene):
    used = {L.tree.name for sc in bpy.data.scenes for L in sc.dangus.layers if L.tree}
    for g in [g for g in dangus_groups() if g.get(FLAG) == "layer" and g.name not in used]:
        bpy.data.node_groups.remove(g)
    if "dangus_alias" in scene:
        del scene["dangus_alias"]


def _sync_or_build(g, sig, run, store, before=None):
    if g.get("dangus_sig") == sig:
        try:
            t = run(True)
            if t.n == g.get("dangus_count"):
                return t, False
        except (Mismatch,) + SYNC_ERRORS:
            pass
    if before:
        before()
    _reset(g)
    t = run(False)
    g["dangus_sig"], g["dangus_count"] = sig, t.n
    store(t)
    return t, True


def _apply(scene, ls, force):
    stats = {"images": 0, "culled": 0, "layers": len(ls), "note": None, "rebuilt": False}
    gv = global_values(scene)
    ramps, force_top = [], force
    for L in ls:
        sig, vals = layer_signature(scene.dangus, L), _hash(gv + _values(L, []))
        g = L.tree
        if not force and g is not None and g.get(FLAG) == "layer" and g.get("dangus_sig") == sig \
                and g.get("dangus_vals") == vals:
            continue
        g = _layer_tree(L)
        if force:
            g["dangus_sig"] = None

        def run(sync, L=L, g=g):
            return _run_layer(scene, L, g, sync, ramps[0] if ramps else None,
                              g.get("dangus_culled", []) if sync else ())
        t, rebuilt = _sync_or_build(
            g, sig, run, lambda t, g=g: g.__setitem__("dangus_culled", list(t.culled_keys)),
            lambda: ramps or ramps.append(snapshot_ramps(scene)))
        g["dangus_vals"] = vals
        stats["rebuilt"] |= rebuilt
        force_top = force_top or rebuilt and t.iface
        stats["images"] += t.ring_images
        stats["culled"] += t.culled
        stats["note"] = stats["note"] or t.note
    tree = scene.world.node_tree
    top = tree.nodes[SKY_NODE].node_tree
    if force_top or top.get(FLAG) != "top":
        top["dangus_sig"] = None
    top[FLAG] = "top"
    _, rebuilt = _sync_or_build(top, _hash((VERSION, [(L.kind, L.kind == "SUN" and L.sun_mode)
                                                       for L in ls])),
                                lambda sync: _run_top(scene, top, ls, sync), lambda t: None)
    stats["rebuilt"] |= rebuilt
    _world_values(scene, tree)
    mirror_animation(scene)
    if stats["rebuilt"]:
        _cleanup(scene)
    return stats


def clear_sky_nodes(tree):
    ad = tree.animation_data
    for fc in list(ad.drivers) if ad else ():
        if fc.data_path.startswith('nodes["%s' % PREFIX):
            ad.drivers.remove(fc)
    for n in [n for n in tree.nodes if n.name.startswith(PREFIX)]:
        tree.nodes.remove(n)
    out = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputWorld"), None)
    if out is not None:
        out.is_active_output = True


def clear_sky(scene):
    world = scene.world
    if world and world.node_tree:
        clear_sky_nodes(world.node_tree)
    for L in scene.dangus.layers:
        L.tree = None
    removed = True
    while removed:
        removed = False
        for g in dangus_groups():
            if g.users == 0:
                bpy.data.node_groups.remove(g)
                removed = True


def build_sky(scene):
    cfg = scene.dangus
    decals.sync_objects(scene)
    ls = layers(cfg)
    if not ls:
        raise ValueError("Add a Sky Layer with at least one image")
    if cfg.vertical_mode == "FULL" and cfg.sky_top - cfg.sky_bottom < 0.1:
        raise ValueError("Sky Top must be above Sky Bottom")
    world = scene.world or bpy.data.worlds.new("dangus")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    tree = world.node_tree
    gn = tree.nodes.get(SKY_NODE)
    top = gn.node_tree if gn is not None and gn.bl_idname == "ShaderNodeGroup" else None
    clear_sky_nodes(tree)
    gn = tree.nodes.new("ShaderNodeGroup")
    gn.name, gn.label, gn.location = SKY_NODE, "dangus sky", (-300, 0)
    gn.node_tree = top or bpy.data.node_groups.new(TOP_NAME, "ShaderNodeTree")
    bg = tree.nodes.new("ShaderNodeBackground")
    bg.name, bg.location = PREFIX + "Background", (0, 0)
    out = tree.nodes.new("ShaderNodeOutputWorld")
    out.name, out.location = PREFIX + "Output", (250, 0)
    tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    out.is_active_output = True
    stats = _apply(scene, ls, True)
    tree.links.new(gn.outputs["Color"], bg.inputs["Color"])
    _world_values(scene, tree)
    return world, stats


def refresh(scene, force=False):
    world = scene.world
    tree = world.node_tree if world else None
    gn = tree.nodes.get(SKY_NODE) if tree else None
    if gn is None or gn.node_tree is None:
        return "none"
    decals.sync_objects(scene)
    ls = layers(scene.dangus)
    if not ls:
        return "none"
    if gn.node_tree.get(FLAG) != "top":
        build_sky(scene)
        return "rebuild"
    return "rebuild" if _apply(scene, ls, force)["rebuilt"] else "sync"
