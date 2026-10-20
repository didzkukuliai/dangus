import json
import os

import bpy

from .properties import EDGE_PROPS

BUILTIN = {
    "Soft Clouds": dict(edge_noise="NOISE", noise_scale=4.0, edge_detail=2.0,
                        edge_roughness=0.5, edge_distortion=0.0, edge_low=0.3, edge_high=0.7,
                        edge_softness=0.6),
    "Patchy": dict(edge_noise="NOISE", noise_scale=10.0, edge_detail=6.0, edge_roughness=0.7,
                   edge_distortion=0.5, edge_low=0.35, edge_high=0.65, edge_softness=0.15),
    "Cells": dict(edge_noise="VORONOI", noise_scale=6.0, edge_detail=0.0, edge_roughness=0.5,
                  edge_randomness=1.0, edge_low=0.1, edge_high=0.6, edge_softness=0.3),
    "Wind Streaks": dict(edge_noise="WAVE", noise_scale=3.0, edge_distortion=6.0,
                         edge_detail=4.0, edge_roughness=0.6, edge_low=0.2, edge_high=0.8,
                         edge_softness=0.4),
    "Swirls": dict(edge_noise="MAGIC", noise_scale=2.0, edge_distortion=2.5, edge_low=0.2,
                   edge_high=0.8, edge_softness=0.5),
    "Grain": dict(edge_noise="GABOR", noise_scale=12.0, edge_frequency=3.0,
                  edge_anisotropy=0.8, edge_low=-0.4, edge_high=0.4, edge_softness=0.3),
    "Hard Cut": dict(edge_noise="NOISE", noise_scale=4.0, edge_detail=0.0, edge_roughness=0.5,
                     edge_distortion=0.0, edge_low=0.49, edge_high=0.51, edge_softness=0.05),
}


def user_dir(create=False):
    pkg = __package__ or "dangus"
    try:
        return bpy.utils.extension_path_user(pkg, path="edge_presets", create=create)
    except (ValueError, KeyError):
        path = bpy.utils.user_resource("CONFIG", path=os.path.join("dangus", "edge_presets"))
        if create:
            os.makedirs(path, exist_ok=True)
        return path


def _path(name):
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    return os.path.join(user_dir(create=True), (safe or "preset") + ".json")


def user_presets():
    d = user_dir()
    out = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    data = json.load(f)
                out[data.get("name", fn[:-5])] = data.get("values", {})
            except (OSError, ValueError):
                continue
    return out


def capture(cfg):
    return {k: getattr(cfg, k) for k in EDGE_PROPS}


def apply(cfg, name):
    values = BUILTIN.get(name) or user_presets().get(name)
    if values is None:
        return False
    if "edge_noise" in values:
        cfg.edge_noise = values["edge_noise"]
    for k, v in values.items():
        if k in EDGE_PROPS and k != "edge_noise":
            setattr(cfg, k, v)
    return True


def save(cfg, name):
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump({"name": name, "values": capture(cfg)}, f, indent=1)


def remove(name):
    d = user_dir()
    for fn in os.listdir(d) if os.path.isdir(d) else ():
        p = os.path.join(d, fn)
        try:
            with open(p, encoding="utf-8") as f:
                if json.load(f).get("name") == name:
                    os.remove(p)
                    return True
        except (OSError, ValueError):
            continue
    return False


