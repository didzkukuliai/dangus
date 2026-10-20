import math
import os
import shutil
import tempfile

import bpy

from . import nodes_builder

IMAGE_EXT = {"OPEN_EXR": ".exr", "PNG": ".png", "JPEG": ".jpg", "TIFF": ".tif", "HDR": ".hdr",
             "WEBP": ".webp"}
DEPTH = {"OPEN_EXR": "32", "PNG": "16", "TIFF": "16"}
VIDEO = {"MP4_H264": ("MPEG4", "H264", ".mp4"), "MP4_H265": ("MPEG4", "H265", ".mp4"),
         "MKV_AV1": ("MKV", "AV1", ".mkv"), "WEBM_VP9": ("WEBM", "WEBM", ".webm"),
         "MOV_PRORES": ("QUICKTIME", "PRORES", ".mov"), "MKV_FFV1": ("MKV", "FFV1", ".mkv")}
KNOWN_EXT = set(IMAGE_EXT.values()) | {v[2] for v in VIDEO.values()} | {".jpeg"}


def size(cfg):
    w = cfg.bake_width + cfg.bake_width % 2
    h = w // 2
    return w, h + h % 2


def base_path(cfg):
    path = cfg.bake_path or "//dangus_sky"
    if path.startswith("//") and not bpy.data.filepath:
        path = os.path.join(bpy.app.tempdir or os.path.expanduser("~"), path[2:])
    base = bpy.path.abspath(path)
    stem, ext = os.path.splitext(base)
    return stem if ext.lower() in KNOWN_EXT else base


def _image_settings(im, fmt):
    im.media_type, im.file_format = "IMAGE", fmt
    im.color_mode = "RGB"
    if fmt in DEPTH:
        im.color_depth = DEPTH[fmt]
    if fmt == "OPEN_EXR":
        im.exr_codec = "ZIP"


def _setup(scene, tmp, cfg):
    tmp.world = scene.world
    for L in scene.dangus.layers:
        for it in L.items:
            ob = it.decal_object
            if ob is not None and ob.name not in tmp.collection.objects:
                tmp.collection.objects.link(ob)
    cam = bpy.data.objects.new("dangus_bake", bpy.data.cameras.new("dangus_bake"))
    cam.data.type, cam.data.panorama_type = "PANO", "EQUIRECTANGULAR"
    cam.rotation_euler = (math.pi / 2, 0.0, -math.pi / 2)
    tmp.collection.objects.link(cam)
    tmp.camera = cam
    r = tmp.render
    r.engine = "CYCLES"
    tmp.cycles.samples, tmp.cycles.use_denoising = cfg.bake_samples, False
    tmp.cycles.device = scene.cycles.device
    (r.resolution_x, r.resolution_y), r.resolution_percentage = size(cfg), 100
    r.film_transparent = False
    r.fps, r.fps_base = scene.render.fps, scene.render.fps_base
    vs = tmp.view_settings
    vs.view_transform, vs.look, vs.exposure, vs.gamma = "Standard", "None", 0.0, 1.0
    return cam


def _encode(scene, cfg, frames, base):
    fmt, codec, ext = VIDEO[cfg.bake_video]
    enc = bpy.data.scenes.new("dangus_encode")
    try:
        strip = enc.sequence_editor_create().strips.new_image(
            "dangus_frames", frames[0], 1, scene.frame_start)
        for p in frames[1:]:
            strip.elements.append(os.path.basename(p))
        r = enc.render
        (r.resolution_x, r.resolution_y), r.resolution_percentage = size(cfg), 100
        r.fps, r.fps_base = scene.render.fps, scene.render.fps_base
        enc.frame_start, enc.frame_end = scene.frame_start, scene.frame_start + len(frames) - 1
        enc.view_settings.view_transform = "Standard"
        r.image_settings.media_type, r.image_settings.file_format = "VIDEO", "FFMPEG"
        r.ffmpeg.format, r.ffmpeg.codec = fmt, codec
        if codec not in ("PRORES", "FFV1"):
            r.ffmpeg.constant_rate_factor = cfg.bake_quality
        r.filepath = base + "_"
        with bpy.context.temp_override(scene=enc):
            bpy.ops.render.render(animation=True, scene=enc.name)
        return r.frame_path(frame=enc.frame_start)
    finally:
        bpy.data.scenes.remove(enc)


def steps(scene):
    cfg = scene.dangus
    if nodes_builder.refresh(scene) == "none":
        nodes_builder.build_sky(scene)
    base = base_path(cfg)
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    video = cfg.bake_kind == "VIDEO"
    animated = video or cfg.bake_animation
    frames = list(range(scene.frame_start, scene.frame_end + 1)) if animated else [
        scene.frame_current]
    total = len(frames) + video
    tmp = bpy.data.scenes.new("dangus_bake")
    cam = folder = None
    current = scene.frame_current
    try:
        cam = _setup(scene, tmp, cfg)
        fmt = "PNG" if video else cfg.bake_format
        _image_settings(tmp.render.image_settings, fmt)
        folder = tempfile.mkdtemp(prefix="dangus_bake_") if video else None
        written = []
        for n, f in enumerate(frames):
            tmp.frame_current = f
            scene.frame_set(f)
            nodes_builder.apply_world_animation(scene, f)
            with bpy.context.temp_override(scene=tmp):
                bpy.ops.render.render(scene=tmp.name)
            if video:
                path = os.path.join(folder, "frame_%06d.png" % f)
            elif animated:
                path = "%s_%04d%s" % (base, f, IMAGE_EXT[fmt])
            else:
                path = base + IMAGE_EXT[fmt]
            bpy.data.images["Render Result"].save_render(path, scene=tmp)
            written.append(path)
            yield n + 1, total, ("Encoding the video..." if video and n + 1 == len(frames) else
                                 "Rendering frame %d of %d" % (min(n + 2, len(frames)), len(frames)))
        result = _encode(scene, cfg, written, base) if video else written[0]
        if video:
            yield total, total, "Done"
    finally:
        scene.frame_set(current)
        nodes_builder.apply_world_animation(scene, current)
        bpy.data.scenes.remove(tmp)
        if cam is not None:
            data = cam.data
            bpy.data.objects.remove(cam)
            bpy.data.cameras.remove(data)
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
    return result, animated, len(frames)


def bake(scene, progress=None):
    run = steps(scene)
    while True:
        try:
            step = next(run)
        except StopIteration as stop:
            return stop.value
        if progress:
            progress(*step)
