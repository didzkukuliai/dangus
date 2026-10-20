import traceback
from functools import partial

import bpy
from bpy.app.handlers import persistent

from . import nodes_builder
from .utils import has_sky_nodes

_pending = set()


def on_update(self, context):
    scene = self.id_data
    if isinstance(scene, bpy.types.Scene):
        schedule(scene)


def schedule(scene):
    name = scene.name
    if name in _pending:
        return
    _pending.add(name)
    bpy.app.timers.register(partial(_run, name), first_interval=0.05)


def _run(name):
    _pending.discard(name)
    scene = bpy.data.scenes.get(name)
    if scene is not None and has_sky_nodes(scene.world):
        try:
            nodes_builder.refresh(scene)
        except ValueError:
            pass
        except Exception:
            traceback.print_exc()
    return None


@persistent
def on_depsgraph(scene, depsgraph=None):
    if getattr(scene, "dangus", None) is not None and scene.dangus.layers and \
            has_sky_nodes(scene.world):
        try:
            nodes_builder.mirror_animation(scene)
        except Exception:
            traceback.print_exc()


@persistent
def on_frame(scene, depsgraph=None):
    if getattr(scene, "dangus", None) is None or not scene.dangus.layers:
        return
    try:
        nodes_builder.apply_time_keys(scene)
        if has_sky_nodes(scene.world) and nodes_builder.keyed_settings(scene):
            nodes_builder.refresh(scene)
    except ValueError:
        pass
    except Exception:
        traceback.print_exc()


@persistent
def on_load(*_args):
    for scene in bpy.data.scenes:
        on_frame(scene)


HANDLERS = ((bpy.app.handlers.depsgraph_update_post, on_depsgraph),
            (bpy.app.handlers.frame_change_post, on_frame),
            (bpy.app.handlers.load_post, on_load))


def register():
    for handlers, fn in HANDLERS:
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    for handlers, fn in HANDLERS:
        if fn in handlers:
            handlers.remove(fn)
