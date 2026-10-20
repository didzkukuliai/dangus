import math
import os
import uuid

import bpy
from mathutils import Quaternion, Vector

FLAG = "dangus_decal"
COLOR = "COLOR_05"
DOME_RADIUS = 10.0
DEFAULT_HALF = 0.5
ROOT_FLAG = "dangus_root"
LAYER_FLAG = "dangus_layer"


def extents(image):
    iw, ih = image.size if image else (0, 0)
    if iw <= 0 or ih <= 0:
        return 1.0, 1.0
    return (1.0, ih / iw) if iw >= ih else (iw / ih, 1.0)


def aim_rotation(direction, roll=0.0, compat=None):
    q = (-Vector(direction)).normalized().to_track_quat("Z", "Y")
    q = q @ Quaternion((0.0, 0.0, 1.0), roll)
    return q.to_euler("XYZ", compat) if compat is not None else q.to_euler("XYZ")


def direction(ob):
    loc = ob.matrix_world.translation
    if loc.length > 1e-6:
        return loc.normalized()
    return -(ob.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()


def distance(ob):
    d = ob.location.length
    return d if d > 1e-6 else DOME_RADIUS


def get_scale(ob):
    return ob.scale.x / (DEFAULT_HALF * distance(ob)) if ob is not None else 1.0


def set_scale(ob, value):
    target = max(value, 1e-4) * DEFAULT_HALF * distance(ob)
    cur = ob.scale.x
    f = target / cur if abs(cur) > 1e-9 else None
    ob.scale = (target, target, target) if f is None else tuple(c * f for c in ob.scale)


def on_dome(dirn, lowest=math.radians(1.0)):
    d = Vector(dirn).normalized()
    if d.z >= math.sin(lowest):
        return d
    flat = Vector((d.x, d.y, 0.0))
    flat = flat.normalized() if flat.length > 1e-6 else Vector((1.0, 0.0, 0.0))
    return (flat * math.cos(lowest) + Vector((0.0, 0.0, math.sin(lowest)))).normalized()


def place(ob, dirn, distance, half_extent, roll=0.0):
    ob.location = Vector(dirn).normalized() * distance
    ob.rotation_euler = aim_rotation(dirn, roll, ob.rotation_euler)
    ob.scale = (half_extent, half_extent, half_extent)


def _stem(image, fallback="Cloud"):
    return os.path.splitext(image.name)[0] if image else fallback


def _has_base_name(idb, base):
    n = idb.name
    return n == base or (n.startswith(base + ".") and n[len(base) + 1:].isdigit())


def _rename(idb, base):
    if not _has_base_name(idb, base):
        idb.name = base


def _colour(coll):
    if coll.color_tag != COLOR:
        coll.color_tag = COLOR


def _style(ob, image):
    ob.empty_display_type = "IMAGE"
    ob.empty_display_size = 2.0
    ob.empty_image_offset = (-0.5, -0.5)
    ob.use_empty_image_alpha = True
    ob.color[3] = 0.6
    if ob.data is not image:
        ob.data = image


def root_collection(scene):
    for c in scene.collection.children_recursive:
        if c.get(ROOT_FLAG):
            break
    else:
        c = bpy.data.collections.new("dangus_%s" % scene.name)
        c[ROOT_FLAG] = True
        scene.collection.children.link(c)
    _rename(c, "dangus_%s" % scene.name)
    _colour(c)
    return c


def layer_collection(layer, scene):
    root = root_collection(scene)
    c = layer.collection
    if c is None or c.name not in bpy.data.collections:
        c = bpy.data.collections.new("%s_cloud" % layer.name)
        c[LAYER_FLAG] = True
        layer.collection = c
    if c.name not in root.children:
        root.children.link(c)
    _rename(c, "%s_cloud" % layer.name)
    _colour(c)
    return c


def _move_to(ob, coll):
    if coll not in ob.users_collection:
        coll.objects.link(ob)
    for c in list(ob.users_collection):
        if c is not coll and (c.get(LAYER_FLAG) or c.get(ROOT_FLAG)):
            c.objects.unlink(ob)
    if not ob.users_collection:
        coll.objects.link(ob)


def remove_layer_collection(layer):
    c = layer.collection
    if c is not None and c.get(LAYER_FLAG) and not c.objects and not c.children:
        bpy.data.collections.remove(c)


def new_uid():
    return uuid.uuid4().hex[:12]


def ensure_object(item, layer, scene, index=0):
    ob = item.decal_object
    if ob is not None:
        return ob
    ob = bpy.data.objects.new("%s_cloud" % _stem(item.image), None)
    _style(ob, item.image)
    ob.rotation_mode = "XYZ"
    az, el = -0.9 * index, math.radians(40.0)
    place(ob, (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)),
          DOME_RADIUS, DOME_RADIUS * DEFAULT_HALF)
    ob[FLAG] = True
    layer_collection(layer, scene).objects.link(ob)
    item.decal_object = ob
    return ob


def sync_objects(scene):
    cfg = scene.dangus
    for L in cfg.layers:
        clouds = [it for it in L.items if it.kind == "DECAL" and it.image is not None]
        for n, it in enumerate(L.items):
            if not it.uid:
                it.uid = new_uid()
        stacks = [it.modifiers for it in L.items]
        for masks in [L.masks] + [it.masks for it in L.items]:
            stacks += [m.modifiers for m in masks]
        for mods in stacks:
            for m in mods:
                if not m.uid:
                    m.uid = new_uid()
        for n, it in enumerate(clouds):
            ob = it.decal_object
            if ob is None:
                ensure_object(it, L, scene, n)
                continue
            if not ob.get(FLAG):
                continue
            _style(ob, it.image)
            _rename(ob, "%s_cloud" % _stem(it.image))
            _move_to(ob, layer_collection(L, scene))
        if not clouds and L.collection is not None:
            remove_layer_collection(L)


def remove_object(item):
    ob = item.decal_object
    if ob is not None and ob.get(FLAG):
        bpy.data.objects.remove(ob)


def copy_object(src, item, layer, scene):
    ob = src.copy()
    ob.name = "%s_cloud" % _stem(item.image)
    layer_collection(layer, scene).objects.link(ob)
    item.decal_object = ob
    return ob
