import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smoke_test import enable, make_image

ICONS = {e.identifier for e in
         bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items}
CALLS = []
UI = None


def check_icon(kw):
    icon = kw.get("icon")
    assert icon is None or icon in ICONS, "unknown icon %r" % icon


def check_prop(data, prop):
    assert data is not None, "drawing a property of None"
    assert prop in data.bl_rna.properties, "%s has no property %r" % (
        data.bl_rna.identifier, prop)


class OpProps:
    def __init__(self, idname):
        mod, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, mod), name).get_rna_type()
        object.__setattr__(self, "_props", rna.properties)
        object.__setattr__(self, "_id", idname)

    def __setattr__(self, key, value):
        assert key in self._props, "%s has no property %r" % (self._id, key)


FUNCS = bpy.types.UILayout.bl_rna.functions
CONTAINERS = {"row", "column", "box", "split", "column_flow", "grid_flow"}
MENUS_DRAWN = set()


class Layout:

    def __init__(self):
        self.__dict__.update(active=True, enabled=True, scale_x=1.0, scale_y=1.0,
                             alert=False, alignment="EXPAND", use_property_split=False,
                             use_property_decorate=False, emboss="NORMAL")

    def __setattr__(self, key, value):
        assert key in bpy.types.UILayout.bl_rna.properties, "UILayout has no %r" % key
        self.__dict__[key] = value

    def __getattr__(self, name):
        assert name in FUNCS, "UILayout has no function %r" % name
        params = [p.identifier for p in FUNCS[name].parameters]

        def call(*args, **kw):
            for k in kw:
                assert k in params, "%s() has no argument %r" % (name, k)
            kw.update(zip(params, args))
            check_icon(kw)
            CALLS.append((name, kw.get("operator") or kw.get("property") or kw.get("menu")))
            return self._handle(name, kw)
        return call

    def _handle(self, name, kw):
        if name in CONTAINERS:
            return Layout()
        if name in {"prop", "prop_menu_enum", "template_ID", "template_color_ramp"}:
            check_prop(kw["data"], kw["property"])
        if name in {"operator", "operator_menu_enum"}:
            props = OpProps(kw["operator"])
            if name == "operator_menu_enum":
                assert kw["property"] in props._props, kw["property"]
            return props
        if name == "template_ID" and kw.get("open"):
            OpProps(kw["open"])
        if name == "menu":
            cls = getattr(bpy.types, kw["menu"], None)
            assert cls is not None, "unknown menu %r" % kw["menu"]
            if kw["menu"] not in MENUS_DRAWN:
                MENUS_DRAWN.add(kw["menu"])
                cls.draw(Fake(), bpy.context)
        if name == "template_list":
            listtype = kw["listtype_name"]
            assert listtype in dir(bpy.types), "unknown UIList %r" % listtype
            data, prop = kw["dataptr"], kw["propname"]
            check_prop(data, prop)
            check_prop(kw["active_dataptr"], kw["active_propname"])
            cls = getattr(bpy.types, listtype)
            for i, item in enumerate(getattr(data, prop)):
                cls.draw_item(None, bpy.context, Layout(), data, item, 0,
                              kw["active_dataptr"], kw["active_propname"], i)
        return None


class Fake:
    def __init__(self):
        self.layout = Layout()


def draw_all():
    MENUS_DRAWN.clear()
    for cls in UI.classes:
        if issubclass(cls, (bpy.types.Panel, bpy.types.Menu)):
            if hasattr(cls, "poll") and not cls.poll(bpy.context):
                continue
            for hook in ("draw_header", "draw_header_preset"):
                if hasattr(cls, hook):
                    getattr(cls, hook)(Fake(), bpy.context)
            cls.draw(Fake(), bpy.context)


def recorded(fn):
    start = len(CALLS)
    MENUS_DRAWN.clear()
    fn()
    return CALLS[start:]


def v04_checks(cfg, L, cloud, ui):
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, True)
    L.active_item = list(L.items).index(cloud)
    props = {c[1] for c in recorded(draw_all)}
    assert "edge_noise" not in props and "sky_rotation" not in props, "sky sections for a cloud"
    assert "cloud_scale" in props, "cloud Scale slider missing"
    L.active_item = 0
    props = {c[1] for c in recorded(draw_all)}
    assert "edge_noise" in props and "sky_rotation" in props, "sky sections missing for a sky"
    assert "cloud_distance" not in props

    def ops_of(menu):
        start = len(CALLS)
        getattr(bpy.types, menu).draw(Fake(), bpy.context)
        return [op for name, op in CALLS[start:] if name in ("operator", "operator_menu_enum")]

    side = {"dangus.add_layer", "dangus.remove_layer", "dangus.duplicate_layer",
            "dangus.move_layer", "dangus.remove_image", "dangus.duplicate_image",
            "dangus.move_image"}
    for menu in ("DANGUS_MT_item_specials", "DANGUS_MT_add_image"):
        ops = ops_of(menu)
        assert not side & set(ops), "%s repeats list buttons: %s" % (menu, side & set(ops))
    wrench = [o for o in ops_of("DANGUS_MT_item_specials") if o != "dangus.set_kind"]
    assert len(wrench) == len(set(wrench)), wrench
    assert not hasattr(bpy.types, "DANGUS_MT_layer_specials"), "layer menu only repeated buttons"

    for owner, prop in ((cfg, "active_layer"), (L, "active_item"), (L, "active_mask"),
                        (cloud, "active_mask")):
        rna = owner.bl_rna.properties[prop]
        assert rna.name and rna.name != prop and rna.description, "%s shows as %r" % (prop, prop)


def v05_checks(cfg, L, cloud, ui):
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, True)
    seen = set()
    for active in (0, list(L.items).index(cloud)):
        L.active_item = active
        img = L.items[active].image
        for tab in ("SETTINGS", "SOURCE"):
            ui.item_tab = tab
            props = {c[1] for c in recorded(draw_all)}
            if tab == "SOURCE":
                assert {"tex_interpolation", "tex_projection", "tex_extension", "source",
                        "name", "alpha_mode", "map_location", "map_rotation",
                        "map_scale"} <= props, props
                assert "blend_width" not in props and "cloud_scale" not in props
                seen |= props
            else:
                assert "tex_interpolation" not in props
                assert ("panorama" in props) == (L.items[active].kind == "SKY")
        ui.item_tab = "SOURCE"
        for source in ("GENERATED", "FILE", "SEQUENCE", "MOVIE"):
            img.source = source
            seen |= {c[1] for c in recorded(draw_all)}
        img.source = "GENERATED"
        L.items[active].tex_projection = "BOX"
        seen |= {c[1] for c in recorded(draw_all)}
        L.items[active].tex_projection = "FLAT"
    for k in ("generated_width", "generated_type", "source", "frame_duration", "use_cyclic",
              "use_deinterlace", "tex_projection_blend", "map_type", "map_scale_lock",
              "map_location", "map_rotation", "map_scale"):
        assert k in seen, "Source tab never drew %s" % k
    assert "filepath" not in seen, "the file path is in the image picker only"
    ui.item_tab = "SETTINGS"

    start = len(CALLS)
    bpy.types.DANGUS_MT_add_image.draw(Fake(), bpy.context)
    menu = [op for name, op in CALLS[start:] if name == "operator"]
    assert menu.count("dangus.new_image") == 3 and menu.count("dangus.add_fog") == 1, menu


def v05_fog_sun_modifiers(cfg, L, cloud, ui):
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, True)
    ui.item_tab = "SETTINGS"
    assert bpy.ops.dangus.add_fog() == {"FINISHED"}
    fog = L.items[L.active_item]
    assert fog.kind == "FOG" and fog.image is None
    for image in (None, L.items[0].image):
        fog.image = image
        for mode in ("GRADIENT", "TEXTURE"):
            fog.fog_mode = mode
            for fit in ("STRETCH", "FILL", "REPEAT"):
                fog.fog_fit = fit
                props = {c[1] for c in recorded(draw_all)}
                assert "fog_top" in props and "fog_density" in props
                assert "edge_noise" not in props, "Edge Mix is for skies"
                assert ("fog_tile" in props) == (mode == "TEXTURE" and fit == "REPEAT")
    ui.item_tab = "SOURCE"
    props = {c[1] for c in recorded(draw_all)}
    assert "tex_interpolation" in props
    ui.item_tab = "SETTINGS"

    bpy.ops.dangus.build()
    kinds = [k[0] for k in UI.MODIFIER_KINDS]
    L.active_item = list(L.items).index(cloud)
    if not cloud.masks:
        cloud.masks.add().kind = "NOISE"
    L.masks.add().kind = "GRADIENT"
    for owner in ("ITEM", "ITEM_MASK", "LAYER_MASK"):
        for k in kinds:
            assert bpy.ops.dangus.add_modifier(owner=owner, kind=k) == {"FINISHED"}, (owner, k)
    calls = recorded(draw_all)
    bpy.ops.dangus.build()
    calls = recorded(draw_all)
    names = [c[0] for c in calls]
    assert names.count("template_curve_mapping") >= 3, "curves not drawn"
    assert "template_color_ramp" in names
    ops = {c[1] for c in calls}
    assert {"dangus.add_modifier", "dangus.remove_modifier", "dangus.move_modifier"} <= ops
    props = {c[1] for c in calls}
    for p in ("factor", "gamma", "hue", "multiplier", "math_op", "math_value", "math_clamp"):
        assert p in props, "%s not drawn" % p
    for gone in ("alpha_multiplier", "math_alpha", "invert_alpha", "alpha_from_luminance",
                 "use_alpha", "use_view_as_render", "sun_img_size"):
        assert gone not in props, "%s is a removed alpha duplicate" % gone
    assert {"dangus.set_alpha_source", "dangus.new_alpha_image"} <= ops, "alpha menu missing"
    for src in ("CHANNEL", "LUMINANCE", "IMAGE", "NONE"):
        cloud.alpha_source = src
        calls = recorded(draw_all)
        assert ("alpha_image" in {c[1] for c in calls}) == (src == "IMAGE")
    assert bpy.ops.dangus.add_modifier(owner="ALPHA", kind="RAMP") == {"FINISHED"}
    assert len(cloud.alpha_modifiers) == 1
    cloud.alpha_source = "CHANNEL"
    assert "sun_strength" not in props, "a sky layer has no sun settings any more"
    for m in cloud.modifiers:
        m.show_expanded = False
    recorded(draw_all)
    v06_sun_layer(cfg, ui)


def v06_sun_layer(cfg, ui):
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, True)
    sky_layer = cfg.layers[cfg.active_layer]
    start = len(CALLS)
    bpy.types.DANGUS_MT_add_layer_mask.draw(Fake(), bpy.context)
    kinds = [c for c in CALLS[start:] if c[0] == "operator"]
    assert len(kinds) == 3, "layer masks: Noise, Cells, Fade only"
    assert bpy.ops.dangus.add_layer(kind="SUN") == {"FINISHED"}
    S = cfg.layers[cfg.active_layer]
    assert S.kind == "SUN" and S.name.startswith("Sun") and S.blend == "ADD"
    assert [it.kind for it in S.items] == ["SUN_COLOR"], "a new sun starts with a Solid Color"
    assert S.sun_mode == "TIME", "a new sun is placed by the time of day"
    assert bpy.ops.dangus.new_image(kind="SUN_IMAGE", width=32, height=32) == {"FINISHED"}
    calls = recorded(draw_all)
    props = {c[1] for c in calls}
    for p in ("sun_mode", "sun_time", "sun_latitude", "sun_day", "sun_north", "sun_strength",
              "sun_cloud_strength", "sun_scatter", "sun_size", "sun_glow", "sun_relight_sky",
              "sun_relight_amount", "sun_relight_disc", "sun_relight_disc_amount", "sun_night", "sun_haze"):
        assert p in props, "sun layer: %s not drawn" % p
    assert "sun_azimuth" not in props and "sun_object" not in props
    assert not {"sun_match", "sun_light", "sun_light_strength"} & props, "experimental removed"
    S.sun_mode = "ANGLES"
    props = {c[1] for c in recorded(draw_all)}
    assert "sun_azimuth" in props and "sun_time" not in props
    S.sun_mode = "TIME"
    start = len(CALLS)
    bpy.types.DANGUS_MT_add_image.draw(Fake(), bpy.context)
    menu = [(name, op) for name, op in CALLS[start:] if name in ("operator", "label")]
    assert [op for _n, op in menu if op] == ["dangus.add_image", "dangus.add_existing_image",
                                             "dangus.new_image", "dangus.add_sun_item"], menu
    assert [n for n, _op in menu].count("label") == 2, "sections with headers, like the sky menu"
    for i, kind in enumerate(("SUN_COLOR", "SUN_IMAGE")):
        S.active_item = i
        assert S.items[i].kind == kind
        for tab in ("SETTINGS", "SOURCE"):
            ui.item_tab = tab
            props = {c[1] for c in recorded(draw_all)}
            if kind == "SUN_COLOR":
                assert "solid_color" in props and "item_tab" not in props
            else:
                assert "item_tab" in props
                assert ("tex_interpolation" in props) == (tab == "SOURCE")
                assert ("emission" in props) == (tab == "SETTINGS")
    ui.item_tab = "SETTINGS"
    assert "GOD_RAYS" not in {e.identifier for e in S.items[0].bl_rna.properties["kind"].enum_items}

    def quick_ops(layer):
        cfg.active_layer = list(cfg.layers).index(layer)
        start = len(CALLS)
        MENUS_DRAWN.update(c.__name__ for c in UI.classes if issubclass(c, bpy.types.Menu))
        UI.DANGUS_PT_main.draw(Fake(), bpy.context)
        return [op for name, op in CALLS[start:] if name == "operator"]
    ops = quick_ops(S)
    assert ops.count("dangus.add_image") == 1 and "dangus.add_sun_item" not in ops, ops
    ops = quick_ops(sky_layer)
    assert ops.count("dangus.add_image") == 3 and "dangus.add_sun_item" not in ops
    cfg.active_layer = list(cfg.layers).index(S)
    try:
        bpy.ops.dangus.add_fog()
        raise AssertionError("fog must not go into a sun layer")
    except RuntimeError:
        pass
    cfg.active_layer = list(cfg.layers).index(sky_layer)


def main():
    global UI
    UI = enable().ui
    sc = bpy.context.scene
    cfg = sc.dangus
    draw_all()
    L = cfg.layers.add()
    draw_all()
    for n in range(2):
        it = L.items.add()
        it.image = make_image("sky%d" % n, (0.2, 0.3, 0.9))
    cloud = L.items.add()
    cloud.kind, cloud.image = "DECAL", make_image("cloud", (1, 1, 1))
    for kind in ("IMAGE", "NOISE", "VORONOI", "GRADIENT"):
        for owner in (L, cloud):
            m = owner.masks.add()
            m.kind = kind
    ui = bpy.context.window_manager.dangus_ui
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, True)
    for active in range(len(L.items)):
        L.active_item = active
        for am in range(4):
            L.active_mask = cloud.active_mask = am
            for mode in ("FULL", "ASPECT"):
                cfg.vertical_mode = mode
                draw_all()
    for kind in [e.identifier for e in cfg.bl_rna.properties["edge_noise"].enum_items]:
        cfg.edge_noise = kind
        draw_all()
    for kind in ("VIDEO", "IMAGE"):
        cfg.bake_kind = kind
        props = {c[1] for c in recorded(draw_all)}
        assert {"bake_kind", "bake_path", "bake_width", "bake_samples", "bake_load"} <= props
        assert ("bake_animation" in props) == ("bake_format" in props) == (kind == "IMAGE")
        assert ("bake_video" in props) == (kind == "VIDEO")
    ui_state = bpy.context.window_manager.dangus_ui
    ui_state.baking, ui_state.bake_progress, ui_state.bake_status = True, 0.4, "Rendering frame 3"
    calls = recorded(draw_all)
    assert "progress" in [c[0] for c in calls] and "dangus.bake_sky" not in {c[1] for c in calls}
    ui_state.baking = False
    cloud.alpha_source = "LUMINANCE"
    L.active_item = 2
    draw_all()
    bpy.ops.dangus.build()
    draw_all()
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, False)
    draw_all()
    ops = {c[1] for c in CALLS if c[0] in ("operator", "operator_menu_enum")}
    for needed in ("dangus.add_image", "dangus.add_existing_image", "dangus.place_cloud",
                   "dangus.select_cloud", "dangus.build", "dangus.clear", "dangus.add_mask",
                   "dangus.duplicate_image", "dangus.duplicate_layer", "dangus.set_kind",
                   "dangus.replace_image", "dangus.reload_image", "dangus.edge_preset_apply",
                   "dangus.edge_preset_save", "dangus.bake_sky", "dangus.load_panorama"):
        assert needed in ops, "%s button never drawn" % needed
    assert UI.DANGUS_PT_main.bl_label == "dangus %s" % UI.VERSION and UI.VERSION != "?"
    v04_checks(cfg, L, cloud, ui)
    v05_checks(cfg, L, cloud, ui)
    v05_fog_sun_modifiers(cfg, L, cloud, ui)
    print("UI: %d calls validated" % len(CALLS))
    print("DANGUS UI TEST PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
