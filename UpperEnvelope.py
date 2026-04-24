bl_info = {
    "name": "Upper Envelope",
    "author": "Bo Yuan, Jiang",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > My Addon",
    "description": "Template addon with Panel, Operator, and Header button",
    "category": "3D View",
    "doc_url": "https://github.com/jeang-bo-yuan/upper_envelope_blender",
}

from arrangement2D.upper_envelope import upper_envelope
import arrangement2D.config as cfg
import bpy
import bmesh
from shapely import Polygon
from bpy.props import *
import math

# --------------------------------------------------
# Property
# --------------------------------------------------
class UPPERENV_PROP_find(bpy.types.PropertyGroup):
    """ Properties for find """
    project_method: EnumProperty(
        name="Project Method",
        description="在找 Upper Envelope 時如何將頂點投影回去",
        items=[("VERTEX", "VERTEX", "每個頂點分開投影。對於每個頂點向上投影到高度最高的平面。"), 
               ("FACE", "FACE", "以面為單位進行投影。對於每個面向上投影到高度最高的平面。")],
        default="VERTEX"
    ) #type: ignore

    buffer_size: FloatProperty(
        name="Buffer Size",
        description="""
因為數值問題，在計算 mesh arrangement 時交點可能會偏離原直線一點點，導致 arrangement 的結果可能比原本輸入的三角面還要向外擴。
所以在把頂點投影回去時，把原本的每個平面在 XY 平面上都向外擴 buffer_size 的大小再做覆蓋（cover）檢測。

buffer_size 調大會把更多 arrangement 的面投影到同個平面上，結果「可能」會看起來更 low poly。
但是在遇到幾乎垂直的面時，反而會把旁邊的頂點拉到極端高的地方。
""",
        default=1e-15
    ) #type: ignore

    auto_buffer_size: BoolProperty(
        name="Auto Adjust Buffer Size",
        description="在 Project Method 為 VERTEX 時 Buffer Size 設成 1e-15，在 Project Method 為 FACE 時設成 1e-10",
        default=True
    ) #type: ignore

    do_cleanup: BoolProperty(
        name="Do Cleanup",
        description="是否對 Upper Envelope 的結果清理過多的頂點。 !!!WARNING!!!: 清理的結果可能會影響拓樸。",
        default=True
    ) #type: ignore


# --------------------------------------------------
# Operator
# --------------------------------------------------

class UPPERENV_OT_find(bpy.types.Operator):
    """ Find the upper envelope """
    bl_idname = "upperenv.find_upper_envelope"
    bl_label = "Find the upper envelope"
    bl_options = {'REGISTER', 'UNDO'}

    project_method: EnumProperty(
        name="Project Method",
        description="在找 Upper Envelope 時如何將頂點投影回去",
        items=[("VERTEX", "VERTEX", "每個頂點分開投影。對於每個頂點向上投影到高度最高的平面。"), 
               ("FACE", "FACE", "以面為單位進行投影。對於每個面向上投影到高度最高的平面。")],
        default="VERTEX"
    ) #type: ignore

    buffer_size: FloatProperty(
        name="Buffer Size",
        description="""
因為數值問題，在計算 mesh arrangement 時交點可能會偏離原直線一點點，導致 arrangement 的結果可能比原本輸入的三角面還要向外擴。
所以在把頂點投影回去時，把原本的每個平面在 XY 平面上都向外擴 buffer_size 的大小再做覆蓋（cover）檢測。

buffer_size 調大會把更多 arrangement 的面投影到同個平面上，結果「可能」會看起來更 low poly。
但是在遇到幾乎垂直的面時，反而會把旁邊的頂點拉到極端高的地方。
""",
        default=1e-15
    ) #type: ignore

    auto_buffer_size: BoolProperty(
        name="Auto Adjust Buffer Size",
        description="在 Project Method 為 VERTEX 時 Buffer Size 設成 1e-15，在 Project Method 為 FACE 時設成 1e-10",
        default=True
    ) #type: ignore

    do_cleanup: BoolProperty(
        name="Do Cleanup",
        description="是否對 Upper Envelope 的結果清理過多的頂點。 !!!WARNING!!!: 清理的結果可能會影響拓樸。",
        default=True
    ) #type: ignore

    @classmethod
    def poll(cls, context):
        return context.object != None and context.object.mode == 'OBJECT'

    def execute(self, context):
        old_debug = cfg.DEBUG, cfg.DEBUG_PLOT
        cfg.DEBUG, cfg.DEBUG_PLOT = (True, False)
        
        if self.auto_buffer_size:
            self.buffer_size = 1e-15 if self.project_method == 'VERTEX' else 1e-10

        newObj = self.ObjFindUpperEnvelope(context.object)
        if self.do_cleanup:
            self.cleanup(newObj)

        cfg.DEBUG, cfg.DEBUG_PLOT = old_debug
        return {'FINISHED'}
    
    def ObjFindUpperEnvelope(self, obj: bpy.types.Object) -> bpy.types.Object:
        """
        傳入一個 Object，找 Upper Envelope，然後建立新的物件
        """
        # 提取 Polygon ################################################################
        polygons = []

        for P in obj.data.polygons:
            exterior = []
            for vid in P.vertices:
                exterior.append(obj.matrix_world @ obj.data.vertices[vid].co)

            shapelyP = Polygon(exterior)
            if shapelyP.is_valid:
                polygons.append(shapelyP)

        # 生成 upper envelope ###########################################################
        polygons = upper_envelope(polygons, buffer_size=self.buffer_size, project_method=self.project_method)

        # Polygon To Mesh ###############################################################        
        # 1. 準備純 Python 的清單 (速度極快)
        all_coords = []      # 儲存 (x, y, z)
        faces_indices = []   # 儲存頂點的索引 [ [0, 1, 2], [2, 3, 4], ... ]
        coord_to_idx = {}    # 快速存取 vertex index

        for P in polygons:
            current_face = []
            # P.exterior.coords 頭尾相同，所以我們取到倒數第二個
            for coord in P.exterior.coords[:-1]:
                if coord not in coord_to_idx:
                    coord_to_idx[coord] = len(all_coords)
                    all_coords.append(coord)
                current_face.append(coord_to_idx[coord])
            
            faces_indices.append(current_face)

        # 2. 一次性寫入 Mesh (這是 Blender 最快的寫入方式)
        name = f"{obj.name} Upper Envelope"
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(all_coords, [], faces_indices)
        mesh.update()
        # 建立 Object
        newObj = bpy.data.objects.new(name, mesh)
        
        ################################################################################
        for C in obj.users_collection:
            C.objects.link(newObj)

        return newObj

    def cleanup(self, obj: bpy.types.Object):
        """
        清理過多的頂點但可能影響拓樸
        """
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        # Decimate Planar
        decimate_modifier = obj.modifiers.new(name="Decimate", type='DECIMATE')
        decimate_modifier.decimate_type = 'DISSOLVE'
        decimate_modifier.angle_limit = math.radians(5)
        bpy.ops.object.modifier_apply(modifier="Decimate")

        bpy.ops.object.mode_set(mode='EDIT')
        # 三角化
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.quads_convert_to_tris()

        bm = bmesh.from_edit_mesh(obj.data)
        # 1. 刪除面積為 0 的線和邊
        old_edge_len = len(bm.edges)
        while True:
            bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.0001)

            if len(bm.edges) == old_edge_len: # 重覆直到沒有邊被刪
                break

            old_edge_len = len(bm.edges)

        # 2. 清理連接多個面的邊
        target_edges = [e for e in bm.edges if len(e.link_faces) > 2]
        disconnected_edges = bmesh.ops.split_edges(bm, edges=target_edges)['edges']
        disconnected_edges = [e for e in disconnected_edges if len(e.link_faces) == 1]
        bmesh.ops.delete(bm, geom=disconnected_edges, context='EDGES')

        # 3. 刪除 wire 和沒連接邊的點
        wire_edges = [e for e in bm.edges if not e.link_faces]
        bmesh.ops.delete(bm, geom=wire_edges, context='EDGES')
        lone_verts = [v for v in bm.verts if not v.link_edges]
        bmesh.ops.delete(bm, geom=lone_verts, context='VERTS')

        bpy.ops.object.mode_set(mode='OBJECT')

# --------------------------------------------------
# Panel (Sidebar / N-panel)
# --------------------------------------------------

class UPPERENV_PT_panel(bpy.types.Panel):
    bl_label = "Upper Envelope"
    bl_idname = "UPPERENV_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Upper Envelope"

    def draw(self, context):
        prop: UPPERENV_PROP_find = context.scene.upperenv_settings

        layout = self.layout
        layout.label(text="Hello My Addon 👋")
        op = layout.operator(UPPERENV_OT_find.bl_idname, icon='PLAY')
        op.project_method = prop.project_method
        op.buffer_size = prop.buffer_size
        op.auto_buffer_size = prop.auto_buffer_size
        op.do_cleanup = prop.do_cleanup

        layout.separator(type='LINE')
        layout.label(text="Settings:")
        layout.prop(prop, 'project_method')
        layout.prop(prop, 'auto_buffer_size')
        if not prop.auto_buffer_size:
            layout.prop(prop, 'buffer_size')
        layout.prop(prop, 'do_cleanup')

# --------------------------------------------------
# Register / Unregister
# --------------------------------------------------

classes = (
    UPPERENV_PROP_find,
    UPPERENV_OT_find,
    UPPERENV_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.upperenv_settings = PointerProperty(type=UPPERENV_PROP_find)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.upperenv_settings

if __name__ == "__main__":
    register()




