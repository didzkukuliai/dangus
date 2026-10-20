import os
import subprocess
import sys
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
OUT = os.path.join(ROOT, "tests", "out")
STATE = {}


def setup():
    import smoke_test as st
    pkg = st.enable()
    os.makedirs(OUT, exist_ok=True)
    sc = bpy.context.scene
    cfg = sc.dangus
    L = cfg.layers.add()
    L.name = "Sky"
    for k in range(3):
        it = L.items.add()
        it.image = st.make_image("sky%d" % k, (0.2 * k, 0.5, 0.9))
    L.items[1].masks.add().kind = "NOISE"
    C = cfg.layers.add()
    C.name = "Clouds"
    it = C.items.add()
    it.kind, it.image = "DECAL", st.make_image("cloud", (1, 1, 1))
    m = it.modifiers.add()
    m.kind, m.uid = "RAMP", "r1"
    C.items.add().kind = "FOG"
    S = cfg.layers.add()
    S.name, S.kind, S.blend = "Sun", "SUN", "ADD"
    S.items.add().kind = "SUN_COLOR"
    S.sun_relight_sky = S.sun_relight_disc = True
    S.sun_mode = "TIME"
    pkg.nodes_builder.build_sky(sc)
    win = bpy.context.window_manager.windows[0]
    areas = sorted(win.screen.areas, key=lambda a: -a.width * a.height)
    area = areas[0]
    area.type = "NODE_EDITOR"
    area.ui_type = "ShaderNodeTree"
    sp = area.spaces.active
    sp.shader_type = "WORLD"
    STATE["win"], STATE["area"] = win, area
    win.event_simulate(type="ESC", value="PRESS", x=10, y=10)
    win.event_simulate(type="ESC", value="RELEASE", x=10, y=10)
    sp.show_region_ui = False
    for a in win.screen.areas:
        if a != area and a.type == "NODE_EDITOR":
            a.type = "OUTLINER"
    return 0.8


def group_node(tree, name):
    return next(n for n in tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree.name == name)


def enter(name):
    def step():
        win, area = STATE["win"], STATE["area"]
        sp = area.spaces.active
        wt = bpy.context.scene.world.node_tree
        top = wt.nodes["DG_Sky"].node_tree
        chain = [wt.nodes["DG_Sky"]]
        if name == "dangus Cloud Plane":
            host = bpy.data.node_groups["dangus · Clouds"]
            chain += [group_node(top, host.name), group_node(host, name)]
        elif name != "dangus":
            chain.append(group_node(top, name))
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, area=area, region=region, space_data=sp):
            while len(sp.path) > 1:
                bpy.ops.node.tree_path_parent()
            for n in chain:
                tree = sp.edit_tree
                for x in tree.nodes:
                    x.select = False
                n.select = True
                tree.nodes.active = n
                bpy.ops.node.group_edit(exit=False)
            print("PATH", [p.node_tree.name for p in sp.path])
        area.tag_redraw()
        return 0.6
    return step

def view():
    win, area = STATE["win"], STATE["area"]
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.node.select_all(action="DESELECT")
        bpy.ops.node.view_all()
    return 0.8


def shot(fname):
    def step():
        win, area = STATE["win"], STATE["area"]
        H = win.height
        geo = "%dx%d+%d+%d" % (area.width, area.height, area.x, H - area.y - area.height)
        subprocess.run(["import", "-window", "root", "-crop", geo, "+repage",
                        os.path.join(OUT, fname)], check=True)
        return 0.3
    return step


STEPS = [setup]
for g, f in (("dangus", "top"), ("dangus · Sky", "sky"), ("dangus · Clouds", "clouds"),
             ("dangus · Sun", "sun"), ("dangus Cloud Plane", "cloud_plane")):
    STEPS += [enter(g), view, shot("nodes_%s.png" % f)]


def zoom():
    win, area = STATE["win"], STATE["area"]
    region = next(r for r in area.regions if r.type == "WINDOW")
    sp = area.spaces.active
    tree = sp.edit_tree
    frames = [n for n in tree.nodes if n.bl_idname == "NodeFrame"]
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.node.select_all(action="DESELECT")
        for n in tree.nodes:
            n.select = n.parent == frames[0] or n == frames[0]
        bpy.ops.node.view_selected()
        bpy.ops.node.select_all(action="DESELECT")
    return 0.8


STEPS += [enter("dangus · Clouds"), view, zoom, shot("nodes_clouds_detail.png")]


def run():
    try:
        d = STEPS.pop(0)()
    except Exception:
        traceback.print_exc()
        os._exit(1)
    if STEPS:
        return d
    print("NODE SHOTS DONE")
    sys.stdout.flush()
    os._exit(0)


bpy.app.timers.register(run, first_interval=1.0)
