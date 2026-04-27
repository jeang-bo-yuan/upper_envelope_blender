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

from arrangement2D.upper_envelope import upper_envelope, get_plane_equation, point2D_solve_z
from arrangement2D import util
from arrangement2D.arrangement2D import arrangement2D
import arrangement2D.config as cfg
from shapely import Polygon
from shapely.strtree import STRtree
import bpy
import bmesh
from bpy.props import *
import math
from collections import defaultdict
import time

def PolygonsToVF(polygons: list[Polygon]):
    """
    將 Polygons 轉成 V 陣列（包含所有頂點的座標）、F陣列（每個面由哪些頂點組成）、VtoVid
    """
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

    return all_coords, faces_indices, coord_to_idx

def PolygonsToObj(polygons: list[Polygon], name: str):
    # 1. 準備純 Python 的清單 (速度極快)
    V, F, _ = PolygonsToVF(polygons)

    # 2. 一次性寫入 Mesh (這是 Blender 最快的寫入方式)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(V, [], F)
    mesh.update()

    # 建立 Object
    return bpy.data.objects.new(name, mesh)

def upper_envelope_face_fill_wall(polygons: list[Polygon], buffer_size: float, newObjName: str) -> bpy.types.Object:
    """
    UPPER ENVELOPE and fill vertical wall
    """
    polygons = [P for P in util.triangulate(polygons) if P.is_valid]

    # 取出每一面的 x y 座標
    edges : list[cfg.RAW_EDGE_TYPE] = []
    minZ = math.inf

    for poly in polygons:
        for i in range(1, len(poly.exterior.coords)):
            edges.append((
                poly.exterior.coords[i - 1][:2],    # 起點 xy
                poly.exterior.coords[i][:2]         # 終點 xy
            ))

            minZ = min(minZ, poly.exterior.coords[i][2])

    # Step 1. 做 Arrangement #############################################################################
    A = arrangement2D(edges)
    A = util.triangulate(A)

    # Step 2. Project Face and Record Vertex Height ######################################################
    # 給一個 (x, y) -> 一個列表包含所有高度
    vertex_height_list: defaultdict[cfg.RAW_POINT_TYPE, list[float]] = defaultdict(list)

    # 所有原始的面
    tree = STRtree([P.buffer(buffer_size) for P in polygons])
    
    # 結果
    result: list[Polygon] = []

    print("== Upper Envelope Face Fill Wall ==")
    print(f"\t#Arrangement / #Polygons: {len(A)} / {len(polygons)}")
    perf_start = time.perf_counter()
    project_fail = 0

    for arrangement in A:
        # 最好的投影、最好的投影的高度
        best_proj = [(co[0], co[1], minZ) for co in arrangement.exterior.coords]
        best_height = minZ

        # 找出被原始的哪些面覆蓋
        for i in tree.query(arrangement, predicate='covered_by'):
            plane_eq = get_plane_equation(polygons[i])
            
            # 實際投影一次
            proj = [(co[0], co[1], point2D_solve_z(co, plane_eq)) for co in arrangement.exterior.coords]
            height = sum(co[2] for co in proj) / len(proj) # 平均高度

            # 若更高
            if height > best_height:
                best_proj = proj
                best_height = height
        pass

        if best_height == minZ:
            print(f"Project Fail: {best_proj}")
            project_fail += 1
        
        # 對每個 vertex 看那個 (x, y) 是否有其他 a 投影過，如果有而且 z 差距小於 1e-4 則使用它
        # 做這步的用意：即使相鄰兩面原本是連起來的，但經過計算得到投影的 z 值可能會和原本的值有誤差
        for i, vert in enumerate(best_proj):
            do_snap = False

            for z in vertex_height_list[vert[:2]]:
                if abs(vert[2] - z) < 1e-4:
                    best_proj[i] = vert[:2] + (z,)
                    do_snap = True
                    break

            # 記錄 z 值
            if not do_snap:
                vertex_height_list[vert[:2]].append(vert[2])
        
        result.append(Polygon(best_proj))

    print(f"\t#Project Failed: {project_fail}")
    print(f"Project Face Height: {time.perf_counter() - perf_start}")
    perf_start = time.perf_counter()

    # Step 3. V, F, E -> E 的目的是補上垂直面的線
    V, F, VtoVid = PolygonsToVF(result)

    E = []
    for point2D, zList in vertex_height_list.items():
        if len(zList) > 1:
            zList = sorted(zList)

            for i in range(1, len(zList)):
                E.append((
                    VtoVid[point2D + (zList[i - 1], )],
                    VtoVid[point2D + (zList[i], )]
                ))

    # Step 4. 建 Mesh
    mesh = bpy.data.meshes.new(newObjName)
    mesh.from_pydata(V, E, F)
    mesh.update()
    newObj =  bpy.data.objects.new(newObjName, mesh)

    print(f"Add vertical edge && Create mesh: {time.perf_counter() - perf_start}")
    perf_start = time.perf_counter()

    # Step5. Fill Hole
    # 選 wire 和 boundary （但 Arrangement 中最外圍一圈不能選）
    boundary: set[cfg.RAW_EDGE_TYPE] = set()
    for arrangement in A:
        for i in range(1, len(arrangement.exterior.coords)):
            v1 = arrangement.exterior.coords[i - 1][:2]
            v2 = arrangement.exterior.coords[i][:2]

            # 非 boundary 會被兩個相鄰面共用
            if (v1, v2) in boundary and (v2, v1) in boundary:
                boundary.remove((v1, v2))
                boundary.remove((v2, v1))
            else:
                boundary.add((v1, v2))
                boundary.add((v2, v1))

    # 進入 Edit Mode
    bpy.context.scene.collection.objects.link(newObj)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = newObj
    newObj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    bpy.ops.mesh.select_mode(type='EDGE')
    bpy.ops.mesh.select_non_manifold(extend=False, use_wire=True, use_boundary=True, use_multi_face=False, use_non_contiguous=False, use_verts=False)

    bm = bmesh.from_edit_mesh(newObj.data)
    # 取消選擇 Arrangement 最外圍
    for edge in bm.edges:
        v1, v2 = edge.verts

        if (v1.co.to_tuple()[:2], v2.co.to_tuple()[:2]) in boundary:
            edge.select_set(False)
    
    # Fill Hole
    bpy.ops.mesh.fill_holes(sides=0)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.scene.collection.objects.unlink(newObj)

    print(f"Fill Wall: {time.perf_counter() - perf_start}")

    return newObj

# --------------------------------------------------
# Property
# --------------------------------------------------
class UPPERENV_PROP_find(bpy.types.PropertyGroup):
    """ Properties for find """
    project_method: EnumProperty(
        name="Project Method",
        description="在找 Upper Envelope 時如何將頂點投影回去",
        items=[("VERTEX", "VERTEX", "每個頂點分開投影。對於每個頂點向上投影到高度最高的平面。"), 
               ("FACE", "FACE", "以面為單位進行投影。對於每個面向上投影到高度最高的平面。"),
               ("FACE_FILL_WALL", "FACE_FILL_WALL", "以面為單位並補垂直牆")
               ],
        default="FACE_FILL_WALL"
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
        default=False
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
               ("FACE", "FACE", "以面為單位進行投影。對於每個面向上投影到高度最高的平面。"),
               ("FACE_FILL_WALL", "FACE_FILL_WALL", "以面為單位並補垂直牆")
               ],
        default="FACE_FILL_WALL"
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
        default=False
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
        if self.project_method == "FACE_FILL_WALL":
            newObj = upper_envelope_face_fill_wall(polygons, self.buffer_size, f"{obj.name} Upper Envelope")
        else:
            polygons = upper_envelope(polygons, buffer_size=self.buffer_size, project_method=self.project_method)
            newObj = PolygonsToObj(polygons, f"{obj.name} Upper Envelope")
        
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




