import math

from mathutils import Vector

TWO_PI = 2.0 * math.pi


def _wrap(a):
    return (a + math.pi) % TWO_PI - math.pi


def angular_distance(a, b):
    return abs(_wrap(a - b))


def _frame_sample(scene, cam):
    if cam is None or cam.type != "CAMERA":
        return None, "no active camera"
    if cam.data.type != "PERSP":
        return None, "camera is not perspective"
    frame = list(cam.data.view_frame(scene=scene))
    rot = cam.matrix_world.to_quaternion()
    inv = rot.inverted()
    xs = [f.x for f in frame]
    ys = [f.y for f in frame]
    for up in (Vector((0, 0, 1)), Vector((0, 0, -1))):
        c = inv @ up
        if c.z < 0:
            k = frame[0].z / c.z
            px, py = c.x * k, c.y * k
            if min(xs) <= px <= max(xs) and min(ys) <= py <= max(ys):
                return None, "camera sees the zenith or nadir"
    pts = frame + [(frame[k] + frame[(k + 1) % 4]) / 2 for k in range(4)]
    dirs = [(rot @ p).normalized() for p in pts]
    fwd = (rot @ Vector((0, 0, -1))).normalized()
    if any(d.dot(fwd) < 0.1 for d in dirs):
        return None, "field of view too wide"
    mean = sum(dirs, Vector((0, 0, 0)))
    if mean.x ** 2 + mean.y ** 2 < 1e-6:
        return None, "camera looks straight up or down"
    c_az = math.atan2(mean.y, mean.x)
    deltas = [_wrap(math.atan2(d.y, d.x) - c_az) for d in dirs]
    lo, hi = min(deltas), max(deltas)
    if hi - lo > 5.0:
        return None, "field of view covers almost the whole horizon"
    max_elev = max(math.asin(max(-1.0, min(1.0, d.z))) for d in dirs)
    return (((c_az + (lo + hi) / 2), (hi - lo) / 2), max_elev), None


def camera_visibility(scene, margin, animated):
    frames = [scene.frame_current]
    if animated:
        a, b = scene.frame_start, scene.frame_end
        step = max(1, (b - a) // 32)
        frames = list(range(a, b + 1, step))
        if frames[-1] != b:
            frames.append(b)
    orig = scene.frame_current
    arcs, max_elev = [], -math.pi
    try:
        for f in frames:
            if animated:
                scene.frame_set(f)
            res, reason = _frame_sample(scene, scene.camera)
            if res is None:
                return None, reason
            (c, h), me = res
            arcs.append((c, h + margin))
            max_elev = max(max_elev, me)
    finally:
        if animated:
            scene.frame_set(orig)
    return {"arcs": arcs, "max_elev": max_elev + margin}, None


def average_color(image):
    try:
        import numpy as np
        w, h = image.size
        ch = image.channels
        if w == 0 or h == 0:
            return (0.5, 0.5, 0.5, 1.0)
        buf = np.empty(w * h * ch, dtype=np.float32)
        image.pixels.foreach_get(buf)
        px = buf.reshape(-1, ch)
        px = px[:: max(1, px.shape[0] // 65536)]
        alpha = float(px[:, 3].mean()) if ch == 4 else 1.0
        rgb = px[:, :3] if ch >= 3 else np.repeat(px[:, :1], 3, axis=1)
        if not image.is_float and image.colorspace_settings.name == "sRGB":
            rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        return tuple(float(x) for x in rgb.mean(axis=0)) + (alpha,)
    except Exception:
        return (0.5, 0.5, 0.5, 1.0)
