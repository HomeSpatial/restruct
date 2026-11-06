# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "HomeSpatial Structured Model",
    "author": "HomeSpatial",
    "version": (0, 3, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > HomeSpatial",
    "description": "Agent-first structured-model import, evidence, edits and checked GLB export",
    "category": "Import-Export",
}

import json
import bpy
from bpy.props import StringProperty, FloatProperty, FloatVectorProperty, EnumProperty, BoolProperty, PointerProperty
from . import api


def _publish(result):
    value = json.dumps(result, ensure_ascii=False, indent=2)
    bpy.context.scene["hs_last_result"] = value
    text = bpy.data.texts.get("HomeSpatial Agent Result.json") or bpy.data.texts.new("HomeSpatial Agent Result.json")
    text.clear()
    text.write(value)


class HS_Settings(bpy.types.PropertyGroup):
    bundle_dir: StringProperty(name="模型目录", subtype="DIR_PATH")
    reference_glb: StringProperty(name="扫描参考（可选）", subtype="FILE_PATH")
    object_id: StringProperty(name="对象 ID")
    dimensions: FloatVectorProperty(name="尺寸（米）", size=3, default=(1, 1, 1), min=.000001)
    change_x: BoolProperty(name="X", default=True)
    change_y: BoolProperty(name="Y", default=True)
    change_z: BoolProperty(name="Z", default=True)
    anchor: EnumProperty(name="固定端", items=[(v, v, v) for v in ("MIN", "CENTER", "MAX")])
    axis: EnumProperty(name="分区轴", items=[(v, v, v) for v in ("X", "Y", "Z")])
    offset: FloatProperty(name="分界坐标（米）")
    negative: FloatVectorProperty(name="负侧颜色", subtype="COLOR_GAMMA", size=3, min=0, max=1, default=(.65, .67, .65))
    positive: FloatVectorProperty(name="正侧颜色", subtype="COLOR_GAMMA", size=3, min=0, max=1, default=(.84, .84, .80))
    roughness: FloatProperty(name="粗糙度", min=0, max=1, default=.65)
    output_glb: StringProperty(name="导出 GLB", subtype="FILE_PATH")
    output_blend: StringProperty(name="保存工程", subtype="FILE_PATH")
    overwrite: BoolProperty(name="允许覆盖所选输出", default=False)


class HS_OT_command(bpy.types.Operator):
    bl_idname = "homespatial.command"
    bl_label = "HomeSpatial JSON Command"
    request_json: StringProperty()

    def execute(self, context):
        try:
            result = api.dispatch(json.loads(self.request_json))
        except (ValueError, RuntimeError) as error:
            result = {"ok": False, "error": {"code": "EXECUTION", "message": str(error)}}
        _publish(result)
        if not result["ok"]:
            self.report({"ERROR"}, result["error"]["message"])
            return {"CANCELLED"}
        return {"FINISHED"}


class HS_OT_action(bpy.types.Operator):
    bl_idname = "homespatial.action"
    bl_label = "HomeSpatial Action"
    action: StringProperty()

    def execute(self, context):
        s = context.scene.hs_settings
        if self.action == "active":
            obj = context.active_object
            if obj is None or not obj.get("hs_id") or obj.get("hs_role") != "model":
                self.report({"ERROR"}, "请选择模型对象")
                return {"CANCELLED"}
            s.object_id = obj["hs_id"]
            s.dimensions = api.inspect_object(s.object_id)["dimensions_m"]
            return {"FINISHED"}
        requests = {
            "open": {"op": "open_project", "args": {"bundle_dir": bpy.path.abspath(s.bundle_dir),
                     "reference_glb": bpy.path.abspath(s.reference_glb) if s.reference_glb else None}},
            "photos": {"op": "photos", "args": {"object_id": s.object_id}},
            "inspect": {"op": "inspect_object", "args": {"object_id": s.object_id}},
            "dimensions": {"op": "set_dimensions", "args": {"object_id": s.object_id,
                           "dimensions_m": [v if use else None for v, use in zip(s.dimensions, (s.change_x, s.change_y, s.change_z))], "anchor": s.anchor}},
            "partition": {"op": "partition_material", "args": {"object_id": s.object_id, "axis": s.axis,
                          "offset_m": s.offset, "negative": {"base_color_srgb": list(s.negative), "roughness": s.roughness},
                          "positive": {"base_color_srgb": list(s.positive), "roughness": s.roughness}}},
            "validate": {"op": "validate"},
            "export": {"op": "export_glb", "args": {"filepath": bpy.path.abspath(s.output_glb), "overwrite": s.overwrite}},
            "save": {"op": "save_project", "args": {"filepath": bpy.path.abspath(s.output_blend), "overwrite": s.overwrite}},
        }
        if context.scene.get("hs_scene_model"):
            parameters=json.loads(context.scene["hs_scene_model"])
            requests["rebuild"]={"op":"rebuild_scene"}
            requests["capture"]={"op":"capture_transforms","args":{"object_ids":[o["hs_id"] for o in context.selected_objects if o.get("hs_role")=="model"],"expected_revision":parameters["revision"]}}
        result = api.dispatch(requests[self.action])
        _publish(result)
        if not result["ok"]:
            self.report({"ERROR"}, result["error"]["message"])
            return {"CANCELLED"}
        if self.action == "photos":
            context.scene["hs_photo_results"] = json.dumps(result["data"])
        self.report({"INFO"}, "完成；详细结果见 HomeSpatial Agent Result.json")
        return {"FINISHED"}


class HS_PT_panel(bpy.types.Panel):
    bl_label = "HomeSpatial Model"
    bl_idname = "HS_PT_model"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HomeSpatial"

    def draw(self, context):
        layout, s = self.layout, context.scene.hs_settings
        box = layout.box()
        box.prop(s, "bundle_dir")
        box.prop(s, "reference_glb")
        box.operator("homespatial.action", text="导入并对齐").action = "open"
        if not context.scene.get("hs_project"):
            return
        box = layout.box()
        box.prop(s, "object_id")
        box.operator("homespatial.action", text="使用选中对象").action = "active"
        row = box.row()
        row.operator("homespatial.action", text="查看对象").action = "inspect"
        row.operator("homespatial.action", text="关联照片").action = "photos"
        for photo in json.loads(context.scene.get("hs_photo_results", "[]")):
            button = box.operator("homespatial.command", text=f"照片 {photo['frame_id']} · 可见采样 {photo['visible_samples']}")
            button.request_json = json.dumps({"op": "open_photo", "args": {"frame_id": photo["frame_id"]}})
        box.label(text="照片加载到图像编辑器；路径见结果 JSON")
        box = layout.box()
        if context.scene.get("hs_scene_model"):
            parameters=json.loads(context.scene["hs_scene_model"])
            box.label(text=f"参数场景 · 版本 {parameters['revision']}")
            box.label(text="尺寸与共享边界通过 JSON 参数编辑")
            box.operator("homespatial.action",text="回写选中对象的变换").action="capture"
            box.operator("homespatial.action",text="从当前参数重新构建").action="rebuild"
        else:
            box.label(text="尺寸编辑 · 对齐坐标轴包围盒")
            box.prop(s, "dimensions")
            row = box.row()
            for key in ("change_x", "change_y", "change_z"):
                row.prop(s, key)
            box.prop(s, "anchor")
            box.operator("homespatial.action", text="应用尺寸").action = "dimensions"
            box = layout.box()
            box.prop(s, "axis")
            box.prop(s, "offset")
            box.prop(s, "negative")
            box.prop(s, "positive")
            box.prop(s, "roughness")
            box.operator("homespatial.action", text="精确材质分区").action = "partition"
        box = layout.box()
        box.operator("homespatial.action", text="检查模型").action = "validate"
        box.prop(s, "output_glb")
        box.prop(s, "output_blend")
        box.prop(s, "overwrite")
        box.operator("homespatial.action", text="检查并导出 GLB").action = "export"
        box.operator("homespatial.action", text="保存 .blend 工程").action = "save"


CLASSES = (HS_Settings, HS_OT_command, HS_OT_action, HS_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hs_settings = PointerProperty(type=HS_Settings)


def unregister():
    del bpy.types.Scene.hs_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
