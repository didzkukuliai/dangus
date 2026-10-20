import bpy

from . import live, operators, properties, ui


_modules = (properties, operators, ui)


def register():
    for m in _modules:
        for cls in m.classes:
            bpy.utils.register_class(cls)
    bpy.types.Scene.dangus = bpy.props.PointerProperty(type=properties.DG_Settings)
    bpy.types.WindowManager.dangus_ui = bpy.props.PointerProperty(type=properties.DG_UIState)
    live.register()
    operators.register_keys()


def unregister():
    operators.unregister_keys()
    live.unregister()
    del bpy.types.WindowManager.dangus_ui
    del bpy.types.Scene.dangus
    for m in reversed(_modules):
        for cls in reversed(m.classes):
            bpy.utils.unregister_class(cls)
