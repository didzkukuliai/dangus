import bpy

from . import nodes_builder, presets
from .bake import size as bake_size
from .operators import active_item, active_layer, mask_stack
from .properties import ALPHA_SOURCES, MASK_KINDS, MODIFIER_KINDS
from .utils import VERSION, has_sky_nodes

CLOUD_ICON, FOG_ICON = "OUTLINER_OB_VOLUME", "MOD_FLUID"
KIND_ICON = {"SKY": "WORLD", "DECAL": CLOUD_ICON, "FOG": FOG_ICON,
             "SUN_IMAGE": "IMAGE_DATA", "SUN_COLOR": "COLOR"}
SUN_ICON = "LIGHT_SUN"
LAYER_ICON = {"SKY": "RENDERLAYERS", "SUN": SUN_ICON}
IMAGE_KINDS = ("SKY", "DECAL", "FOG", "SUN_IMAGE")
MOD_ICON = {k: icon for k, _n, _d, icon, _i in MODIFIER_KINDS}
PATTERN_ICON, GRADIENT_ICON = "TEXTURE", "IPO_EASE_OUT"
NOISE_MASKS = ("NOISE", "VORONOI", "WAVE", "MAGIC", "GABOR")
MASK_ICON = {"IMAGE": "IMAGE_DATA", "GRADIENT": GRADIENT_ICON,
             **{k: PATTERN_ICON for k in NOISE_MASKS}}
MASK_LABEL = {k: n for k, n, *_rest in MASK_KINDS}
MASK_MOD_OWNER = {"LAYER": "LAYER_MASK", "ITEM": "ITEM_MASK"}
SLIDERS = {"opacity", "softness", "blend_width", "intensity", "factor", "hue", "edge_roughness",
           "edge_randomness", "edge_anisotropy", "edge_softness", "fog_density",
           "roughness", "randomness", "anisotropy",
           "cloud_scale", "sun_time", "sun_relight_amount", "sun_relight_disc_amount", "sun_night"}
MOD_PROPS = {"BRIGHT_CONTRAST": ("bright", "contrast"),
             "HUE_SAT": ("hue", "saturation", "value"), "GAMMA": ("gamma",),
             "MATH": (("math_op=", "math_value"), "math_clamp"), "MULTIPLIER": ("multiplier",)}
ALPHA_LABEL = {k: (n, icon) for k, n, _d, icon, _i in ALPHA_SOURCES}
EDGE_PROPS = (("edge_detail", {"NOISE", "VORONOI", "WAVE"}),
              ("edge_roughness", {"NOISE", "VORONOI", "WAVE"}),
              ("edge_distortion", {"NOISE", "WAVE", "MAGIC"}), ("edge_randomness", {"VORONOI"}),
              ("edge_frequency", {"GABOR"}), ("edge_anisotropy", {"GABOR"}))
MASK_PATTERN_PROPS = tuple((n[len("edge_"):], kinds) for n, kinds in EDGE_PROPS)


SUN_LABEL = {"SUN_COLOR": "Solid Color"}


def item_label(it):
    if it.image is not None and it.kind != "SUN_COLOR":
        return it.image.name
    return it.name or SUN_LABEL.get(it.kind) or ("Fog" if it.kind == "FOG" else "(no image)")


def look_row(layout, it, color):
    split = layout.split(factor=0.36, align=True)
    split.prop(it, "blend", text="")
    split = split.split(factor=0.8, align=True)
    split.prop(it, "opacity", slider=True)
    split.prop(it, color, text="")
    return layout


def props(layout, data, *names):
    for n in names:
        if isinstance(n, tuple):
            props(layout.row(align=True), data, *n)
            continue
        n, eq, text = n.partition("=")
        kw = {"text": text} if eq else {}
        layout.prop(data, n, slider=n in SLIDERS, **kw)
    return layout


def arrow(layout, data, prop):
    layout.prop(data, prop, text="", emboss=False,
                icon="DOWNARROW_HLT" if getattr(data, prop) else "RIGHTARROW")


def section(layout, ui, prop, title, icon):
    layout.separator(factor=0.4)
    box = layout.box()
    row = box.row(align=True)
    arrow(row, ui, prop)
    row.label(text=title, icon=icon)
    if getattr(ui, prop):
        col = box.column()
        col.separator(factor=0.2)
        return col


def sub_box(layout, title=None, icon="NONE"):
    col = layout.box().column()
    if title:
        col.label(text=title, icon=icon)
    return col


def toggle_row(layout, ui, prop, title, icon, count=None):
    row = layout.row(align=True)
    arrow(row, ui, prop)
    row.label(text=title if count is None else "%s (%d)" % (title, count), icon=icon)
    return getattr(ui, prop)


def eye(row, item):
    row.prop(item, "enabled", text="", emboss=False, icon="HIDE_OFF" if item.enabled else "HIDE_ON")


def list_side(col, add_menu, prefix):
    col.menu(add_menu, text="", icon="ADD")
    col.operator("dangus.remove_" + prefix, text="", icon="REMOVE")
    col.operator("dangus.duplicate_" + prefix, text="", icon="DUPLICATE")
    col.separator()
    for d in ("UP", "DOWN"):
        col.operator("dangus.move_" + prefix, text="", icon="TRIA_" + d).direction = d


class DANGUS_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon=LAYER_ICON[item.kind])
        eye(row, item)


class DANGUS_UL_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        owner = item.image if item.image is not None and item.kind != "SUN_COLOR" else item
        if owner == item and not item.name:
            row.label(text=item_label(item), icon=KIND_ICON[item.kind])
        else:
            row.prop(owner, "name", text="", emboss=False, icon=KIND_ICON[item.kind])
        if item.kind != "SKY":
            row.prop(item, "opacity", text="", slider=True)


class DANGUS_UL_masks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.image.name if item.kind == "IMAGE" and item.image
                  else MASK_LABEL[item.kind], icon=MASK_ICON[item.kind])
        row.prop(item, "invert", text="", icon="ARROW_LEFTRIGHT", emboss=False)
        eye(row, item)


def menu(name, label, draw):
    return type(name, (bpy.types.Menu,), {"bl_idname": name, "bl_label": label, "draw": draw})


def _draw_add_layer(self, context):
    for kind, title in (("SKY", "Sky Layer"), ("SUN", "Sun Layer")):
        self.layout.operator("dangus.add_layer", text=title, icon=LAYER_ICON[kind]).kind = kind


def _draw_add_mask(layout, target):
    layout.label(text="Image", icon="IMAGE_DATA")
    for op, text, icon in (("add_mask_image", "Open Image Files...", "FILEBROWSER"),
                           ("add_existing_mask_image", "Use Existing Image...", "IMAGE_DATA"),
                           ("new_mask_image", "New Image...", "FILE_NEW")):
        layout.operator("dangus." + op, text=text, icon=icon).target = target
    layout.separator()
    layout.label(text="Noise", icon=PATTERN_ICON)
    for kind in NOISE_MASKS:
        op = layout.operator("dangus.add_mask", text=MASK_LABEL[kind])
        op.target, op.kind = target, kind
    layout.separator()
    op = layout.operator("dangus.add_mask", text="Gradient", icon=GRADIENT_ICON)
    op.target, op.kind = target, "GRADIENT"


def _draw_add_layer_mask(self, context):
    _draw_add_mask(self.layout, "LAYER")


def _draw_add_item_mask(self, context):
    _draw_add_mask(self.layout, "ITEM")


def _draw_add_image(self, context):
    layout = self.layout
    L = active_layer(context.scene.dangus)
    sun = L is not None and L.kind == "SUN"
    kinds = (("SUN_IMAGE", "Sun"),) if sun else (
        ("SKY", "Sky"), ("DECAL", "Cloud (decal)"), ("FOG", "Fog"))
    for n, (kind, title) in enumerate(kinds):
        if n:
            layout.separator()
        layout.label(text=title, icon=SUN_ICON if kind == "SUN_IMAGE" else KIND_ICON[kind])
        if kind == "FOG":
            layout.operator("dangus.add_fog", text="Gradient Fog", icon="IPO_EASE_OUT")
        for op, text, icon in (("add_image", "Open Image Files...", "FILEBROWSER"),
                               ("add_existing_image", "Use Existing Image...", "IMAGE_DATA"),
                               ("new_image", "New Image...", "FILE_NEW")):
            layout.operator("dangus." + op, text=text, icon=icon).kind = kind
    if sun:
        layout.separator()
        layout.label(text="Color", icon=KIND_ICON["SUN_COLOR"])
        layout.operator("dangus.add_sun_item", text="Solid Color", icon="ADD")


def _draw_alpha_source(self, context):
    layout = self.layout
    for key in ("CHANNEL", "LUMINANCE", None, "IMAGE", "NEW", None, "NONE"):
        if key is None:
            layout.separator()
        elif key == "NEW":
            layout.operator("dangus.new_alpha_image", text="New Image...", icon="FILE_NEW")
        else:
            text, icon = ALPHA_LABEL[key]
            layout.operator("dangus.set_alpha_source", text=text, icon=icon).source = key


def _draw_item_specials(self, context):
    layout = self.layout
    it = active_item(active_layer(context.scene.dangus))
    layout.operator("dangus.replace_image", icon="FILEBROWSER")
    layout.operator("dangus.reload_image", icon="FILE_REFRESH")
    if it is not None and it.kind == "SUN_IMAGE":
        return
    layout.separator()
    for kind, title in (("SKY", "Sky"), ("DECAL", "Cloud"), ("FOG", "Fog")):
        if it is None or it.kind != kind:
            layout.operator("dangus.set_kind", text="Convert to " + title,
                            icon=KIND_ICON[kind]).kind = kind


def _draw_edge_presets(self, context):
    layout = self.layout
    layout.label(text="Built-in", icon="PRESET")
    for name in presets.BUILTIN:
        layout.operator("dangus.edge_preset_apply", text=name).name = name
    mine = presets.user_presets()
    if mine:
        layout.separator()
        layout.label(text="Saved", icon="FILE_TICK")
        for name in mine:
            row = layout.row(align=True)
            row.operator("dangus.edge_preset_apply", text=name).name = name
            row.operator("dangus.edge_preset_remove", text="", icon="X").name = name
    layout.separator()
    layout.operator("dangus.edge_preset_save", text="Save Current...", icon="ADD")


def draw_masks(layout, context, cfg, target, ui):
    owner, coll, idx = mask_stack(cfg, target)
    row = layout.row()
    row.template_list("DANGUS_UL_masks", target, owner, coll, owner, idx, rows=2)
    side = row.column(align=True)
    side.menu("DANGUS_MT_add_layer_mask" if target == "LAYER" else "DANGUS_MT_add_item_mask",
              text="", icon="ADD")
    side.operator("dangus.remove_mask", icon="REMOVE", text="").target = target
    masks, i = getattr(owner, coll), getattr(owner, idx)
    if not 0 <= i < len(masks):
        return
    m = masks[i]
    col = sub_box(layout)
    if m.kind == "IMAGE":
        col.template_ID(m, "image", open="image.open")
    props(col, m, ("angle_from", "angle_to") if m.kind == "GRADIENT" else "scale",
          *(n for n, kinds in MASK_PATTERN_PROPS if m.kind in kinds),
          *(() if m.kind == "GRADIENT" else (("low", "high"),)), "intensity",
          *(("blend=Combine",) if i else ()))
    if toggle_row(col, ui, "show_mask_modifiers", "Mask Modifiers", "MODIFIER", len(m.modifiers)):
        draw_modifiers(col, context, m.modifiers, MASK_MOD_OWNER[target])


def _template(col, node, what, kind):
    if node is None:
        col.label(text="Build the sky to edit the " + what, icon="INFO")
    elif kind == "RAMP":
        col.template_color_ramp(node, "color_ramp", expand=True)
    else:
        col.template_curve_mapping(node, "mapping", type="COLOR")


def draw_modifiers(layout, context, mods, owner):
    layout.operator_menu_enum("dangus.add_modifier", "kind", text="Add Modifier",
                              icon="ADD").owner = owner
    L = active_layer(context.scene.dangus)
    g = L.tree if L else None
    for i, m in enumerate(mods):
        box = layout.box()
        row = box.row(align=True)
        arrow(row, m, "show_expanded")
        row.prop(m, "enabled", text="")
        row.label(text=m.bl_rna.properties["kind"].enum_items[m.kind].name, icon=MOD_ICON[m.kind])
        for d in ("UP", "DOWN"):
            op = row.operator("dangus.move_modifier", text="", icon="TRIA_" + d, emboss=False)
            op.owner, op.index, op.direction = owner, i, d
        op = row.operator("dangus.remove_modifier", text="", icon="X", emboss=False)
        op.owner, op.index = owner, i
        if m.show_expanded:
            col = box.column()
            col.active = m.enabled
            props(col, m, "factor", *MOD_PROPS.get(m.kind, ()))
            if m.kind == "RAMP":
                _template(col, nodes_builder.ramp_node(g, m.uid), "ramp", "RAMP")
            elif m.kind == "CURVES":
                _template(col, nodes_builder.curves_node(g, m.uid), "curves", "CURVES")


def draw_sun(layout, context, L, ui):
    body = section(layout, ui, "show_sun_position", "Position", "LIGHT_SUN")
    if body:
        body.row().prop(L, "sun_mode", expand=True)
        if L.sun_mode == "TIME":
            props(body, L, "sun_time", "sun_latitude", "sun_day", "sun_north")
        else:
            props(body, L, ("sun_azimuth", "sun_elevation"))
    body = section(layout, ui, "show_sun_disc", "Disc & Glow", "SHADING_RENDERED")
    if body:
        props(body, L, "sun_size", "sun_softness", "sun_glow", "sun_glow_size")
    body = section(layout, ui, "show_sun_brightness", "Brightness", "LIGHT")
    if body:
        props(body, L, "sun_strength", "sun_cloud_strength", "sun_scatter")
    body = section(layout, ui, "show_sun_relight", "Relight", "WORLD")
    if body:
        for flag, extra in (("sun_relight_sky", ("sun_relight_amount", "sun_night")),
                            ("sun_relight_disc", ("sun_relight_disc_amount",))):
            box = sub_box(body)
            box.prop(L, flag)
            col = props(box.column(), L, *extra)
            col.active = getattr(L, flag)
        col = props(body.column(), L, "sun_haze")
        col.active = L.sun_relight_sky or L.sun_relight_disc


def draw_layers(layout, context, cfg, ui):
    row = layout.row()
    row.template_list("DANGUS_UL_layers", "", cfg, "layers", cfg, "active_layer", rows=3)
    list_side(row.column(align=True), "DANGUS_MT_add_layer", "layer")
    L = active_layer(cfg)
    if L is None:
        return
    col = props(sub_box(layout), L, ("blend=", "opacity"))
    if L.kind == "SKY":
        col.prop(L, "sky_spin")
    else:
        draw_sun(layout, context, L, ui)
    layout.separator(factor=0.3)
    if toggle_row(layout, ui, "show_layer_masks", "Layer Masks", "MOD_MASK", len(L.masks)):
        draw_masks(layout.box().column(), context, cfg, "LAYER", ui)


def draw_cloud(layout, it):
    col = sub_box(layout, "Placement", "EMPTY_ARROWS")
    row = col.row(align=True)
    row.scale_y = 1.2
    row.operator("dangus.place_cloud", text="Place", icon="CURSOR")
    row.operator("dangus.select_cloud", text="Select Empty", icon="RESTRICT_SELECT_OFF")
    col.prop(it, "decal_object", text="")
    ob = it.decal_object
    if ob is not None:
        col.prop(it, "cloud_scale", slider=True)
        if ob.matrix_world.translation.length < 1e-6:
            col.label(text="Empty is at the origin: Place it", icon="ERROR")
    props(look_row(sub_box(layout, "Look", "SHADING_RENDERED"), it, "tint"), it, "softness")


def draw_images(layout, context, cfg, ui):
    L = active_layer(cfg)
    if L is None:
        layout.label(text="Add a Sky Layer first", icon="INFO")
        return
    row = layout.row()
    row.template_list("DANGUS_UL_items", "", L, "items", L, "active_item", rows=4)
    list_side(row.column(align=True), "DANGUS_MT_add_image", "image")
    row = layout.row(align=True)
    if L.kind == "SUN":
        row.operator("dangus.add_image", text="Sun", icon=SUN_ICON).kind = "SUN_IMAGE"
    else:
        for kind, text in (("SKY", "Sky"), ("DECAL", "Cloud"), ("FOG", "Fog")):
            row.operator("dangus.add_image", text=text, icon=KIND_ICON[kind]).kind = kind
    it = active_item(L)
    if it is None:
        return
    layout.separator(factor=0.3)
    box = layout.box()
    row = box.row(align=True)
    arrow(row, ui, "show_item")
    row.label(text=item_label(it), icon=KIND_ICON[it.kind])
    has_image = it.kind in IMAGE_KINDS
    if has_image:
        row.menu("DANGUS_MT_item_specials", text="", icon="MODIFIER")
    if not ui.show_item:
        return
    col = box.column()
    if has_image:
        col.row().prop(ui, "item_tab", expand=True)
        col.separator(factor=0.3)
        if ui.item_tab == "SOURCE":
            draw_source(col, context, it)
            return
    sun = it.kind in ("SUN_IMAGE", "SUN_COLOR")
    if sun:
        draw_sun_item(col, it)
    elif it.kind == "SKY":
        box = sub_box(col)
        box.prop(it, "panorama")
        if not it.panorama:
            box.prop(it, "blend_width", slider=True)
    elif it.kind == "FOG":
        draw_fog(col, it)
    else:
        draw_cloud(col, it)
    if not sun:
        col.separator(factor=0.3)
        if toggle_row(col, ui, "show_item_masks", "Image Masks", "MOD_MASK", len(it.masks)):
            draw_masks(col.box().column(), context, cfg, "ITEM", ui)
    if it.kind in ("SKY", "DECAL", "SUN_IMAGE") or (it.kind == "FOG" and it.fog_mode == "TEXTURE"
                                                    and it.image is not None):
        text, icon = ALPHA_LABEL[it.alpha_source]
        if toggle_row(col, ui, "show_item_alpha", "Alpha: " + text, "IMAGE_ALPHA"):
            draw_alpha(col.box().column(), context, it)
    if toggle_row(col, ui, "show_item_modifiers", "Image Modifiers", "MODIFIER",
                  len(it.modifiers)):
        draw_modifiers(col.box().column(), context, it.modifiers, "ITEM")


def draw_alpha(layout, context, it):
    text, icon = ALPHA_LABEL[it.alpha_source]
    layout.menu("DANGUS_MT_alpha_source", text=text, icon=icon)
    if it.alpha_source == "IMAGE":
        layout.template_ID(it, "alpha_image", new="dangus.new_alpha_image", open="image.open")
    elif it.alpha_source == "LUMINANCE":
        layout.label(text="White = opaque", icon="INFO")
    layout.label(text="Alpha Modifiers (%d)" % len(it.alpha_modifiers), icon="MODIFIER")
    draw_modifiers(layout, context, it.alpha_modifiers, "ALPHA")


def draw_sun_item(layout, it):
    k = it.kind
    box = look_row(sub_box(layout, "Look", "SHADING_RENDERED"), it,
                   "solid_color" if k == "SUN_COLOR" else "tint")
    if k == "SUN_IMAGE":
        box.prop(it, "emission")


def draw_source(layout, context, it):
    layout.template_ID(it, "image", new="image.new", open="image.open")
    col = sub_box(layout, "Image Texture", "TEXTURE")
    img = it.image
    if img is not None:
        col.prop(img, "source", text="")
        if img.source == "GENERATED":
            props(col.column(align=True), img, "generated_width=X", "generated_height=Y")
            col.prop(img, "use_generated_float")
            col.row().prop(img, "generated_type", expand=True)
            if img.generated_type == "BLANK":
                col.prop(img, "generated_color", text="")
        if img.source in {"SEQUENCE", "MOVIE"}:
            props(col.column(align=True), it, "frame_duration", "frame_start", "frame_offset")
            props(col.row(), it, "use_cyclic", "use_auto_refresh")
            if img.source == "MOVIE":
                col.prop(img, "use_deinterlace")
        labelled(col, "Color Space:", img.colorspace_settings, "name")
        labelled(col, "Alpha:", img, "alpha_mode")
        if img.is_float:
            col.prop(img, "use_half_precision")
        if context.scene.render.use_multiview:
            col.prop(img, "use_multiview")
            if img.use_multiview:
                col.prop(img, "views_format", expand=True)
    props(col, it, "tex_interpolation=", "tex_projection=", "tex_extension=")
    draw_mapping(layout, it)


def labelled(layout, text, data, prop):
    split = layout.split(factor=0.5)
    split.label(text=text)
    split.prop(data, prop, text="")


def draw_mapping(layout, it):
    col = sub_box(layout, "Mapping", "ORIENTATION_GLOBAL")
    if it.tex_projection == "BOX":
        labelled(col, "Projection Blend:", it, "tex_projection_blend")
    labelled(col, "Transform:", it, "map_type")
    row = col.row()
    for title, prop in (("Offset:", "map_location"), ("Rotation:", "map_rotation"),
                        ("Scale:", "map_scale")):
        c = row.column(align=True)
        head = c.row(align=True)
        head.label(text=title)
        if prop == "map_scale":
            head.prop(it, "map_scale_lock", text="", emboss=False,
                      icon="LOCKED" if it.map_scale_lock else "UNLOCKED")
        c.prop(it, prop, text="")


def draw_edge(layout, cfg):
    row = layout.row(align=True)
    row.menu("DANGUS_MT_edge_presets", text="Presets", icon="PRESET")
    row.operator("dangus.edge_preset_save", text="", icon="ADD")
    props(sub_box(layout, "Pattern", "TEXTURE"), cfg, "edge_noise=", "noise_scale",
          *(n for n, kinds in EDGE_PROPS if cfg.edge_noise in kinds))
    props(sub_box(layout, "Dissolve", "IPO_EASE_IN_OUT"), cfg, ("edge_low", "edge_high"),
          "edge_softness")


def draw_dome(layout, cfg):
    props(sub_box(layout), cfg, "strength", "sky_rotation")
    props(sub_box(layout, "Vertical", "SORT_DESC"), cfg, "vertical_mode=",
          *(("sky_bottom", "sky_top") if cfg.vertical_mode == "FULL" else
            ("sky_horizon", "z_scale")))
    props(sub_box(layout, "Ground", "VIEW_PERSPECTIVE"), cfg, "ground_color=", "ground_blur")


def draw_fog(layout, it):
    layout.row().prop(it, "fog_mode", expand=True)
    props(look_row(sub_box(layout, "Look", "SHADING_RENDERED"), it, "fog_color"), it,
          "fog_density")
    if it.fog_mode == "TEXTURE":
        tex = sub_box(layout, "Texture", "TEXTURE")
        if it.image is None:
            tex.label(text="Pick an image in Source (a gradient until then)", icon="INFO")
        tex.row().prop(it, "fog_fit", expand=True)
        props(tex, it, *(("fog_tile",) if it.fog_fit == "REPEAT" else ()), "fog_rotation")
    props(sub_box(layout, "Height", "SORT_ASC"), it, "fog_top", "fog_fade", "fog_falloff=")


def draw_culling(layout, cfg):
    sub_box(layout, "Cycles Light Map", "LIGHT").prop(cfg, "light_map", text="")
    box = layout.box()
    box.prop(cfg, "cull_camera")
    col = box.column()
    col.active = cfg.cull_camera
    props(col, cfg, "cull_margin", "cull_animation").label(
        text="Rebuild after moving the camera", icon="CAMERA_DATA")


def draw_bake(layout, context, cfg, ui):
    layout.row().prop(cfg, "bake_kind", expand=True)
    col = props(sub_box(layout, "Output", "OUTPUT"), cfg, "bake_path=")
    if cfg.bake_kind == "IMAGE":
        props(col, cfg, "bake_format=", "bake_animation")
    else:
        props(col, cfg, "bake_video=")
        if cfg.bake_video not in ("MOV_PRORES", "MKV_FFV1"):
            col.prop(cfg, "bake_quality", text="")
    sc = context.scene
    if cfg.bake_kind == "VIDEO" or cfg.bake_animation:
        col.label(text="Frames %d-%d at %g fps" % (sc.frame_start, sc.frame_end,
                                                  round(sc.render.fps / sc.render.fps_base, 3)),
                  icon="TIME")
    w, h = bake_size(cfg)
    props(sub_box(layout, "Render", "RENDER_STILL"), cfg, "bake_width", "bake_samples").label(
        text="%d x %d px, equirectangular" % (w, h))
    layout.prop(cfg, "bake_load")
    if ui.baking:
        layout.progress(factor=ui.bake_progress, type="BAR", text=ui.bake_status)
        layout.label(text="Esc cancels", icon="CANCEL")
        return
    row = layout.row()
    row.scale_y = 1.3
    row.operator("dangus.bake_sky", icon="RENDER_ANIMATION" if cfg.bake_kind == "VIDEO" or
                 cfg.bake_animation else "RENDER_STILL")
    layout.operator("dangus.load_panorama", text="Load Panorama...", icon="FILEBROWSER")


SECTIONS = (("show_layers", "Sky Layers", "RENDERLAYERS", draw_layers, False),
            ("show_images", "Images", "IMAGE_DATA", draw_images, False),
            ("show_edge", "Edge Mix", "MOD_NOISE", lambda l, c, cfg, u: draw_edge(l, cfg), True),
            ("show_dome", "Dome", "WORLD_DATA", lambda l, c, cfg, u: draw_dome(l, cfg), True),
            ("show_culling", "Performance", "MOD_TIME",
             lambda l, c, cfg, u: draw_culling(l, cfg), False),
            ("show_bake", "Bake", "RENDER_STILL", lambda l, c, cfg, u: draw_bake(l, c, cfg, u), False))


class DANGUS_PT_main(bpy.types.Panel):
    bl_label = "dangus %s" % VERSION
    bl_idname = "DANGUS_PT_main"
    bl_category = "dangus"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        cfg, ui = context.scene.dangus, context.window_manager.dangus_ui
        row = layout.row()
        if has_sky_nodes(context.scene.world):
            row.label(text="Edits apply instantly")
            sub = row.row(align=True)
            sub.alignment = "RIGHT"
            sub.operator("dangus.build", text="", icon="FILE_REFRESH")
            sub.operator("dangus.clear", text="", icon="X")
        else:
            row.scale_y = 1.4
            row.operator("dangus.build", text="Build Sky", icon="NODETREE")
        it = active_item(active_layer(cfg))
        sky = it is not None and it.kind == "SKY"
        for prop, title, icon, draw, sky_only in SECTIONS:
            body = None if sky_only and not sky else section(layout, ui, prop, title, icon)
            if body:
                draw(body, context, cfg, ui)


classes = (DANGUS_UL_layers, DANGUS_UL_items, DANGUS_UL_masks,
           menu("DANGUS_MT_add_layer", "New Layer", _draw_add_layer),
           menu("DANGUS_MT_add_layer_mask", "Add Layer Mask", _draw_add_layer_mask),
           menu("DANGUS_MT_add_item_mask", "Add Mask", _draw_add_item_mask),
           menu("DANGUS_MT_add_image", "Add Image", _draw_add_image),
           menu("DANGUS_MT_item_specials", "Image", _draw_item_specials),
           menu("DANGUS_MT_alpha_source", "Alpha", _draw_alpha_source),
           menu("DANGUS_MT_edge_presets", "Edge Mix Presets", _draw_edge_presets),
           DANGUS_PT_main)
