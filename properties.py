from math import radians

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from .live import on_update as U
from .utils import hm_to_hours, hours_to_hm

BLEND_MODES = [
    ("MIX", "Normal", "Alpha over"),
    ("ADD", "Add", "Brighten / glow"),
    ("SCREEN", "Screen", "Soft brighten"),
    ("LIGHTEN", "Lighten", "Only where brighter than what is below"),
    ("OVERLAY", "Overlay", "Contrast-preserving blend"),
    ("SOFT_LIGHT", "Soft Light", "Gentle contrast blend"),
    ("MULTIPLY", "Multiply", "Darken (storm clouds, shadows)"),
    ("DARKEN", "Darken", "Only where darker than what is below"),
]

KINDS = [
    ("SKY", "Sky", "Image placed side by side around the dome, in list order"),
    ("DECAL", "Cloud (decal)", "Image projected onto the dome from an Empty you place in the viewport"),
    ("FOG", "Fog", "Fog rising from the bottom of the dome: a gradient, or an image texture "
     "that stretches, fills or repeats upwards"),
    ("SUN_IMAGE", "Sun Image", "An image texture centred on the sun (its disc, corona, flare...)"),
    ("SUN_COLOR", "Solid Color", "A solid colour for the sun's light"),
]
LAYER_KINDS = [
    ("SKY", "Sky Layer", "Sky images, clouds and fog", "RENDERLAYERS", 0),
    ("SUN", "Sun Layer", "A sun. Layers above it hide it (by their coverage and blend mode); "
     "layers below are behind it", "LIGHT_SUN", 1),
]


MASK_KINDS = [
    ("IMAGE", "Image", "Image mask, aligned with the item (layer masks use an equirect image)"),
    ("NOISE", "Noise", "Procedural noise"),
    ("VORONOI", "Voronoi", "Procedural cells"),
    ("GRADIENT", "Gradient", "Fade by elevation above the horizon"),
]

MASK_BLENDS = [
    ("MULTIPLY", "Multiply", "Intersect with the masks above"),
    ("ADD", "Add", "Union with the masks above"),
    ("SUBTRACT", "Subtract", "Cut out of the masks above"),
]

EDGE_NOISES = [
    ("NOISE", "Noise", "Soft fractal noise: cloudy, organic seams"),
    ("VORONOI", "Voronoi", "Cells: patchy, tiled seams"),
    ("WAVE", "Wave", "Bands: streaky, wind-swept seams"),
    ("MAGIC", "Magic", "Interference pattern: swirly seams"),
    ("GABOR", "Gabor", "Directional speckle: grainy, anisotropic seams"),
]

EDGE_LEVELS = {"NOISE": (0.3, 0.7), "VORONOI": (0.1, 0.6), "WAVE": (0.2, 0.8),
               "MAGIC": (0.2, 0.8), "GABOR": (-0.4, 0.4)}


def _get_cloud_scale(self):
    from . import decals
    return decals.get_scale(self.decal_object)


def _set_cloud_scale(self, value):
    from . import decals
    if self.decal_object is not None:
        decals.set_scale(self.decal_object, value)


def _node_enum(prop, extra=()):
    rna = bpy.types.ShaderNodeTexImage.bl_rna.properties[prop]
    return list(extra) + [(e.identifier, e.name, e.description) for e in rna.enum_items]


TEX_INTERPOLATION = _node_enum("interpolation")
TEX_PROJECTION = _node_enum("projection")
TEX_EXTENSION = _node_enum("extension", [(
    "AUTO", "Auto", "What dangus needs: Extend for sky rings (Repeat for a single sky "
    "image), Clip for clouds")])

ALPHA_SOURCES = [
    ("CHANNEL", "Image Alpha", "The image's own alpha channel", "IMAGE_RGB_ALPHA", 0),
    ("LUMINANCE", "Luminance", "The image's brightness (white = opaque): for images without "
     "alpha, e.g. clouds on black", "IMAGE_ZDEPTH", 1),
    ("IMAGE", "Image Texture", "Another image's brightness, laid exactly like this image",
     "IMAGE_DATA", 2),
    ("NONE", "Opaque", "No alpha: the image covers everything (transparent parts turn black)",
     "SNAP_FACE", 3),
]
FOG_TYPES = [
    ("GRADIENT", "Gradient", "A colour fading upwards from the bottom of the dome"),
    ("TEXTURE", "Texture", "The fog image laid from the bottom of the dome upwards"),
]
FOG_FITS = [
    ("STRETCH", "Stretch", "Once around the dome and once from the bottom to the fog top"),
    ("FILL", "Fill", "As tall as the fog, keeping its proportions; repeated around the dome"),
    ("REPEAT", "Repeat", "Tiles of a chosen height, from the bottom of the dome upwards"),
]
FOG_FALLOFFS = [
    ("LINEAR", "Linear", "Straight fade"),
    ("SMOOTHSTEP", "Smooth", "Eased fade"),
    ("SMOOTHERSTEP", "Smoother", "Softer eased fade"),
]


def _edge_noise_changed(self, context):
    self.edge_low, self.edge_high = EDGE_LEVELS[self.edge_noise]
    U(self, context)


MODIFIER_KINDS = [
    ("INVERT", "Invert", "Invert the colour (and optionally the alpha)", "MOD_MASK", 0),
    ("BRIGHT_CONTRAST", "Brightness/Contrast", "Brightness and contrast",
     "MOD_BRIGHTNESS_CONTRAST", 1),
    ("HUE_SAT", "Hue/Saturation", "Shift hue, saturation and value", "MOD_HUE_SATURATION", 2),
    ("GAMMA", "Gamma", "Gamma curve", "MOD_TONEMAP", 3),
    ("CURVES", "RGB Curves", "Curves per channel (edited in place)", "FCURVE", 4),
    ("RAMP", "Color Ramp", "Remap brightness through a colour ramp (its alpha multiplies "
     "the image alpha)", "MOD_TINT", 5),
    ("MULTIPLIER", "Multiplier", "Multiply the colour and the alpha", "MOD_OPACITY", 6),
    ("MATH", "Math", "A math operation with a value, per colour channel (and optionally "
     "on the alpha)", "LINCURVE", 7),
]
MATH_OPS = [(k, n, "") for k, n in (
    ("ADD", "Add"), ("SUBTRACT", "Subtract"), ("MULTIPLY", "Multiply"), ("DIVIDE", "Divide"),
    ("POWER", "Power"), ("MINIMUM", "Minimum"), ("MAXIMUM", "Maximum"),
    ("MODULO", "Modulo"), ("SNAP", "Snap"))]


def F(name, default, desc="", **kw):
    return FloatProperty(name=name, default=default, description=desc, update=U, **kw)


def A(name, deg, desc="", lo=None, hi=None, **kw):
    kw.update({k: radians(v) for k, v in (("min", lo), ("max", hi)) if v is not None})
    return F(name, radians(deg), desc, subtype="ANGLE", **kw)


def B(name, default, desc=""):
    return BoolProperty(name=name, default=default, description=desc, update=U)


def I(name, default, desc="", **kw):
    return IntProperty(name=name, default=default, description=desc, update=U, **kw)


def E(items, name, default, desc="", **kw):
    return EnumProperty(items=items, name=name, default=default, description=desc,
                        **{"update": U, **kw})


def V(name, default, desc="", subtype="XYZ"):
    return FloatVectorProperty(name=name, subtype=subtype, size=3, default=default,
                               description=desc, update=U)


def C(name, default, desc=""):
    return FloatVectorProperty(name=name, subtype="COLOR", size=3, min=0.0, max=1.0,
                               default=default, description=desc, update=U)


def UNIT(name, default, desc="", **kw):
    return F(name, default, desc, min=0.0, max=1.0, **kw)


def _layer_name(layer, context):
    taken = {L.name for L in layer.id_data.dangus.layers if L.as_pointer() != layer.as_pointer()}
    if layer.name in taken:
        name, n = layer.name, 1
        while "%s.%03d" % (name, n) in taken:
            n += 1
        layer.name = "%s.%03d" % (name, n)
        return
    U(layer, context)


def _set_hm(layer, value):
    from .nodes_builder import time_display_value
    animated = time_display_value(layer)
    if animated is None or abs(animated - value) > 1e-4:
        layer.sun_hours = hm_to_hours(value)


MAP_TYPES = [
    ("POINT", "Point", "Move, turn and scale the image"),
    ("TEXTURE", "Texture", "Move, turn and scale the coordinates instead (the inverse of Point)"),
    ("VECTOR", "Vector", "Turn and scale only, no offset"),
    ("NORMAL", "Normal", "Turn and inverse-scale, normalized"),
]


def _map_scale(item, context):
    scale = tuple(item.map_scale)
    last = tuple(item.get("dangus_scale", scale))
    if item.map_scale_lock:
        changed = [v for v, w in zip(scale, last) if abs(v - w) > 1e-7]
        if changed and len(set(scale)) > 1:
            item.map_scale = (changed[0],) * 3
            return
    item["dangus_scale"] = scale
    U(item, context)


def _lock_scale(item, context):
    if item.map_scale_lock and len(set(item.map_scale)) > 1:
        item.map_scale = (item.map_scale[0],) * 3


def ACTIVE(what):
    return IntProperty(name="Active " + what, default=0, description="The selected %s in the list"
                       % (what if " " in what else what.lower()))


class DG_Modifier(bpy.types.PropertyGroup):
    uid: StringProperty(options={"HIDDEN"}, description="Stable id (keeps ramps and curves)")
    kind: E(MODIFIER_KINDS, "Type", "INVERT")
    enabled: B("Enabled", True)
    show_expanded: BoolProperty(name="Expanded", default=True)
    factor: UNIT("Factor", 1.0, "How much of the modifier to apply", subtype="FACTOR")
    bright: F("Brightness", 0.0, soft_min=-1.0, soft_max=1.0)
    contrast: F("Contrast", 0.0, soft_min=-1.0, soft_max=1.0)
    hue: UNIT("Hue", 0.5, subtype="FACTOR")
    saturation: F("Saturation", 1.0, min=0.0, soft_max=2.0)
    value: F("Value", 1.0, min=0.0, soft_max=2.0)
    gamma: F("Gamma", 1.0, min=0.001, soft_max=5.0)
    multiplier: F("Color", 1.0, "Multiplies the colour", min=0.0, soft_max=4.0)
    math_op: E(MATH_OPS, "Operation", "MULTIPLY")
    math_value: F("Value", 1.0, soft_min=-10.0, soft_max=10.0)
    math_clamp: B("Clamp", False, "Clamp the result to 0..1")


class DG_Mask(bpy.types.PropertyGroup):
    enabled: B("Enabled", True)
    kind: E(MASK_KINDS, "Type", "NOISE")
    image: PointerProperty(type=bpy.types.Image, name="Image", update=U)
    invert: B("Invert", False)
    intensity: UNIT("Intensity", 1.0)
    scale: F("Scale", 4.0, min=0.01, soft_max=50.0)
    low: UNIT("Low", 0.0, "Values at or below this become 0")
    high: UNIT("High", 1.0, "Values at or above this become 1")
    angle_from: A("From", -5.0, "Elevation where the mask is 0", -90.0, 90.0)
    angle_to: A("To", 25.0, "Elevation where the mask is 1", -90.0, 90.0)
    blend: E(MASK_BLENDS, "Combine", "MULTIPLY")
    modifiers: CollectionProperty(type=DG_Modifier)


class DG_Item(bpy.types.PropertyGroup):
    uid: StringProperty(options={"HIDDEN"}, description="Stable id (keeps the colour ramp)")
    image: PointerProperty(type=bpy.types.Image, name="Image", update=U)
    kind: E(KINDS, "Type", "SKY")
    blend_width: F("Blend Width", 0.3, "Blend zone on this image's left edge, as a fraction of "
                   "one image's width", min=0.01, max=1.0)
    decal_object: PointerProperty(
        type=bpy.types.Object, name="Empty", update=U,
        description="The cloud is the Empty's image plane seen from the world origin: move it "
                    "over the dome, rotate it to roll, scale it to size it")
    cloud_scale: FloatProperty(
        name="Scale", min=0.01, soft_max=4.0, precision=3, get=_get_cloud_scale,
        set=_set_cloud_scale, options=set(),
        description="Size of the cloud (1 = about 53 degrees across). Resizes its Empty, "
                    "so the viewport and the sky always match")
    softness: UNIT("Edge Softness", 0.15, "Fade at the cloud's edges")
    opacity: UNIT("Opacity", 1.0)
    blend: E(BLEND_MODES, "Blend", "MIX")
    tint: C("Tint", (1.0, 1.0, 1.0))
    tex_interpolation: E(TEX_INTERPOLATION, "Interpolation", "Linear")
    tex_projection: E(TEX_PROJECTION, "Projection", "FLAT", "Image Texture projection. dangus "
                      "feeds 2D image coordinates, so Flat is what keeps the image in place")
    tex_projection_blend: UNIT("Blend", 0.0, subtype="FACTOR")
    tex_extension: E(TEX_EXTENSION, "Extension", "AUTO")
    alpha_source: E(ALPHA_SOURCES, "Alpha", "CHANNEL", "Where the image's alpha comes from")
    alpha_image: PointerProperty(type=bpy.types.Image, name="Alpha Image", update=U,
                                 description="Grey image whose brightness is the alpha (white = "
                                             "opaque), laid exactly like the image")
    map_location: V("Location", (0.0, 0.0, 0.0), "Move the image (in image coordinates)",
                    "TRANSLATION")
    map_rotation: V("Rotation", (0.0, 0.0, 0.0), "Rotate the image coordinates", "EULER")
    map_scale: FloatVectorProperty(name="Scale", subtype="XYZ", size=3, default=(1.0, 1.0, 1.0),
                                   description="Scale the image coordinates (2 = twice as many "
                                   "repeats)", update=_map_scale)
    map_scale_lock: BoolProperty(name="Uniform Scale", description="Scale all three axes together",
                                 default=False, update=_lock_scale)
    map_type: E(MAP_TYPES, "Transform", "POINT")
    panorama: B("Full Panorama", False, "The image is a whole equirectangular panorama (a "
                "baked sky): it covers the entire dome, ignoring the Dome settings")
    frame_duration: I("Frames", 1, "Number of images of a movie or sequence to use", min=0)
    frame_start: I("Start Frame", 1, "Global starting frame of the movie or sequence")
    frame_offset: I("Offset", 0, "Offset the number of the frame to use")
    use_cyclic: B("Cyclic", False, "Cycle the images in the movie")
    use_auto_refresh: B("Auto Refresh", True, "Always refresh the image on frame changes")
    solid_color: C("Color", (1.0, 0.95, 0.85))
    emission: F("Strength", 1.0, "Brightness of the sun image (1 = the image as it is). Clouds "
                "above dim it and sky layers above hide it, like the sun", min=0.0, soft_max=100.0)
    modifiers: CollectionProperty(type=DG_Modifier)
    alpha_modifiers: CollectionProperty(type=DG_Modifier)
    fog_mode: E(FOG_TYPES, "Fog", "GRADIENT")
    fog_color: C("Color", (0.75, 0.8, 0.85), "Fog colour (tints a fog image)")
    fog_density: UNIT("Density", 0.85, subtype="FACTOR")
    fog_top: A("Top", 10.0, "Elevation where the fog has faded out", 0.5, 90.0)
    fog_fade: A("Fade", 25.0, "How far below the top the fog starts to thin out", 0.1, 180.0)
    fog_falloff: E(FOG_FALLOFFS, "Falloff", "SMOOTHSTEP")
    fog_fit: E(FOG_FITS, "Fit", "FILL")
    fog_tile: A("Tile Height", 20.0, "Height of one tile in Repeat (width follows the image's "
                "proportions)", 0.5, 180.0)
    fog_rotation: A("Rotation", 0.0, "Turn the fog image around the dome")
    masks: CollectionProperty(type=DG_Mask)
    active_mask: ACTIVE("Mask")


class DG_SkyLayer(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="Sky Layer", update=_layer_name)
    kind: E(LAYER_KINDS, "Layer Type", "SKY")
    enabled: B("Enabled", True)
    opacity: UNIT("Opacity", 1.0, "Also a keyframable input on the dangus node group")
    blend: E(BLEND_MODES, "Blend", "MIX", "How this layer's sky combines with the layers below")
    sky_spin: A("Spin", 0.0, "Turn the ring of sky images around the dome (per second)",
                soft_min=radians(-5.0), soft_max=radians(5.0))
    items: CollectionProperty(type=DG_Item)
    active_item: ACTIVE("Image")
    masks: CollectionProperty(type=DG_Mask)
    active_mask: ACTIVE("Mask")
    sun_mode: E([("TIME", "Time of Day", "Place the sun by the time of day, latitude and day "
                  "of the year"),
                 ("ANGLES", "Angles", "Place the sun by azimuth and elevation")],
                "Position", "ANGLES")
    sun_hours: F("Time", 10.0, "Time of day in hours", min=0.0, max=24.0)
    sun_time: FloatProperty(
        name="Time", description="Time of day as hours.minutes (9.47 = 09:47). Press I over the "
        "slider to keyframe it, Alt+I to delete the keyframe",
        min=0.0, max=24.0, soft_min=0.0, soft_max=24.0, step=10, precision=2,
        get=lambda self: hours_to_hm(self.sun_hours), set=_set_hm)
    sun_latitude: A("Latitude", 45.0, "Where on Earth: 0 = equator, 90 = north pole", -90.0, 90.0)
    sun_day: I("Day of Year", 172, "1 = January 1st, 172 = midsummer (northern hemisphere)",
               min=1, max=365)
    sun_north: A("North", 90.0, "Azimuth of north in the scene (90 = +Y)")
    sun_azimuth: A("Azimuth", 30.0)
    sun_elevation: A("Elevation", 25.0, "", -90.0, 90.0)
    sun_size: A("Size", 2.0, "Apparent diameter of the disc (the real sun: 0.53 deg)", 0.05, 45.0)
    sun_softness: F("Softness", 0.25, "Width of the disc's soft edge, relative to its radius",
                    min=0.0, max=2.0)
    sun_strength: F("Clear Sky", 40.0, "Brightness of the disc where nothing hides it",
                    min=0.0, soft_max=1000.0)
    sun_cloud_strength: F("Through Clouds", 1.5, "Brightness of the disc behind fully opaque "
                          "cloud. In between, light falls off exponentially with the mask "
                          "(Beer-Lambert): Clear * (Clouds/Clear)^mask", min=0.0, soft_max=100.0)
    sun_glow: F("Glow", 0.6, "Brightness of the halo around the disc", min=0.0, soft_max=10.0)
    sun_glow_size: A("Glow Size", 12.0, "Angle where the halo is at half brightness", 0.5, 89.0)
    sun_scatter: F("Scatter", 1.0, "Forward scattering: thin cloud near the sun lights up "
                   "(silver linings). Strongest where the cloud cover is half", min=0.0,
                   soft_max=5.0)
    sun_relight_sky: B("Relight Sky", False, "The sun recolours the layers below it by its "
                       "elevation (dark at night, warm at sunset) and a physically based mask "
                       "towards the sun")
    sun_relight_amount: UNIT("Amount", 1.0, subtype="FACTOR")
    sun_relight_disc: B("Relight Sun", False, "The sun's own light takes the colour of sunlight "
                        "through the air: white high up, orange to red near the horizon")
    sun_relight_disc_amount: UNIT("Amount", 1.0, subtype="FACTOR")
    sun_night: UNIT("Night Level", 0.05, "Brightness of the layers below once the sun has set")
    sun_haze: F("Haze", 1.0, "How much the air reddens a low sun (scales the Rayleigh "
                "extinction)", min=0.0, soft_max=4.0)
    tree: PointerProperty(type=bpy.types.NodeTree, options={"HIDDEN"},
                          description="This layer's node subgroup")
    collection: PointerProperty(type=bpy.types.Collection, options={"HIDDEN"},
                                description="Holds this layer's cloud Empties")


class DG_Settings(bpy.types.PropertyGroup):
    layers: CollectionProperty(type=DG_SkyLayer)
    active_layer: ACTIVE("Sky Layer")
    edge_noise: E(EDGE_NOISES, "Noise", "NOISE", "Pattern that shapes the dissolve at image "
                  "seams", update=_edge_noise_changed)
    noise_scale: F("Scale", 4.0, "Size of the pattern that shapes the seams", min=0.1,
                   soft_max=30.0)
    edge_detail: F("Detail", 2.0, min=0.0, max=15.0)
    edge_roughness: UNIT("Roughness", 0.5, subtype="FACTOR")
    edge_distortion: F("Distortion", 0.0, soft_min=-5.0, soft_max=10.0)
    edge_randomness: UNIT("Randomness", 1.0, subtype="FACTOR")
    edge_frequency: F("Frequency", 2.0, min=0.0, soft_max=10.0)
    edge_anisotropy: UNIT("Anisotropy", 1.0, subtype="FACTOR")
    edge_low: F("Low", 0.3, "Pattern value where the seam is fully the left image",s
                soft_min=-1.0, soft_max=1.0)
    edge_high: F("High", 0.7, "Pattern value where the seam is fully the right image",
                 soft_min=-1.0, soft_max=1.0)
    edge_softness: F("Softness", 0.4, "Low = patchy dissolve, high = smooth fade", min=0.05,
                     max=2.0)
    sky_rotation: A("Rotation", 0.0, "Rotate the ring of images around the dome")
    vertical_mode: E([("FULL", "Fill Dome", "Stretch each image between Bottom and Top"),
                      ("ASPECT", "Keep Aspect", "Keep each image's proportions (e.g. 1 picture "
                       "tall, N around). Edge pixels extend above and below")], "Vertical", "FULL")
    sky_horizon: A("Horizon", 0.0, "Elevation where the bottom edge of the images sits",
                   -89.0, 89.0)
    z_scale: F("Z Scale", 1.0, "Vertical size multiplier in Keep Aspect mode", min=0.1,
               soft_max=4.0)
    sky_bottom: A("Bottom", 0.0, "Elevation mapped to the bottom edge of the images (the "
                  "horizon at the most)", -89.0, 89.0)
    sky_top: A("Top", 90.0, "Elevation mapped to the top edge of the images", 1.0, 90.0)
    ground_color: C("Ground", (0.02, 0.018, 0.015), "Below the horizon: the sky is a dome, "
                    "standing on this ground")
    ground_blur: A("Horizon Blur", 0.5, "How softly the dome meets the ground", 0.0, 20.0)
    strength: F("Strength", 1.0, min=0.0, soft_max=10.0)
    light_map: E([("AUTO", "Auto", "Blender's automatic size (can take many seconds per render)"),
                  ("512", "512", "Fastest"), ("1024", "1024", "Fast, sharp enough for a sun"),
                  ("2048", "2048", "Sharper sun shadows"), ("4096", "4096", "Slowest")],
                 "Light Map", "1024", "Size of the map Cycles samples world light from. Cycles "
                 "evaluates the whole sky into it before every render")
    cull_camera: B("Camera Culling", False, "Skip texture lookups for sky images outside the "
                   "camera's view (replaced by their average colour). Baked at build time: press "
                   "Rebuild after moving or re-aiming the camera")
    cull_margin: A("Margin", 15.0, "Extra angle kept around the camera's view", 0.0, 90.0)
    cull_animation: B("Whole Frame Range", True, "Take the camera's position over the whole "
                      "frame range into account")
    bake_path: StringProperty(name="File", subtype="FILE_PATH", default="//dangus_sky",
                              description="Where to save the baked sky (the extension is added)")
    bake_kind: EnumProperty(name="Bake", default="IMAGE", items=[
        ("IMAGE", "Image", "A panorama image, or an image sequence of the frame range", "IMAGE_DATA", 0),
        ("VIDEO", "Video", "A panorama video of the frame range", "FILE_MOVIE", 1)])
    bake_format: EnumProperty(name="Format", default="OPEN_EXR", items=[
        ("OPEN_EXR", "OpenEXR", "32-bit float: keeps the full brightness (best to load back)", 0),
        ("PNG", "PNG", "8-bit, clipped at 1", 1), ("JPEG", "JPEG", "8-bit, compressed", 2),
        ("TIFF", "TIFF", "16-bit", 4), ("HDR", "Radiance HDR", "Float, compact", 5),
        ("WEBP", "WebP", "8-bit, compressed", 6)])
    bake_video: EnumProperty(name="Video", default="MP4_H264", items=[
        ("MP4_H264", "MP4 (H.264)", "Plays everywhere"),
        ("MP4_H265", "MP4 (H.265)", "Smaller files, newer players"),
        ("MKV_AV1", "Matroska (AV1)", "Smallest files"),
        ("WEBM_VP9", "WebM (VP9)", "For the web"),
        ("MOV_PRORES", "QuickTime (ProRes)", "For editing"),
        ("MKV_FFV1", "Matroska (FFV1, lossless)", "No compression loss, large files")])
    bake_quality: EnumProperty(name="Quality", default="HIGH", items=[
        ("PERC_LOSSLESS", "Perceptually Lossless", ""), ("HIGH", "High", ""),
        ("MEDIUM", "Medium", ""), ("LOW", "Low", "")])
    bake_width: IntProperty(name="Width", default=4096, min=16, max=65536, subtype="PIXEL",
                            description="Panorama width (height is half of it)")
    bake_samples: IntProperty(name="Samples", default=8, min=1, max=4096,
                              description="Cycles samples per pixel")
    bake_animation: BoolProperty(name="Image Sequence", default=False,
                                 description="Bake every frame of the scene's frame range as "
                                             "numbered images")
    bake_load: BoolProperty(name="Load as Sky Layer", default=True,
                            description="Add the baked panorama as a new Sky Layer and hide the "
                                        "others")


EDGE_PROPS = ("edge_noise", "noise_scale", "edge_detail", "edge_roughness", "edge_distortion",
              "edge_randomness", "edge_frequency", "edge_anisotropy", "edge_low", "edge_high",
              "edge_softness")


class DG_UIState(bpy.types.PropertyGroup):
    show_layers: BoolProperty(name="Sky Layers", default=True)
    show_images: BoolProperty(name="Images", default=True)
    show_item: BoolProperty(name="Image Settings", default=True)
    show_item_masks: BoolProperty(name="Image Masks", default=False)
    show_layer_masks: BoolProperty(name="Layer Masks", default=False)
    show_edge: BoolProperty(name="Edge Mix", default=False)
    show_dome: BoolProperty(name="Dome", default=False)
    show_culling: BoolProperty(name="Performance", default=False)
    show_bake: BoolProperty(name="Bake", default=False)
    show_sun_position: BoolProperty(name="Position", default=True)
    show_sun_disc: BoolProperty(name="Disc & Glow", default=False)
    show_sun_brightness: BoolProperty(name="Brightness", default=False)
    show_sun_relight: BoolProperty(name="Relight", default=False)
    show_item_alpha: BoolProperty(name="Alpha", default=False)
    baking: BoolProperty(name="Baking", default=False, options={"SKIP_SAVE"})
    bake_progress: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0,
                                 subtype="FACTOR")
    bake_status: StringProperty(name="Status", default="")
    show_item_modifiers: BoolProperty(name="Image Modifiers", default=False)
    show_mask_modifiers: BoolProperty(name="Mask Modifiers", default=True)
    item_tab: EnumProperty(
        name="Image Tab", default="SETTINGS",
        items=[("SETTINGS", "Settings", "How the image is used in the sky", "PROPERTIES", 0),
               ("SOURCE", "Source", "The image itself and its Image Texture settings",
                "IMAGE_DATA", 1)])


classes = (DG_Modifier, DG_Mask, DG_Item, DG_SkyLayer, DG_Settings, DG_UIState)
