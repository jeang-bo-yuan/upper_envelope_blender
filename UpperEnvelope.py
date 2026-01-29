bl_info = {
    "name": "Upper Envelope",
    "author": "Bo Yuan, Jiang",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > My Addon",
    "description": "Template addon with Panel, Operator, and Header button",
    "category": "3D View",
}

from arrangement2D.upper_envelope import upper_envelope
import arrangement2D.config as cfg
import bpy
import bmesh
from shapely import Polygon

def PolygonsToUpperEnvelopeObj(name: str, polygons: list[Polygon]) -> bpy.types.Object:
    polygons = upper_envelope(polygons, buffer_size=1e-15)

    ####################################################################
    # Polygon To Mesh
    ####################################################################
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
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(all_coords, [], faces_indices)
    mesh.update()

    ####################################################################
    # 清理
    ####################################################################
    bm = bmesh.new()
    bm.from_mesh(mesh)
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

    bm.to_mesh(mesh)
    bm.free()

    # 建立 Object
    obj = bpy.data.objects.new(name, mesh)
    return obj

def ObjFindUpperEnvelope(obj: bpy.types.Object) -> bpy.types.Object:
    # 提取 Polygon
    polygons = []

    for P in obj.data.polygons:
        exterior = []
        for vid in P.vertices:
            exterior.append(obj.matrix_world @ obj.data.vertices[vid].co)

        shapelyP = Polygon(exterior)
        if shapelyP.is_valid:
            polygons.append(shapelyP)

    # 生成 upper envelope
    newObj = PolygonsToUpperEnvelopeObj(f"{obj.name} Upper Envelope", polygons)
    
    for C in obj.users_collection:
        C.objects.link(newObj)

    return newObj



# --------------------------------------------------
# Operator
# --------------------------------------------------

class UPPERENV_OT_find(bpy.types.Operator):
    """ Find the upper envelope """
    bl_idname = "upperenv.find_upper_envelope"
    bl_label = "Find the upper envelope"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object != None and context.object.mode == 'OBJECT'

    def execute(self, context):
        old_debug = cfg.DEBUG, cfg.DEBUG_PLOT
        cfg.DEBUG, cfg.DEBUG_PLOT = (True, False)
        
        ObjFindUpperEnvelope(context.object)

        cfg.DEBUG, cfg.DEBUG_PLOT = old_debug
        return {'FINISHED'}

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
        layout = self.layout
        layout.label(text="Hello My Addon 👋")
        layout.operator("upperenv.find_upper_envelope", icon='PLAY')

# --------------------------------------------------
# Register / Unregister
# --------------------------------------------------

classes = (
    UPPERENV_OT_find,
    UPPERENV_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()




