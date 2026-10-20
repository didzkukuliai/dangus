import os
import tomllib

PREFIX = "DG_"
SUN_KINDS = ("SUN_IMAGE", "SUN_COLOR")
BLEND_OCCLUSION = {"MIX": 1.0, "MULTIPLY": 1.0, "DARKEN": 1.0, "OVERLAY": 0.5,
                   "SOFT_LIGHT": 0.5, "ADD": 0.0, "SCREEN": 0.0, "LIGHTEN": 0.0}


def _read_version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as f:
            return tomllib.load(f)["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "?"


VERSION = _read_version()


def sk(sockets, key):
    for s in sockets:
        if s.identifier == key:
            return s
    return sockets[key]


def has_sky_nodes(world):
    tree = world.node_tree if world else None
    return tree is not None and tree.nodes.get(PREFIX + "Sky") is not None


def scene_fps(scene):
    return scene.render.fps / scene.render.fps_base


def hours_to_hm(hours):
    minutes = int(round(min(max(hours, 0.0), 24.0) * 60.0))
    return minutes // 60 + (minutes % 60) / 100.0


def hm_to_hours(value):
    value = min(max(value, 0.0), 24.0)
    h = int(value + 1e-6)
    return min(h + min(round((value - h) * 100.0), 59) / 60.0, 24.0)
