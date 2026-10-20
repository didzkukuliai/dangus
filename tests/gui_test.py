import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "out")
sys.path.insert(0, os.path.join(ROOT, "tests"))

from smoke_test import write_png


class Tee(io.TextIOBase):

    def __init__(self, real):
        self.real, self.buf = real, []

    def write(self, s):
        self.buf.append(s)
        return self.real.write(s)

    def flush(self):
        self.real.flush()


ERR = Tee(sys.stderr)
sys.stderr = ERR
DRAWN = set()


def watch_panels():
    sys.path.insert(0, ROOT)
    from dangus import ui

    def panel(orig, name):
        def draw(self, context):
            orig(self, context)
            DRAWN.add(name)
        return draw

    def ulist(orig, name):
        def draw_item(self, context, layout, data, item, icon, active_data,
                      active_propname, index):
            orig(self, context, layout, data, item, icon, active_data, active_propname, index)
            DRAWN.add(name)
        return draw_item

    for cls in ui.classes:
        if issubclass(cls, bpy.types.UIList):
            cls.draw_item = ulist(cls.draw_item, cls.__name__)
        else:
            cls.draw = panel(cls.draw, cls.__name__)


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    sidebar = next(r for r in area.regions if r.type == "UI")
    return win, area, region, sidebar


def redraw(area):
    for r in area.regions:
        r.tag_redraw()


def screenshot(win, area, name="gui_sidebar.png"):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    if os.environ.get("DISPLAY") and shutil.which("import"):
        subprocess.run(["import", "-window", "root", path], check=False, timeout=30)
    else:
        with bpy.context.temp_override(window=win, area=area):
            bpy.ops.screen.screenshot_area(filepath=path)


def step_setup():
    watch_panels()
    import dangus
    dangus.register()
    win, area, region, sidebar = view3d()
    win.event_simulate(type="ESC", value="PRESS", x=10, y=10)
    win.event_simulate(type="ESC", value="RELEASE", x=10, y=10)
    area.spaces.active.show_region_ui = True
    redraw(area)
    return 0.5


def step_open_tab():
    win, area, region, sidebar = view3d()
    sidebar.active_panel_category = "dangus"
    redraw(area)
    return 0.5


def step_add_images():
    win, area, region, sidebar = view3d()
    assert "DANGUS_PT_main" in DRAWN, "main panel did not draw"
    tmp = tempfile.mkdtemp(prefix="dangus_gui_")
    for name, rgb in (("sky_a.png", (0.2, 0.4, 0.9)), ("sky_b.png", (0.9, 0.5, 0.2)),
                      ("cloud.png", (1.0, 1.0, 1.0))):
        write_png(os.path.join(tmp, name), rgb, alpha=name.startswith("cloud"))

    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        assert bpy.ops.dangus.add_layer() == {"FINISHED"}
        r = bpy.ops.dangus.add_image(directory=tmp, files=[{"name": "sky_a.png"},
                                                           {"name": "sky_b.png"}], kind="SKY")
        assert r == {"FINISHED"}, r
        r = bpy.ops.dangus.add_image(directory=tmp, files=[{"name": "cloud.png"}], kind="DECAL")
        assert r == {"FINISHED"}, "adding a cloud failed: %s" % r
        assert bpy.ops.dangus.build() == {"FINISHED"}

    L = bpy.context.scene.dangus.layers[0]
    assert [it.kind for it in L.items] == ["SKY", "SKY", "DECAL"]
    assert L.items[2].decal_object is not None, "cloud has no Empty"
    L.active_item = 2
    L.items[2].alpha_source = "LUMINANCE"
    bpy.ops.dangus.refresh()
    ui = bpy.context.window_manager.dangus_ui
    for p in ui.bl_rna.properties:
        if p.identifier.startswith("show_"):
            setattr(ui, p.identifier, True)
    redraw(area)
    return 0.5


def step_check_panels():
    win, area, region, sidebar = view3d()
    for name in ("DANGUS_PT_main", "DANGUS_UL_layers", "DANGUS_UL_items"):
        assert name in DRAWN, "%s never finished drawing (errors in draw?)" % name

    cloud = bpy.context.scene.dangus.layers[0].items[2]
    ob = cloud.decal_object
    before = ob.location.copy()
    rv3d = area.spaces.active.region_3d
    rv3d.view_perspective = "PERSP"
    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        r = bpy.ops.dangus.place_cloud("INVOKE_DEFAULT")
    assert r == {"RUNNING_MODAL"}, r
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cx + 150, y=cy + 120)
    win.event_simulate(type="LEFTMOUSE", value="PRESS", x=cx + 150, y=cy + 120)
    win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cx + 260, y=cy + 200)
    win.event_simulate(type="LEFTMOUSE", value="RELEASE", x=cx + 260, y=cy + 200)
    STATE["before"] = before
    return 0.5


def step_check_place():
    from mathutils import Vector
    cfg = bpy.context.scene.dangus
    ob = cfg.layers[0].items[2].decal_object
    after = ob.location.copy()
    ang = math.degrees(STATE["before"].angle(after))
    assert ang > 1.0, "Place Cloud did not re-aim the Empty (moved %.2f deg)" % ang
    radius = STATE["before"].length
    assert abs(after.length - radius) < 1e-3, (after.length, radius)
    facing = ob.rotation_euler.to_matrix() @ Vector((0, 0, 1))
    assert (facing + after.normalized()).length < 1e-3, "Empty does not face the origin"
    assert abs(cfg.layers[0].items[2].cloud_scale - 1.0) > 1e-3, "drag did not resize the cloud"
    print("place_cloud: moved %.1f deg over the dome, half-extent %.3f" % (ang, ob.scale.x))
    win, area, region, sidebar = view3d()
    redraw(area)
    return 0.5


def step_screenshot():
    win, area, region, sidebar = view3d()
    screenshot(win, area, "gui_sidebar_cloud.png")
    cfg = bpy.context.scene.dangus
    cfg.layers[0].active_item = 0
    bpy.context.window_manager.dangus_ui.item_tab = "SOURCE"
    redraw(area)
    return 0.5


def step_fog():
    win, area, region, sidebar = view3d()
    screenshot(win, area, "gui_sidebar_sky.png")
    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        assert bpy.ops.dangus.add_fog() == {"FINISHED"}
        assert bpy.ops.dangus.add_modifier(owner="ITEM", kind="MATH") == {"FINISHED"}
    ui = bpy.context.window_manager.dangus_ui
    ui.item_tab = "SETTINGS"
    ui.show_layer_masks = ui.show_edge = ui.show_dome = ui.show_culling = False
    bpy.ops.dangus.refresh()
    redraw(area)
    return 0.5


def step_sun():
    win, area, region, sidebar = view3d()
    screenshot(win, area, "gui_sidebar_fog.png")
    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        assert bpy.ops.dangus.add_layer(kind="SUN") == {"FINISHED"}
        assert bpy.ops.dangus.add_sun_item() == {"FINISHED"}
    bpy.ops.dangus.refresh()
    redraw(area)
    return 0.5


def step_sun_menu():
    win, area, region, sidebar = view3d()
    screenshot(win, area, "gui_sidebar_sun.png")
    cx, cy = sidebar.x + sidebar.width // 2, sidebar.y + sidebar.height // 2
    win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cx, y=cy)
    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        bpy.ops.wm.call_menu(name="DANGUS_MT_add_image")
    return 0.5


def step_sun_menu_shot():
    win, area, region, sidebar = view3d()
    screenshot(win, area, "gui_sun_add_menu.png")
    win.event_simulate(type="ESC", value="PRESS", x=10, y=10)
    win.event_simulate(type="ESC", value="RELEASE", x=10, y=10)
    return 0.5


def gen_step(gen):
    def step():
        try:
            delay = next(gen)
        except StopIteration:
            return 0.2
        STEPS.insert(0, step)
        return delay
    return step


def clock_events():
    from dangus import nodes_builder
    win, area, region, sidebar = view3d()
    sc = bpy.context.scene
    L = next(L for L in sc.dangus.layers if L.kind == "SUN")
    L.sun_mode, L.sun_hours = "TIME", 9.0
    sc.frame_set(1)
    nodes_builder.refresh(sc)

    class DANGUS_TEST_PT_clock(bpy.types.Panel):
        bl_space_type, bl_region_type = "VIEW_3D", "UI"
        bl_category, bl_label = "dangusClock", "Clock"

        def draw(self, context):
            col = self.layout.column()
            col.scale_y = 80.0
            col.prop(L, "sun_time", slider=True)

    bpy.utils.register_class(DANGUS_TEST_PT_clock)
    redraw(area)
    yield 0.5
    sidebar.active_panel_category = "dangusClock"
    redraw(area)
    yield 0.5
    x, y = sidebar.x + sidebar.width // 2 - 20, sidebar.y + sidebar.height // 2

    def ev(kind, value="PRESS", dx=0, **kw):
        win.event_simulate(type=kind, value=value, x=x + dx, y=y, **kw)

    def hover():
        ev("MOUSEMOVE", "NOTHING", 2)
        ev("MOUSEMOVE", "NOTHING", 0)

    def tap(kind, **kw):
        ev(kind, "PRESS", **kw)
        ev(kind, "RELEASE", **kw)

    def socket():
        gn = sc.world.node_tree.nodes["DG_Sky"]
        return gn.inputs["%s Time" % L.name].default_value

    def keys():
        bag = nodes_builder.channelbag(sc)
        fc = bag.fcurves.find(L.path_from_id("sun_hours")) if bag else None
        return sorted(k.co[0] for k in fc.keyframe_points) if fc else []

    for dx in range(4):
        ev("MOUSEMOVE", "NOTHING", dx)
        yield 0.1
    tap("I")
    yield 0.4
    assert keys() == [1.0], ("I over the Time slider keys the time", keys())
    ev("LEFTMOUSE")
    yield 0.3
    for dx in range(10, 130, 10):
        ev("MOUSEMOVE", "NOTHING", dx)
        yield 0.05
    ev("LEFTMOUSE", "RELEASE", 120)
    for _ in range(10):
        yield 0.1
    assert L.sun_hours > 9.2, ("dragging the slider changes the time", L.sun_hours)
    assert abs(socket() - L.sun_hours) < 1e-4, "an edit on a keyed time shows in the sky"
    sc.frame_set(2)
    yield 0.3
    assert abs(L.sun_hours - 9.0) < 1e-4 and abs(socket() - 9.0) < 1e-4, (
        "another frame brings back the keyed time", L.sun_hours, socket())
    sc.frame_set(21)
    L.sun_time = 10.30
    hover()
    yield 0.3
    tap("I")
    yield 0.4
    assert keys() == [1.0, 21.0], keys()
    sc.frame_set(11)
    yield 0.3
    assert 9.2 < L.sun_hours < 10.3 and abs(socket() - L.sun_hours) < 1e-4, (L.sun_hours, socket())
    sc.frame_set(21)
    hover()
    yield 0.3
    tap("I", alt=True)
    yield 0.4
    assert keys() == [1.0], ("Alt+I deletes the key", keys())
    sc.frame_set(1)
    sidebar.active_panel_category = "dangus"
    redraw(area)
    yield 0.5
    screenshot(win, area, "gui_time_keyed.png")
    print("time slider: I keys, Alt+I deletes, drag, live on keyed frames")
    bpy.utils.unregister_class(DANGUS_TEST_PT_clock)
    redraw(area)
    yield 0.3


def step_bake():
    win, area, region, sidebar = view3d()
    cfg = bpy.context.scene.dangus
    cfg.bake_path = os.path.join(tempfile.mkdtemp(prefix="dangus_bake_"), "sky")
    cfg.bake_width, cfg.bake_samples, cfg.bake_load = 64, 1, True
    n = len(cfg.layers)
    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        assert bpy.ops.dangus.bake_sky() == {"FINISHED"}
    assert os.path.exists(cfg.bake_path + ".exr"), "baking from the sidebar wrote no file"
    assert len(cfg.layers) == n + 1 and cfg.layers[-1].items[0].panorama
    assert bpy.context.scene.name == "Scene" and "dangus_bake" not in bpy.data.scenes
    bpy.ops.dangus.refresh()
    ui = bpy.context.window_manager.dangus_ui
    ui.show_layers = ui.show_bake = True
    redraw(area)
    return 0.5


def step_bake_modal():
    win, area, region, sidebar = view3d()
    sc = bpy.context.scene
    cfg = sc.dangus
    folder = tempfile.mkdtemp(prefix="dangus_bake_modal_")
    cfg.bake_kind, cfg.bake_format, cfg.bake_animation = "IMAGE", "PNG", True
    cfg.bake_path, cfg.bake_width, cfg.bake_samples, cfg.bake_load = (
        os.path.join(folder, "sky"), 256, 16, False)
    sc.frame_start, sc.frame_end = 1, 6
    STATE["folder"], STATE["seen"] = folder, []
    with bpy.context.temp_override(window=win, area=area, region=sidebar):
        assert bpy.ops.dangus.bake_sky("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    return 0.2


def step_bake_progress():
    ui = bpy.context.window_manager.dangus_ui
    STATE["seen"].append((ui.baking, round(ui.bake_progress, 3), ui.bake_status))
    if ui.baking and 0.0 < ui.bake_progress < 1.0 and "shot" not in STATE:
        win, area, region, sidebar = view3d()
        screenshot(win, area, "gui_bake_progress.png")
        STATE["shot"] = True
    if ui.baking:
        STEPS.insert(0, step_bake_progress)
        return 0.1
    files = sorted(os.listdir(STATE["folder"]))
    assert files == ["sky_%04d.png" % f for f in range(1, 7)], files
    partial = [p for b, p, _s in STATE["seen"] if b and 0.0 < p < 1.0]
    assert partial and "shot" in STATE, "no progress was shown: %s" % STATE["seen"]
    print("modal bake: progress seen %s" % sorted(set(partial)))
    return 0.3


def step_screenshot_sun():
    win, area, region, sidebar = view3d()
    screenshot(win, area, "gui_sidebar_bake.png")
    errors = "".join(ERR.buf)
    assert "Traceback" not in errors, "Python errors while drawing the UI:\n" + errors
    return None


STATE = {}
STEPS = [step_setup, step_open_tab, step_add_images, step_check_panels, step_check_place,
         step_screenshot, step_fog, step_sun, step_sun_menu, step_sun_menu_shot, gen_step(clock_events()), step_bake,
         step_bake_modal, step_bake_progress, step_screenshot_sun]


def runner():
    try:
        delay = STEPS.pop(0)()
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        sys.__stderr__.flush()
        os._exit(1)
    if delay is not None and STEPS:
        return delay
    if STEPS:
        return 0.2
    print("DANGUS GUI TEST PASSED")
    sys.stdout.flush()
    os._exit(0)


def watchdog():
    print("GUI TEST TIMED OUT; steps left: %s" % [f.__name__ for f in STEPS], file=sys.__stderr__)
    sys.__stderr__.flush()
    os._exit(1)


bpy.app.timers.register(runner, first_interval=1.0)
bpy.app.timers.register(watchdog, first_interval=120.0)
