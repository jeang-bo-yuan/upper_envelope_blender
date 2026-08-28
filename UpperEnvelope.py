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

import shapely
from shapely import Polygon
from shapely.strtree import STRtree
import numpy as np

import bpy
import bmesh
from bpy.props import *
from mathutils import Vector
import math

from typing import Literal
from collections import defaultdict
import time

RAW_POINT_TYPE = tuple[float, float, float]
RAW_EDGE_TYPE = tuple[RAW_POINT_TYPE, RAW_POINT_TYPE]
RAW_POINT2D_TYPE = tuple[float, float]
RAW_EDGE2D_TYPE = tuple[RAW_POINT2D_TYPE, RAW_POINT2D_TYPE]

class Util:
    @staticmethod
    def get_plane_equation(poly: Polygon) -> tuple[float, float, float, float]:
        """ 回傳 (a, b, c, d) 代表平面方程式 ax + by + cz + d = 0 """
        p0 = np.array(poly.exterior.coords[0], np.float64)
        p1 = np.array(poly.exterior.coords[1], np.float64)
        p2 = np.array(poly.exterior.coords[2], np.float64)

        a, b, c = np.cross(p1 - p0, p2 - p0)
        d = - np.dot([a, b, c], p0)

        return (float(a), float(b), float(c), float(d))

    @staticmethod
    def point2D_solve_z(point: RAW_POINT2D_TYPE, equation: tuple[float, float, float, float]) -> float:
        """ 給定 (x, y) 和平面方程，求出 z """
        # z = -(ax + by + d) / c
        x, y = point
        a, b, c, d = equation
        return -(a * x + b * y + d) / c

    @staticmethod
    def triangulate(polygons: list[Polygon]) -> list[Polygon]:
        return shapely.get_parts(
            shapely.constrained_delaunay_triangles(
                shapely.MultiPolygon(polygons)
            )
        ).tolist()

    @staticmethod
    def PolygonsToVF(polygons: list[Polygon]) -> tuple[list[RAW_POINT_TYPE], list[tuple[int, int, int]], dict[RAW_POINT_TYPE, int]]:
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

    @staticmethod
    def PolygonsToObj(polygons: list[Polygon], name: str) -> bpy.types.Object:
        """
        將一組的 3D Polygon 視做 Mesh 的面並建立 Blender Object
        """
        # 1. 準備純 Python 的清單 (速度極快)
        V, F, _ = Util.PolygonsToVF(polygons)

        # 2. 一次性寫入 Mesh (這是 Blender 最快的寫入方式)
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(V, [], F)
        mesh.update()

        # 建立 Object
        return bpy.data.objects.new(name, mesh)

def upper_envelope(polygons: list[Polygon]
                   , *
                   , buffer_size = 1e-15
                   , project_method: Literal['VERTEX', 'FACE', 'FACE_FILL_WALL'] = 'VERTEX'
                   , overrideMinZ: float | None = None
                   , newObjName: str = "Upper Envelope"
    ) -> bpy.types.Object:
    """
    Upper Envelope : 輸入一堆 mesh 的面，找到數個 open surface 把這些輸入的面給蓋住。

    輸入的面假設 xy 平面為地面，z 軸為高度（和 shapely 一樣）。

    演算法：
    1. 將每個面投影到 xy 平面，並求出 mesh arrangement 2D
    2. 對 arrangement 結果的每個頂點，看它被輸入的哪些面給覆蓋（cover），然後投影回去。當一個頂點被多個面覆蓋（cover）時，投影到最高的點上。

    :param buffer_size: 因為數值問題，在計算 mesh arrangement 時交點可能會偏離原直線一點點，導致 arrangement 的結果可能比原本輸入的三角面還要向外擴。
                        所以在把頂點投影回去時，把原本的每個平面在 XY 平面上都向外擴 buffer_size 的大小再做覆蓋（cover）檢測。

                        buffer_size 調大會把更多 arrangement 的面投影到同個平面上，結果「可能」會看起來更 low poly。
                        但是在遇到幾乎垂直的面時，反而會把旁邊的頂點拉到極端高的地方。
    :param overrideMinZ: 若不是 None，則將所有 Project 失敗的 VERTEX 或 FACE 移到 min(`min Z of polygons`, `overrideMinZ`)
    """
    # Note: 這是對 Polygon 投影到 xy 平面後的面積做過濾
    polygons = [P for P in polygons if P.area > 0]
    polygons = Util.triangulate(polygons)

    # 取出每一個邊的 x y 座標
    edges : list[shapely.LineString] = []
    minZ = overrideMinZ or math.inf

    for poly in polygons:
        for i in range(1, len(poly.exterior.coords)):
            edges.append(shapely.LineString([
                poly.exterior.coords[i - 1][:2],    # 起點 xy
                poly.exterior.coords[i][:2]         # 終點 xy
            ]))

            minZ = min(minZ, poly.exterior.coords[i][2])

    # Step 1. 做 Arrangement #############################################################################
    print("\n== Arrangement 2D (using shapely.unary_union + shapely.polygonize) ==")
    perf_start = time.perf_counter()
    arrangement2Ds = [P for P in 
            shapely.get_parts(shapely.polygonize(shapely.unary_union(edges).geoms))
            if isinstance(P, Polygon)
        ]
    arrangement2Ds = Util.triangulate(arrangement2Ds)
    print("Arrangement 2D: ", time.perf_counter() - perf_start, "s")

    # Step 2. Project Back ###############################################################################
    match project_method:
        case 'VERTEX':
            return project_vertex(polygons, arrangement2Ds, buffer_size, minZ, newObjName)

        case 'FACE':
            return project_face(polygons, arrangement2Ds, buffer_size, minZ, newObjName, fill_wall=False)

        case 'FACE_FILL_WALL':
            return project_face(polygons, arrangement2Ds, buffer_size, minZ, newObjName, fill_wall=True)
    
    raise ValueError(f"Unknown project method: {project_method}")

def project_vertex(polygons: list[Polygon], arrangement2Ds: list[Polygon], buffer_size: float, minZ: float, newObjName: str):
    """
    將 A 的每個頂點投影回 polygons 中最高的位置    
    """
    print("== Upper Envelope Project Vertex ==")
    print(f"\t#Arrangement / #Polygons: {len(arrangement2Ds)} / {len(polygons)}")
    perf_start = time.perf_counter()

    # Step 1. 將 Arrangement 中的每個平面的頂點投影回 3 維 ################################################
    projected_vertex_height = dict()
    point_set = set()
    # 先記錄所有頂點
    for a in arrangement2Ds:
        for i in range(1, len(a.exterior.coords)):
            projected_vertex_height[a.exterior.coords[i]] = minZ # 該頂點預設投回 minZ
            point_set.add(a.exterior.coords[i])

    points = [shapely.Point(p) for p in point_set]
    tree = STRtree(points)
    # 對於每個原始的面
    for poly in polygons:
        equation = Util.get_plane_equation(poly)

        poly_buffer = poly.buffer(buffer_size)
        shapely.prepare(poly_buffer)
        
        # 看它蓋住哪些點
        for i in tree.query(poly_buffer, predicate='covers'):
            point_co = points[i].coords[0]

            # 將這些點投影回該面並記錄最大值
            projected_vertex_height[point_co] = max(
                projected_vertex_height[point_co],
                Util.point2D_solve_z(point_co, equation)
            )

    print(f"Project Vertex Height: {time.perf_counter() - perf_start} s")
    perf_start = time.perf_counter()

    # Step 2. 建造結果 ###############################################################################
    projected_arrangements: list[Polygon] = []
    for a in arrangement2Ds:
        exterior = []
        for co in a.exterior.coords:
            exterior.append((co[0], co[1], projected_vertex_height[co]))

        projected_arrangements.append(Polygon(exterior))
    newObj = Util.PolygonsToObj(projected_arrangements, newObjName)
    print(f"Create Mesh: {time.perf_counter() - perf_start} s")

    return newObj

def project_face(polygons: list[Polygon], arrangement2Ds: list[Polygon], buffer_size: float, minZ: float, newObjName: str, *, fill_wall: bool = True) -> bpy.types.Object:
    """
    以面為單位進行投影
    """
    print(f"== Project Face {'(fill wall)' if fill_wall else ''} ==")
    print(f"\t#Arrangement / #Polygons: {len(arrangement2Ds)} / {len(polygons)}")
    perf_start = time.perf_counter()

    # Step 1. Project Face and Record Vertex Height ######################################################
    # 給一個 (x, y) -> 一個列表包含所有高度
    vertex_height_list: defaultdict[RAW_POINT2D_TYPE, list[float]] = defaultdict(list)

    # 所有原始的面
    tree = STRtree([P.buffer(buffer_size) for P in polygons])
    
    # 結果
    projected_arrangements: list[Polygon] = []
    project_fail = 0

    for arrangement in arrangement2Ds:
        # 最好的投影、最好的投影的高度
        best_proj = [(co[0], co[1], minZ) for co in arrangement.exterior.coords]
        best_height = minZ

        # 找出被原始的哪些面覆蓋
        for i in tree.query(arrangement, predicate='covered_by'):
            plane_eq = Util.get_plane_equation(polygons[i])
            
            # 實際投影一次
            proj = [(co[0], co[1], Util.point2D_solve_z(co, plane_eq)) for co in arrangement.exterior.coords]
            height = sum(co[2] for co in proj) / len(proj) # 平均高度

            # 若更高
            if height > best_height:
                best_proj = proj
                best_height = height
        pass

        if best_height == minZ:
            # print(f"Project Fail: {best_proj}")
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
        
        projected_arrangements.append(Polygon(best_proj))

    print(f"\t#Project Failed: {project_fail}")
    print(f"Project Face Height: {time.perf_counter() - perf_start} s")
    perf_start = time.perf_counter()

    # Step 2. V, F, E -> E 的目的是補上垂直面的線 #############################################
    V, F, VtoVid = Util.PolygonsToVF(projected_arrangements)

    E = []
    if fill_wall:
        # 只有要補牆面才補上垂直面的線
        for point2D, zList in vertex_height_list.items():
            if len(zList) > 1:
                zList = sorted(zList)

                for i in range(1, len(zList)):
                    E.append((
                        VtoVid[point2D + (zList[i - 1], )],
                        VtoVid[point2D + (zList[i], )]
                    ))

    # Step 3. 建 Mesh ######################################################################
    mesh = bpy.data.meshes.new(newObjName)
    mesh.from_pydata(V, E, F)
    mesh.update()
    newObj =  bpy.data.objects.new(newObjName, mesh)

    print(f"{'Add vertical edge && ' if fill_wall else ''}Create mesh: {time.perf_counter() - perf_start} s")
    perf_start = time.perf_counter()

    if not fill_wall:
        return newObj

    # Step4. Fill Wall #####################################################################
    # 選 wire 和 boundary （但 Arrangement 中最外圍一圈不能選）
    boundary: set[RAW_EDGE_TYPE] = set()
    for arrangement in arrangement2Ds:
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

    print(f"Fill Wall: {time.perf_counter() - perf_start} s")

    return newObj

# --------------------------------------------------
# Property
# --------------------------------------------------
class UPPERENV_PROP_find(bpy.types.PropertyGroup):
    """ Properties for finding upper envelope """
    project_method: EnumProperty(
        name="Project Method",
        description="在找 Upper Envelope 時如何將頂點投影回去",
        items=[("VERTEX", "VERTEX", "每個頂點分開投影。對於每個頂點向上投影到高度最高的平面。"), 
               ("FACE", "FACE", "以面為單位進行投影。對於每個面向上投影到高度最高的平面。"),
               ("FACE_FILL_WALL", "FACE_FILL_WALL", "以面為單位並補垂直牆")
               ],
        default="FACE_FILL_WALL"
    ) #type: ignore

    do_cleanup: BoolProperty(
        name="Do Cleanup",
        description="是否對 Upper Envelope 的結果清理過多的頂點。 !!!WARNING!!!: 清理的結果可能會影響拓樸。",
        default=True
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

    snap_grid_size: FloatProperty(
        name="Snap Grid Size",
        description="若值 > 0，則對輸入的模型做 snapping 再找 upper envelope，snapping 時將每個頂點的座標貼到 grid size 的倍數",
        min=0,
        default=1e-3
    ) #type: ignore

    overrideMinZ: BoolProperty(
        name="Override Min Z",
        description="是否覆寫 Min Z",
        default=False
    ) #type: ignore

    minZ: FloatProperty(
        name="minZ",
        description="如果 overrideMinZ 為 True，則將所有 Project 失敗的 VERTEX 或 FACE 移到 min(`模型的 min Z`, `overrideMinZ`)",
        default=0
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

    do_cleanup: BoolProperty(
        name="Do Cleanup",
        description="是否對 Upper Envelope 的結果清理過多的頂點。 !!!WARNING!!!: 清理的結果可能會影響拓樸。",
        default=True
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

    snap_grid_size: FloatProperty(
        name="Snap Grid Size",
        description="若值 > 0，則對輸入的模型做 snapping 再找 upper envelope，snapping 時將每個頂點的座標貼到 grid size 的倍數",
        min=0,
        default=1e-3
    ) #type: ignore

    overrideMinZ: BoolProperty(
        name="Override Min Z",
        description="是否覆寫 Min Z",
        default=False
    ) #type: ignore

    minZ: FloatProperty(
        name="minZ",
        description="如果 overrideMinZ 為 True，則將所有 Project 失敗的 VERTEX 或 FACE 移到 min(`模型的 min Z`, `overrideMinZ`)",
        default=0
    ) #type: ignore

    @classmethod
    def poll(cls, context):
        return context.object != None and context.object.mode == 'OBJECT'

    def execute(self, context):
        perf_start = time.perf_counter()

        original_name = context.object.name

        # 複製一份
        bpy.ops.object.select_all(action='DESELECT')
        context.object.select_set(True)
        bpy.ops.object.duplicate()

        # Snap
        self.SnapActiveObject(context)

        bpy.ops.object.mode_set(mode='EDIT')
        # 做 3D Mesh Arrangement
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.intersect(mode='SELECT', separate_mode='NONE')
        # 做 Triangulate
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # 調 buffer_size
        if self.auto_buffer_size:
            self.buffer_size = 1e-15 if self.project_method == 'VERTEX' else 1e-10

        obj = context.object
        # 找 Upper Envelope
        newObj = self.ObjFindUpperEnvelope(obj, original_name)
        if self.do_cleanup:
            self.CleanUp(newObj)

        # 刪 obj
        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.delete(use_global=True)

        # 選 newObj
        newObj.select_set(True)
        context.view_layer.objects.active = newObj

        print("[Time] Total: ", time.perf_counter() - perf_start, "s")
        return {'FINISHED'}
    
    def SnapActiveObject(self, context: bpy.types.Context):
        """ 對 active obj 上每個頂點做 snapping """
        if self.snap_grid_size > 0:
            for vert in context.object.data.vertices:
                vert.co = Vector([math.floor(val / self.snap_grid_size) * self.snap_grid_size for val in vert.co])

            context.object.data.update()
    
    def ObjFindUpperEnvelope(self, obj: bpy.types.Object, original_name: str) -> bpy.types.Object:
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
        overrideMinZ = self.minZ if self.overrideMinZ else None
        newObj = upper_envelope(polygons
                                , buffer_size=self.buffer_size
                                , project_method=self.project_method
                                , overrideMinZ=overrideMinZ
                                , newObjName=f"{original_name} Upper Envelope {self.project_method}")
        
        ################################################################################
        for C in obj.users_collection:
            C.objects.link(newObj)

        return newObj

    def CleanUp(self, obj: bpy.types.Object):
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
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='SELECT')

        # 三角化
        bpy.ops.mesh.quads_convert_to_tris()
        # Merge by Distance
        bpy.ops.mesh.remove_doubles()

        bm = bmesh.from_edit_mesh(obj.data)
        # 1. 刪除面積為 0 的線和邊
        old_edge_len = len(bm.edges)
        while True:
            bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.0001)

            if len(bm.edges) == old_edge_len: # 重覆直到沒有邊被刪
                break

            old_edge_len = len(bm.edges)

        # 2. 清理連接多個面的邊
        # target_edges = [e for e in bm.edges if len(e.link_faces) > 2]
        # disconnected_edges = bmesh.ops.split_edges(bm, edges=target_edges)['edges']
        # disconnected_edges = [e for e in disconnected_edges if len(e.link_faces) == 1]
        # bmesh.ops.delete(bm, geom=disconnected_edges, context='EDGES')

        # 3. 刪除 wire 和沒連接邊的點
        while wire_edges := [e for e in bm.edges if not e.link_faces]:
            bmesh.ops.delete(bm, geom=wire_edges, context='EDGES')
        while lone_verts := [v for v in bm.verts if not v.link_edges]:
            bmesh.ops.delete(bm, geom=lone_verts, context='VERTS')

        bmesh.update_edit_mesh(obj.data)
        
        # Limited dissolve
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.dissolve_limited()

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
        op.snap_grid_size = prop.snap_grid_size
        op.overrideMinZ = prop.overrideMinZ
        op.minZ = prop.minZ


        layout.separator(type='LINE')
        layout.label(text="Settings:")
        layout.prop(prop, 'project_method')

        layout.separator(type='SPACE')
        layout.label(text='Precision')
        layout.prop(prop, 'auto_buffer_size')
        if not prop.auto_buffer_size:
            layout.prop(prop, 'buffer_size')
        layout.prop(prop, 'snap_grid_size')

        layout.separator(type='SPACE')
        layout.label(text='Post Process')
        layout.prop(prop, 'do_cleanup')
        layout.prop(prop, 'overrideMinZ')
        if prop.overrideMinZ:
            layout.prop(prop, 'minZ')

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




