import math
import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty,
                       IntProperty, StringProperty)
from bpy_extras.view3d_utils import region_2d_to_vector_3d
from mathutils import Vector

from . import bake, decals, live, nodes_builder, presets
from .properties import (ALPHA_SOURCES, EDGE_LEVELS, KINDS, LAYER_KINDS, MASK_KINDS,
                         MODIFIER_KINDS)
from .utils import SUN_KINDS

TARGETS = [("LAYER", "Layer", "Masks on the whole Sky Layer"),
           ("ITEM", "Item", "Masks on the selected image")]
MOD_OWNERS = [("ITEM", "Image", "The selected image"),
              ("ALPHA", "Alpha", "The selected image's alpha"),
              ("LAYER_MASK", "Layer Mask", "The selected layer mask"),
              ("ITEM_MASK", "Image Mask", "The selected image mask")]
DIRECTIONS = [("UP", "Up", "Move up"), ("DOWN", "Down", "Move down")]
HIDDEN = {"HIDDEN", "SKIP_SAVE"}
OK, NO = {"FINISHED"}, {"CANCELLED"}


def active_layer(cfg):
    return cfg.layers[cfg.active_layer] if 0 <= cfg.active_layer < len(cfg.layers) else None


def active_item(layer):
    return layer.items[layer.active_item] if layer and 0 <= layer.active_item < len(layer.items) else None


def _item(context):
    return active_item(active_layer(context.scene.dangus))


def _cloud(context):
    it = _item(context)
    return it if it is not None and it.kind == "DECAL" else None


def _unique(name, taken):
    cand, n = name, 1
    while cand in taken:
        cand, n = "%s.%03d" % (name, n), n + 1
    return cand


def _new_layer(scene, kind="SKY"):
    cfg = scene.dangus
    L = cfg.layers.add()
    L.name = _unique(scene.name if kind == "SKY" else "Sun", {x.name for x in cfg.layers if x != L})
    L.kind = kind
    if kind == "SUN":
        L.blend, L.sun_mode = "ADD", "TIME"
        c = L.items.add()
        c.kind, c.uid, c.name = "SUN_COLOR", decals.new_uid(), "Solid Color"
    cfg.active_layer = len(cfg.layers) - 1
    return L


def copy_props(src, dst, skip=("uid", "decal_object", "collection", "tree")):
    for p in src.bl_rna.properties:
        k = p.identifier
        if k in skip or k == "rna_type" or p.is_readonly and p.type != "COLLECTION":
            continue
        if p.type == "COLLECTION":
            coll = getattr(dst, k)
            coll.clear()
            for s in getattr(src, k):
                copy_props(s, coll.add(), skip)
        elif p.type == "POINTER" and isinstance(getattr(src, k), bpy.types.PropertyGroup):
            copy_props(getattr(src, k), getattr(dst, k), skip)
        else:
            setattr(dst, k, getattr(src, k))


def _alias_uids(scene, src, dst, skip=()):
    if "uid" in dst.bl_rna.properties:
        dst.uid = decals.new_uid()
        if src.uid:
            nodes_builder.alias_ramp(scene, dst.uid, src.uid)
    for p in src.bl_rna.properties:
        if p.type == "COLLECTION" and p.identifier not in skip:
            for a, b in zip(getattr(src, p.identifier), getattr(dst, p.identifier)):
                _alias_uids(scene, a, b)


def _copy_item(scene, layer, src, dst):
    copy_props(src, dst)
    _alias_uids(scene, src, dst)
    if src.kind == "DECAL" and src.decal_object is not None:
        decals.copy_object(src.decal_object, dst, layer, scene)


def _clamp(owner, attr, n):
    setattr(owner, attr, max(0, min(getattr(owner, attr), n - 1)))


def _remap(coll, mapping):
    nodes_builder.remap_paths(coll.id_data, coll.path_from_id(), mapping)


def _removed(coll, i, n):
    _remap(coll, {k: (None if k == i else k - 1) for k in range(i, n)})


def _inserted(coll, i, n):
    _remap(coll, {k: k + 1 for k in range(i, n)})


def _move(coll, i, direction):
    j = i - 1 if direction == "UP" else i + 1
    if 0 <= i < len(coll) and 0 <= j < len(coll):
        coll.move(i, j)
        _remap(coll, {i: j, j: i})
        return j
    return None


def _decal_index(layer):
    return sum(1 for x in layer.items if x.kind == "DECAL") - 1


def mask_stack(cfg, target):
    layer = active_layer(cfg)
    owner = layer if target == "LAYER" else active_item(layer)
    return (owner, "masks", "active_mask") if owner else None


def active_mask(cfg, target):
    st = mask_stack(cfg, target)
    if st is None:
        return None
    masks, i = st[0].masks, st[0].active_mask
    return masks[i] if 0 <= i < len(masks) else None


def modifier_stack(cfg, owner):
    if owner in ("ITEM", "ALPHA"):
        it = active_item(active_layer(cfg))
        return None if it is None else it.modifiers if owner == "ITEM" else it.alpha_modifiers
    m = active_mask(cfg, {"LAYER_MASK": "LAYER", "ITEM_MASK": "ITEM"}[owner])
    return m.modifiers if m else None


def _layer_poll(cls, context):
    return active_layer(context.scene.dangus) is not None


def _item_poll(cls, context):
    return _item(context) is not None


def _fileselect(self, context, event):
    context.window_manager.fileselect_add(self)
    return {"RUNNING_MODAL"}


classes = []


def op(idname, label, desc, run, poll=None, invoke=None, undo=True, **props):
    ns = {"bl_idname": "dangus." + idname, "bl_label": label, "bl_description": desc,
          "bl_options": {"REGISTER", "UNDO"} if undo else {"REGISTER"},
          "__annotations__": props}

    def execute(self, context):
        r = run(self, context)
        if r is None:
            live.schedule(context.scene)
        return r or OK
    ns["execute"] = execute
    if poll:
        ns["poll"] = classmethod(poll)
    if invoke:
        ns["invoke"] = invoke
    cls = type("DANGUS_OT_" + idname, (bpy.types.Operator,), ns)
    classes.append(cls)
    return cls


def _dup_layer(self, context):
    sc = context.scene
    cfg = sc.dangus
    i = cfg.active_layer
    src, dst = cfg.layers[i], cfg.layers.add()
    copy_props(src, dst, skip=("uid", "decal_object", "collection", "tree", "items"))
    _alias_uids(sc, src, dst, skip=("items",))
    dst.name = _unique(src.name, {L.name for L in cfg.layers if L != dst})
    for it in src.items:
        _copy_item(sc, dst, it, dst.items.add())
    dst.active_item = src.active_item
    cfg.layers.move(len(cfg.layers) - 1, i + 1)
    _inserted(cfg.layers, i + 1, len(cfg.layers) - 1)
    cfg.active_layer = i + 1


def _remove_layer(self, context):
    cfg = context.scene.dangus
    L = cfg.layers[cfg.active_layer]
    for it in L.items:
        decals.remove_object(it)
    decals.remove_layer_collection(L)
    n = len(cfg.layers)
    cfg.layers.remove(cfg.active_layer)
    _removed(cfg.layers, cfg.active_layer, n)
    _clamp(cfg, "active_layer", len(cfg.layers))


def _move_active(owner, coll, attr, direction):
    j = _move(getattr(owner, coll), getattr(owner, attr), direction)
    if j is None:
        return OK
    setattr(owner, attr, j)


op("add_layer", "New Layer", "Add a Sky Layer or a Sun Layer on top",
   lambda self, c: _new_layer(c.scene, self.kind) and None,
   kind=EnumProperty(items=LAYER_KINDS, default="SKY", options={"SKIP_SAVE"}))
op("duplicate_layer", "Duplicate Sky Layer",
   "Copy the active Sky Layer with all its images, masks and clouds", _dup_layer, _layer_poll)
op("remove_layer", "Remove Sky Layer", "Remove the active Sky Layer and its cloud Empties",
   _remove_layer, _layer_poll)
op("move_layer", "Move Sky Layer", "Move the active Sky Layer up or down the stack",
   lambda self, c: _move_active(c.scene.dangus, "layers", "active_layer", self.direction),
   _layer_poll, direction=EnumProperty(items=DIRECTIONS))


ITEM_NAMES = {"SUN_COLOR": "Solid Color", "FOG": "Fog"}


def _add_items(op_, context, images, kind):
    sun = kind in SUN_KINDS
    layer = active_layer(context.scene.dangus) or _new_layer(context.scene, "SUN" if sun else "SKY")
    if (layer.kind == "SUN") != sun:
        op_.report({"ERROR"}, "Select a %s Layer first: %s" % (
            ("Sun", "sun images and colours go there") if sun else
            ("Sky", "sky images, clouds and fog go there")))
        return NO
    if not images:
        return NO
    for img in images:
        item = layer.items.add()
        item.uid, item.image, item.kind = decals.new_uid(), img, kind
        if img is None:
            item.name = _unique(ITEM_NAMES[kind], {x.name for x in layer.items if x != item})
        if kind == "FOG" and img is not None:
            item.fog_mode = "TEXTURE"
        if kind == "DECAL":
            decals.ensure_object(item, layer, context.scene, _decal_index(layer))
    layer.active_item = len(layer.items) - 1


def _load_files(self):
    images = []
    for f in self.files:
        try:
            images.append(bpy.data.images.load(os.path.join(self.directory, f.name),
                                               check_existing=True))
        except RuntimeError as e:
            self.report({"WARNING"}, "Could not load %s: %s" % (f.name, e))
    return images


def _open_images(self, context):
    return _add_items(self, context, _load_files(self), self.kind)


def _image_items(self, context):
    return [(img.name, img.name, "", i) for i, img in enumerate(bpy.data.images)
            if img.type in {"IMAGE", "UV_TEST"}] or [("NONE", "(no images)", "")]


def _search(self, context, event):
    context.window_manager.invoke_search_popup(self)
    return {"RUNNING_MODAL"}


def _kind(default="SKY"):
    return EnumProperty(items=KINDS, default=default, options=HIDDEN)


op("add_image", "Open Images", "Import one or more image files into the active Sky Layer",
   _open_images, invoke=_fileselect,
   directory=StringProperty(subtype="DIR_PATH", options=HIDDEN),
   files=CollectionProperty(type=bpy.types.OperatorFileListElement, options=HIDDEN),
   filter_image=BoolProperty(default=True, options=HIDDEN),
   filter_folder=BoolProperty(default=True, options=HIDDEN), kind=_kind())
existing = op("add_existing_image", "Use Existing Image",
              "Add an image that is already loaded in this file",
              lambda self, c: _add_items(self, c, [i for i in [bpy.data.images.get(self.image)] if i],
                                         self.kind),
              invoke=_search, image=EnumProperty(items=_image_items, name="Image"), kind=_kind())
existing.bl_property = "image"

NEW_IMAGE = {"SKY": ("Sky", 2048, 1024, (0.38, 0.6, 0.9, 1.0), True),
             "DECAL": ("Cloud", 1024, 1024, (1.0, 1.0, 1.0, 0.0), True),
             "FOG": ("Fog", 2048, 512, (1.0, 1.0, 1.0, 1.0), True),
             "SUN_IMAGE": ("Sun", 1024, 1024, (1.0, 1.0, 1.0, 0.0), True)}


def _new_image_invoke(self, context, event):
    self.name, self.width, self.height, self.color, self.alpha = NEW_IMAGE[self.kind]
    return context.window_manager.invoke_props_dialog(self, title="New %s Image"
                                                      % NEW_IMAGE[self.kind][0])


def _new_image_draw(self, context):
    col = self.layout.column()
    col.use_property_split, col.use_property_decorate = True, False
    col.prop(self, "name")
    sub = col.column(align=True)
    sub.prop(self, "width")
    sub.prop(self, "height")
    for k in ("color", "alpha", "generated_type", "float_buffer"):
        col.prop(self, k)


def _new_image(self, context):
    img = bpy.data.images.new(self.name, self.width, self.height, alpha=self.alpha,
                              float_buffer=self.float_buffer)
    img.generated_type, img.generated_color = self.generated_type, self.color
    return _add_items(self, context, [img], self.kind)


op("new_image", "New Image", "Create a new image and add it to the active Sky Layer, as a sky "
   "image or as a cloud. It lives in this file: paint it, then save or pack it",
   _new_image, invoke=_new_image_invoke, kind=_kind(),
   name=StringProperty(name="Name", default="Sky"),
   width=IntProperty(name="Width", min=1, max=16384, default=2048, subtype="PIXEL"),
   height=IntProperty(name="Height", min=1, max=16384, default=1024, subtype="PIXEL"),
   color=FloatVectorProperty(name="Color", subtype="COLOR_GAMMA", size=4, min=0.0, max=1.0,
                             default=(0.38, 0.6, 0.9, 1.0)),
   alpha=BoolProperty(name="Alpha", default=True, description="Give the image an alpha channel"),
   generated_type=EnumProperty(name="Generated Type", default="BLANK", items=[
       (e.identifier, e.name, e.description)
       for e in bpy.types.Image.bl_rna.properties["generated_type"].enum_items]),
   float_buffer=BoolProperty(name="32-bit Float", default=False,
                             description="High dynamic range (needed for HDR skies)")
   ).draw = _new_image_draw
op("add_fog", "Gradient Fog", "Add a gradient fog (no image) to the active Sky Layer",
   lambda self, c: _add_items(self, c, [None], "FOG"))
op("add_sun_item", "Solid Color", "Add a Solid Color to the active Sun Layer",
   lambda self, c: _add_items(self, c, [None], self.kind),
   kind=EnumProperty(items=[k for k in KINDS if k[0] == "SUN_COLOR"], default="SUN_COLOR",
                     options={"SKIP_SAVE"}))


def _dup_image(self, context):
    L = active_layer(context.scene.dangus)
    i = L.active_item
    _copy_item(context.scene, L, L.items[i], L.items.add())
    L.items.move(len(L.items) - 1, i + 1)
    _inserted(L.items, i + 1, len(L.items) - 1)
    L.active_item = i + 1


def _remove_image(self, context):
    L = active_layer(context.scene.dangus)
    decals.remove_object(L.items[L.active_item])
    n = len(L.items)
    L.items.remove(L.active_item)
    _removed(L.items, L.active_item, n)
    _clamp(L, "active_item", len(L.items))
    if not any(it.kind == "DECAL" and it.decal_object for it in L.items):
        decals.remove_layer_collection(L)


def _set_kind(self, context):
    L = active_layer(context.scene.dangus)
    it, k = active_item(L), self.kind
    if it.kind == k:
        return NO
    if (it.kind in SUN_KINDS) or (k in SUN_KINDS):
        if (it.kind in SUN_KINDS) != (k in SUN_KINDS):
            self.report({"ERROR"}, "Sky items and sun items cannot be converted into each other")
        return NO
    if it.image is None and k != "FOG":
        self.report({"ERROR"}, "A gradient fog has no image: pick one in its Source tab")
        return NO
    if k != "DECAL":
        decals.remove_object(it)
    if k == "FOG":
        it.fog_mode = "TEXTURE" if it.image is not None else "GRADIENT"
    it.kind = k
    if k == "DECAL":
        decals.ensure_object(it, L, context.scene, _decal_index(L))


def _replace_image(self, context):
    try:
        _item(context).image = bpy.data.images.load(self.filepath, check_existing=True)
    except RuntimeError as e:
        self.report({"ERROR"}, str(e))
        return NO
    return OK


def _reload_image(self, context):
    img = _item(context).image
    if img is None:
        return NO
    img.reload()


op("duplicate_image", "Duplicate Image",
   "Copy the active image with its settings and masks (clouds get their own Empty)",
   _dup_image, _item_poll)
op("remove_image", "Remove Image", "Remove the active image (and its cloud Empty)",
   _remove_image, _item_poll)
op("move_image", "Move Image", "Move the active image up or down the list",
   lambda self, c: _move_active(active_layer(c.scene.dangus), "items", "active_item",
                                self.direction),
   _item_poll, direction=EnumProperty(items=DIRECTIONS))
op("set_kind", "Convert", "Use the active image as a sky image, a cloud or fog", _set_kind,
   _item_poll, kind=EnumProperty(items=KINDS, default="SKY"))
op("replace_image", "Replace Image",
   "Load another image file for the active item, keeping its settings", _replace_image,
   _item_poll, _fileselect, filepath=StringProperty(subtype="FILE_PATH", options=HIDDEN),
   filter_image=BoolProperty(default=True, options=HIDDEN),
   filter_folder=BoolProperty(default=True, options=HIDDEN))
op("reload_image", "Reload Image", "Reload the active image from disk", _reload_image, _item_poll)


def _add_masks(op_, context, kind, images=(None,)):
    st = mask_stack(context.scene.dangus, op_.target)
    if st is None:
        op_.report({"WARNING"}, "Nothing selected to mask")
        return NO
    if not images:
        return NO
    owner = st[0]
    for img in images:
        m = owner.masks.add()
        m.kind, m.image = kind, img
        if kind == "IMAGE":
            m.scale = 1.0
        elif kind in EDGE_LEVELS:
            m.low, m.high = EDGE_LEVELS[kind]
            if kind == "VORONOI":
                m.detail = 0.0
    owner.active_mask = len(owner.masks) - 1


def _add_mask(self, context):
    return _add_masks(self, context, self.kind)


NEW_MASK_IMAGE = {"LAYER": 2048, "ITEM": 1024}


def _new_mask_image_invoke(self, context, event):
    self.name, self.width, self.height = "Mask", NEW_MASK_IMAGE[self.target], 1024
    self.color, self.alpha = (1.0, 1.0, 1.0, 1.0), False
    return context.window_manager.invoke_props_dialog(self, title="New Mask Image")


def _new_mask_image(self, context):
    img = bpy.data.images.new(self.name, self.width, self.height, alpha=self.alpha,
                              float_buffer=self.float_buffer)
    img.generated_type, img.generated_color = self.generated_type, self.color
    return _add_masks(self, context, "IMAGE", [img])


def _remove_mask(self, context):
    st = mask_stack(context.scene.dangus, self.target)
    if st is None or not 0 <= st[0].active_mask < len(st[0].masks):
        return NO
    n = len(st[0].masks)
    st[0].masks.remove(st[0].active_mask)
    _removed(st[0].masks, st[0].active_mask, n)
    _clamp(st[0], "active_mask", len(st[0].masks))


def _add_modifier(self, context):
    mods = modifier_stack(context.scene.dangus, self.owner)
    if mods is None:
        return NO
    m = mods.add()
    m.uid, m.kind = decals.new_uid(), self.kind


def _remove_modifier(self, context):
    mods = modifier_stack(context.scene.dangus, self.owner)
    if mods is None or not 0 <= self.index < len(mods):
        return NO
    n = len(mods)
    mods.remove(self.index)
    _removed(mods, self.index, n)


def _move_modifier(self, context):
    mods = modifier_stack(context.scene.dangus, self.owner)
    if mods is None or _move(mods, self.index, self.direction) is None:
        return NO


def _owner():
    return EnumProperty(items=MOD_OWNERS, default="ITEM")


op("add_mask", "Add Mask", "Add a mask", _add_mask, _layer_poll,
   target=EnumProperty(items=TARGETS, default="LAYER"),
   kind=EnumProperty(items=MASK_KINDS, default="NOISE"))
op("add_mask_image", "Open Mask Images", "Load one or more image files as masks",
   lambda self, c: _add_masks(self, c, "IMAGE", _load_files(self)), _layer_poll, _fileselect,
   directory=StringProperty(subtype="DIR_PATH", options=HIDDEN),
   files=CollectionProperty(type=bpy.types.OperatorFileListElement, options=HIDDEN),
   filter_image=BoolProperty(default=True, options=HIDDEN),
   filter_folder=BoolProperty(default=True, options=HIDDEN),
   target=EnumProperty(items=TARGETS, default="LAYER", options=HIDDEN))
existing_mask = op("add_existing_mask_image", "Use Existing Image",
                   "Use an image that is already loaded in this file as a mask",
                   lambda self, c: _add_masks(self, c, "IMAGE", [
                       i for i in [bpy.data.images.get(self.image)] if i]),
                   _layer_poll, _search, image=EnumProperty(items=_image_items, name="Image"),
                   target=EnumProperty(items=TARGETS, default="LAYER", options=HIDDEN))
existing_mask.bl_property = "image"
op("new_mask_image", "New Mask Image", "Create a new image and use it as a mask. It lives in "
   "this file: paint it, then save or pack it",
   _new_mask_image, _layer_poll, _new_mask_image_invoke,
   target=EnumProperty(items=TARGETS, default="LAYER", options=HIDDEN),
   name=StringProperty(name="Name", default="Mask"),
   width=IntProperty(name="Width", min=1, max=16384, default=2048, subtype="PIXEL"),
   height=IntProperty(name="Height", min=1, max=16384, default=1024, subtype="PIXEL"),
   color=FloatVectorProperty(name="Color", subtype="COLOR_GAMMA", size=4, min=0.0, max=1.0,
                             default=(1.0, 1.0, 1.0, 1.0)),
   alpha=BoolProperty(name="Alpha", default=False, description="Give the image an alpha channel"),
   generated_type=EnumProperty(name="Generated Type", default="BLANK", items=[
       (e.identifier, e.name, e.description)
       for e in bpy.types.Image.bl_rna.properties["generated_type"].enum_items]),
   float_buffer=BoolProperty(name="32-bit Float", default=False,
                             description="High dynamic range")
   ).draw = _new_image_draw
op("remove_mask", "Remove Mask", "Remove the active mask", _remove_mask, _layer_poll,
   target=EnumProperty(items=TARGETS, default="LAYER"))
op("add_modifier", "Add Modifier", "Add a modifier at the bottom of the stack", _add_modifier,
   owner=_owner(), kind=EnumProperty(items=MODIFIER_KINDS, default="INVERT"))
op("remove_modifier", "Remove Modifier", "Remove this modifier", _remove_modifier,
   owner=_owner(), index=IntProperty(min=0))
op("move_modifier", "Move Modifier",
   "Move this modifier up or down the stack (applied top to bottom)", _move_modifier,
   owner=_owner(), index=IntProperty(min=0), direction=EnumProperty(items=DIRECTIONS))


def _set_alpha_source(self, context):
    it = _item(context)
    it.alpha_source = self.source
    if self.source == "IMAGE" and it.alpha_image is None and it.image is not None:
        self.report({"INFO"}, "Pick or create the alpha image below")


def _alpha_image_invoke(self, context, event):
    it = _item(context)
    w, h = tuple(it.image.size) if it.image is not None and it.image.size[0] else (1024, 1024)
    self.name, self.width, self.height = (it.image.name if it.image else "Image") + " Alpha", w, h
    return context.window_manager.invoke_props_dialog(self, title="New Alpha Image")


def _new_alpha_image(self, context):
    it = _item(context)
    img = bpy.data.images.new(self.name, self.width, self.height, alpha=False)
    img.generated_color = (1.0, 1.0, 1.0, 1.0)
    it.alpha_image, it.alpha_source = img, "IMAGE"


op("set_alpha_source", "Alpha Source", "Where the active image's alpha comes from",
   _set_alpha_source, _item_poll,
   source=EnumProperty(items=[s[:3] for s in ALPHA_SOURCES], default="CHANNEL",
                       options={"SKIP_SAVE"}))
op("new_alpha_image", "New Alpha Image", "Create a white image to paint the active image's "
   "alpha on (white = opaque, black = transparent)", _new_alpha_image, _item_poll,
   _alpha_image_invoke, name=StringProperty(name="Name", default="Alpha"),
   width=IntProperty(name="Width", min=1, max=16384, default=1024, subtype="PIXEL"),
   height=IntProperty(name="Height", min=1, max=16384, default=1024, subtype="PIXEL"))


def _select_cloud(self, context):
    ob = _cloud(context).decal_object
    if ob.name not in context.view_layer.objects:
        self.report({"WARNING"}, "The Empty is not in this view layer")
        return NO
    for o in context.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob
    return OK


op("select_cloud", "Select Empty", "Select the cloud's Empty: move it (G) over the dome, rotate "
   "(R) to roll and scale (S) it right where the cloud is", _select_cloud,
   lambda cls, c: _cloud(c) is not None and _cloud(c).decal_object is not None)


class DANGUS_OT_place_cloud(bpy.types.Operator):
    bl_idname = "dangus.place_cloud"
    bl_label = "Place"
    bl_description = ("Aim the cloud at the sky: move the mouse in a perspective 3D view, "
                      "click and drag to size it, mouse wheel to roll, Esc to cancel. The "
                      "Empty moves onto the dome at the cloud")
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return bool(_cloud(context) and context.area and context.area.type == "VIEW_3D")

    def invoke(self, context, event):
        sc = context.scene
        ob = self.ob = decals.ensure_object(_cloud(context), active_layer(sc.dangus), sc)
        self.area, self.orig = context.area, ob.matrix_world.copy()
        self.dist, self.dir = decals.distance(ob), decals.direction(ob)
        self.size = max(ob.scale.x, 0.02 * self.dist)
        rel = decals.aim_rotation(self.dir).to_matrix().transposed() @ \
            ob.matrix_world.to_3x3().normalized()
        self.roll, self.anchor = math.atan2(rel[1][0], rel[0][0]), None
        context.window.cursor_modal_set("CROSSHAIR")
        context.workspace.status_text_set(
            "Place Cloud: move = aim | LMB drag = size | Wheel = roll | MMB = orbit | Esc/RMB = cancel")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _ray(self, event):
        rv3d = self.area.spaces.active.region_3d
        if rv3d.is_perspective:
            for r in self.area.regions:
                x, y = event.mouse_x - r.x, event.mouse_y - r.y
                if r.type == "WINDOW" and 0 <= x < r.width and 0 <= y < r.height:
                    return region_2d_to_vector_3d(r, rv3d, (x, y)).normalized()

    def _apply(self):
        decals.place(self.ob, self.dir, self.dist, self.size, self.roll)

    def _finish(self, context, cancel):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        if cancel:
            self.ob.matrix_world = self.orig
        return NO if cancel else OK

    def modal(self, context, event):
        t, v = event.type, event.value
        if t == "MIDDLEMOUSE" or t.startswith(("NUMPAD", "TRACKPAD")):
            return {"PASS_THROUGH"}
        if t == "MOUSEMOVE":
            d = self._ray(event)
            if d is not None:
                if self.anchor is None:
                    self.dir = decals.on_dome(d)
                else:
                    self.size = max(math.tan(min(self.anchor.angle(d), 1.4)), 0.02) * self.dist
                self._apply()
        elif t == "LEFTMOUSE" and v == "PRESS":
            d = self._ray(event)
            if d is not None:
                self.anchor = Vector(d)
                self.dir = decals.on_dome(d)
                self._apply()
        elif t == "LEFTMOUSE" and v == "RELEASE" and self.anchor is not None:
            return self._finish(context, False)
        elif t in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            self.roll += math.radians(5.0) * (1 if t == "WHEELUPMOUSE" else -1)
            self._apply()
        elif t in {"RIGHTMOUSE", "ESC"} and v == "PRESS":
            return self._finish(context, True)
        return {"RUNNING_MODAL"}


classes.append(DANGUS_OT_place_cloud)


def _hovered_time(context):
    p = getattr(context, "property", None)
    if p and isinstance(p[0], bpy.types.Scene) and p[1].endswith(".sun_time"):
        return p[0], p[0].path_resolve(p[1].rpartition(".")[0])
    return None, None


class DANGUS_OT_key_time(bpy.types.Operator):
    bl_idname = "dangus.key_time"
    bl_label = "Keyframe Time"
    bl_description = "Insert (or delete) a keyframe on the time of day under the mouse"
    bl_options = {"UNDO", "INTERNAL"}

    delete: BoolProperty(options=HIDDEN)

    @classmethod
    def poll(cls, context):
        return _hovered_time(context)[1] is not None

    def execute(self, context):
        scene, L = _hovered_time(context)
        try:
            if self.delete:
                L.keyframe_delete("sun_hours")
            else:
                L.keyframe_insert("sun_hours")
        except RuntimeError as e:
            self.report({"WARNING"}, str(e))
            return NO
        nodes_builder.mirror_animation(scene)
        live.schedule(scene)
        for area in context.screen.areas:
            area.tag_redraw()
        return OK


classes.append(DANGUS_OT_key_time)
_keymaps = []


def register_keys():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new("User Interface")
    _keymaps.append((km, km.keymap_items.new("dangus.key_time", "I", "PRESS")))
    kmi = km.keymap_items.new("dangus.key_time", "I", "PRESS", alt=True)
    kmi.properties.delete = True
    _keymaps.append((km, kmi))


def unregister_keys():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def _preset_apply(self, context):
    if not presets.apply(context.scene.dangus, self.name):
        self.report({"ERROR"}, "Preset %r not found" % self.name)
        return NO


def _preset_save(self, context):
    name = self.name.strip()
    if not name or name in presets.BUILTIN:
        self.report({"ERROR"}, "Choose a name that is not a built-in preset")
        return NO
    try:
        presets.save(context.scene.dangus, name)
    except OSError as e:
        self.report({"ERROR"}, "Could not save the preset: %s" % e)
        return NO
    self.report({"INFO"}, "Saved Edge Mix preset %r" % name)
    return OK


def _build(self, context):
    try:
        _, stats = nodes_builder.build_sky(context.scene)
    except ValueError as e:
        self.report({"ERROR"}, str(e))
        return NO
    if context.scene.dangus.cull_camera:
        if stats["note"]:
            self.report({"WARNING"}, "Camera culling: %s" % stats["note"])
        else:
            self.report({"INFO"}, "Culled %d of %d sky images" % (stats["culled"], stats["images"]))
    return OK


def _refresh(self, context):
    try:
        nodes_builder.refresh(context.scene)
    except ValueError as e:
        self.report({"ERROR"}, str(e))
        return NO
    return OK


op("edge_preset_apply", "Apply Edge Mix Preset", "Load these Edge Mix settings", _preset_apply,
   name=StringProperty(options={"SKIP_SAVE"}))
op("edge_preset_save", "Save Edge Mix Preset",
   "Save the current Edge Mix settings as a preset (available in every file)", _preset_save,
   invoke=lambda self, c, e: c.window_manager.invoke_props_dialog(self), undo=False,
   name=StringProperty(name="Name", default="My Preset", options={"SKIP_SAVE"}))
op("edge_preset_remove", "Remove Edge Mix Preset", "Delete a saved Edge Mix preset",
   lambda self, c: OK if presets.remove(self.name) else NO,
   invoke=lambda self, c, e: c.window_manager.invoke_confirm(self, e), undo=False,
   name=StringProperty(options={"SKIP_SAVE"}))
op("build", "Build Sky", "Build the sky in the World, or force a full rebuild. After the first "
   "build every setting updates live", _build)
op("refresh", "Apply Now", "Apply pending changes immediately (normally automatic)", _refresh,
   undo=False)
op("clear", "Clear", "Remove the dangus node groups and their World nodes (settings are kept)",
   lambda self, c: nodes_builder.clear_sky(c.scene) or OK)


def load_panorama(scene, path, animated=False, frames=1, replace=True):
    img = bpy.data.images.load(path, check_existing=False)
    if animated and img.source == "FILE":
        img.source = "SEQUENCE"
    cfg = scene.dangus
    L = _new_layer(scene)
    L.name = _unique("Baked Sky", {x.name for x in cfg.layers if x != L})
    it = L.items.add()
    it.uid, it.kind, it.image, it.panorama = decals.new_uid(), "SKY", img, True
    if img.source in {"SEQUENCE", "MOVIE"}:
        it.frame_duration = img.frame_duration if img.source == "MOVIE" else frames
        it.frame_start, it.use_auto_refresh = scene.frame_start, True
        it.frame_offset = scene.frame_start - 1 if img.source == "SEQUENCE" else 0
        scene.sync_mode = "FRAME_DROP"
    if replace:
        for x in cfg.layers:
            x.enabled = x == L
    live.schedule(scene)
    return L


BAKE_ERRORS = (RuntimeError, ValueError, OSError, KeyError)


def _baked(op_, context, result):
    sc = context.scene
    path, animated, frames = result
    if sc.dangus.bake_load:
        load_panorama(sc, path, animated, frames)
    op_.report({"INFO"}, "Baked the sky to %s" % path)
    return OK


def _redraw(context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            area.tag_redraw()


class DANGUS_OT_bake_sky(bpy.types.Operator):
    bl_idname = "dangus.bake_sky"
    bl_label = "Bake Sky"
    bl_description = ("Render the whole sky dome (every layer, animated elements included) into "
                      "an equirectangular panorama image, image sequence or video. Esc cancels")
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            return _baked(self, context, bake.bake(context.scene))
        except BAKE_ERRORS as e:
            self.report({"ERROR"}, "Bake failed: %s" % e)
            return NO

    def invoke(self, context, event):
        wm, ui = context.window_manager, context.window_manager.dangus_ui
        if ui.baking:
            return NO
        self.run = bake.steps(context.scene)
        self.timer = wm.event_timer_add(0.05, window=context.window)
        sc, cfg = context.scene, context.scene.dangus
        count = sc.frame_end - sc.frame_start + 1 if cfg.bake_kind == "VIDEO" or \
            cfg.bake_animation else 1
        ui.baking, ui.bake_progress = True, 0.0
        ui.bake_status = "Rendering frame 1 of %d" % count
        wm.modal_handler_add(self)
        _redraw(context)
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        wm, ui = context.window_manager, context.window_manager.dangus_ui
        wm.event_timer_remove(self.timer)
        ui.baking = False
        _redraw(context)

    def modal(self, context, event):
        ui = context.window_manager.dangus_ui
        if event.type == "ESC":
            self.run.close()
            self._finish(context)
            self.report({"WARNING"}, "Bake cancelled")
            return NO
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            done, total, label = next(self.run)
        except StopIteration as stop:
            self._finish(context)
            return _baked(self, context, stop.value)
        except BAKE_ERRORS as e:
            self._finish(context)
            self.report({"ERROR"}, "Bake failed: %s" % e)
            return NO
        ui.bake_progress = done / total
        ui.bake_status = label
        _redraw(context)
        return {"RUNNING_MODAL"}


classes.append(DANGUS_OT_bake_sky)
op("load_panorama", "Load Panorama", "Add an equirectangular panorama (e.g. a baked sky) as a "
   "new Sky Layer that covers the whole dome",
   lambda self, c: load_panorama(c.scene, self.filepath, replace=self.replace) and OK,
   invoke=_fileselect, filepath=StringProperty(subtype="FILE_PATH", options=HIDDEN),
   filter_image=BoolProperty(default=True, options=HIDDEN),
   filter_movie=BoolProperty(default=True, options=HIDDEN),
   filter_folder=BoolProperty(default=True, options=HIDDEN),
   replace=BoolProperty(name="Hide Other Layers", default=True))
