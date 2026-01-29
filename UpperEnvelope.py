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
import bpy
import bmesh
from shapely import Polygon

def PolygonsToUpperEnvelopeObj(name: str, polygons: list[Polygon]) -> bpy.types.Object:
    polygons = upper_envelope(polygons, buffer_size=1e-15)

    # 建立 Mesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    for P in polygons:
        verts = []

        # 把它的每個頂點存入 bmesh
        for coord in P.exterior.coords:
            vertData = bm.verts.new((coord[0], coord[1], coord[2]))
            verts.append(vertData)
        bm.verts.ensure_lookup_table()

        # 建立 edge 和 face
        for i in range(len(verts)):
            bm.edges.new([verts[i], verts[(i + 1) % len(verts)]])
        bm.edges.ensure_lookup_table()
        
        bm.faces.new(verts)
        bm.faces.ensure_lookup_table()

    # 刪除多餘的頂點
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.0001)

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
            exterior.append(obj.data.vertices[vid].co)

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
        return context.object != None

    def execute(self, context):
        ObjFindUpperEnvelope(context.object)
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




