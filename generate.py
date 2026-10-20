"""Procedural voxel-city chunk generator (headless Blender / bpy).

Builds a night-time cyberpunk city block grid with procedural emissive
windows, streets, lane lights and street lamps, then renders a preview.
Run: blender --background --python generate.py
"""
import bpy
import math
import os
import random
from mathutils import Vector

# ---------------------------------------------------------------- config
# Write outputs next to this script (was hardcoded to ~/workspace/voxel-city)
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_DIR = os.path.join(OUT_DIR, "renders")
os.makedirs(RENDER_DIR, exist_ok=True)

SEED = 80
random.seed(SEED)

N_BLOCKS = 5          # blocks per side
BLOCK = 30.0          # block size
STREET = 12.0         # street width
PITCH = BLOCK + STREET
CITY = N_BLOCKS * PITCH
HALF = CITY / 2.0

SAMPLES = 64
RES_X, RES_Y = 1280, 720


# ---------------------------------------------------------------- helpers
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    # purge orphan data
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.curves):
        for x in list(coll):
            coll.remove(x)


def box(name, loc, dims, mat=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.dimensions = dims
    # keep base on the ground: location z is center, so lift by dims.z/2
    o.location.z = loc[2]
    if mat:
        o.data.materials.append(mat)
    return o


def new_material(name):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    return mat, nt


def make_building_material():
    mat, nt = new_material("Building")
    n = nt.nodes
    l = nt.links
    out = n.new("ShaderNodeOutputMaterial")

    geo = n.new("ShaderNodeNewGeometry")
    # world-space position: the boxes are unit cubes with object-level scale,
    # so transform local position into world units for a uniform window grid
    vt = n.new("ShaderNodeVectorTransform")
    vt.vector_type = "VECTOR"
    vt.convert_from = "OBJECT"
    vt.convert_to = "WORLD"
    l.new(geo.outputs["Position"], vt.inputs["Vector"])
    sep_c = n.new("ShaderNodeSeparateXYZ")
    l.new(vt.outputs["Vector"], sep_c.inputs["Vector"])
    sep_n = n.new("ShaderNodeSeparateXYZ")
    l.new(geo.outputs["Normal"], sep_n.inputs["Vector"])

    def math_op(op, a, b=None, b_val=None):
        m = n.new("ShaderNodeMath")
        m.operation = op
        l.new(a, m.inputs[0])
        if b is not None:
            l.new(b, m.inputs[1])
        elif b_val is not None:
            m.inputs[1].default_value = b_val
        return m

    def window_grid(u, v):
        fu = math_op("FRACT",
                     math_op("MULTIPLY", u, b_val=1.0 / 3.0).outputs[0])
        wu = math_op("LESS_THAN", fu.outputs[0], b_val=0.55)
        fv = math_op("FRACT",
                     math_op("MULTIPLY", v, b_val=1.0 / 3.6).outputs[0])
        wv = math_op("LESS_THAN", fv.outputs[0], b_val=0.62)
        return math_op("MULTIPLY", wu.outputs[0], wv.outputs[0]).outputs[0]

    def abs_s(sock):
        m = n.new("ShaderNodeMath")
        m.operation = "ABSOLUTE"
        l.new(sock, m.inputs[0])
        return m.outputs[0]

    # window grids: X-facing walls use (Y,Z), Y-facing walls use (X,Z)
    gx = window_grid(sep_c.outputs["Y"], sep_c.outputs["Z"])
    gy = window_grid(sep_c.outputs["X"], sep_c.outputs["Z"])
    mx = math_op("MULTIPLY", gx, abs_s(sep_n.outputs["X"])).outputs[0]
    my = math_op("MULTIPLY", gy, abs_s(sep_n.outputs["Y"])).outputs[0]
    mask = math_op("MINIMUM",
                   math_op("ADD", mx, my).outputs[0], b_val=1.0).outputs[0]

    # lit-window color variation: warm vs cool
    noise = n.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.35
    noise.inputs["Detail"].default_value = 2.0
    l.new(vt.outputs["Vector"], noise.inputs["Vector"])
    mr = n.new("ShaderNodeMapRange")
    mr.clamp = True
    l.new(noise.outputs["Fac"], mr.inputs["Value"])
    mr.inputs["From Min"].default_value = 0.35
    mr.inputs["From Max"].default_value = 0.65
    mr.inputs["To Min"].default_value = 0.0
    mr.inputs["To Max"].default_value = 1.0
    mixc = n.new("ShaderNodeMix")
    mixc.data_type = "RGBA"
    l.new(mr.outputs["Result"], mixc.inputs["Factor"])
    mixc.inputs["A"].default_value = (1.0, 0.55, 0.22, 1.0)
    mixc.inputs["B"].default_value = (0.35, 0.85, 1.0, 1.0)

    emission = n.new("ShaderNodeEmission")
    l.new(mixc.outputs["Result"], emission.inputs["Color"])
    emission.inputs["Strength"].default_value = 5.0

    wall = n.new("ShaderNodeBsdfPrincipled")
    wall.inputs["Base Color"].default_value = (0.030, 0.038, 0.065, 1.0)
    wall.inputs["Roughness"].default_value = 0.85
    wall.inputs["Metallic"].default_value = 0.15

    mixs = n.new("ShaderNodeMixShader")
    l.new(mask, mixs.inputs["Fac"])
    l.new(wall.outputs["BSDF"], mixs.inputs[1])
    l.new(emission.outputs["Emission"], mixs.inputs[2])
    l.new(mixs.outputs["Shader"], out.inputs["Surface"])
    return mat


def make_emission_material(name, color, strength):
    mat, nt = new_material(name)
    n = nt.nodes
    l = nt.links
    out = n.new("ShaderNodeOutputMaterial")
    em = n.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1.0)
    em.inputs["Strength"].default_value = strength
    l.new(em.outputs[0], out.inputs["Surface"])
    return mat


def make_dark_material(name, color, rough=0.9):
    mat, nt = new_material(name)
    n = nt.nodes
    l = nt.links
    out = n.new("ShaderNodeOutputMaterial")
    bsdf = n.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    l.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


# ---------------------------------------------------------------- build
clear_scene()

bmat = make_building_material()
ground_mat = make_dark_material("Ground", (0.008, 0.010, 0.018))
lane_mat = make_emission_material("Lane", (0.45, 0.85, 1.0), 2.5)
pole_mat = make_dark_material("Pole", (0.02, 0.02, 0.025), 0.6)
lamp_mat = make_emission_material("Lamp", (1.0, 0.62, 0.30), 8.0)
roof_mat = make_dark_material("RoofBox", (0.05, 0.05, 0.08), 0.7)

# ground
box("Ground", (0, 0, -0.5), (CITY + 120, CITY + 120, 1.0), ground_mat)

# streets: emissive lane strips along street centers
street_centers = [-HALF + i * PITCH for i in range(N_BLOCKS + 1)]
for c in street_centers:
    box(f"LaneX_{c:.0f}", (c, 0, 0.06), (0.5, CITY, 0.12), lane_mat)
    box(f"LaneY_{c:.0f}", (0, c, 0.06), (CITY, 0.5, 0.12), lane_mat)

# buildings
n_buildings = 0
for bi in range(N_BLOCKS):
    for bj in range(N_BLOCKS):
        x0 = -HALF + bi * PITCH + STREET / 2.0
        y0 = -HALF + bj * PITCH + STREET / 2.0
        # downtown falloff: taller near center
        cx = (x0 + BLOCK / 2.0) / HALF
        cy = (y0 + BLOCK / 2.0) / HALF
        center_factor = max(0.0, 1.0 - math.sqrt(cx * cx + cy * cy))
        for di in range(2):
            for dj in range(2):
                bx = x0 + BLOCK * (0.25 + 0.5 * di) + random.uniform(-2, 2)
                by = y0 + BLOCK * (0.25 + 0.5 * dj) + random.uniform(-2, 2)
                w = random.uniform(8, 12)
                d = random.uniform(8, 12)
                h = random.uniform(10, 26) + center_factor * random.uniform(15, 55)
                b = box(f"B_{bi}_{bj}_{di}_{dj}", (bx, by, h / 2.0), (w, d, h), bmat)
                n_buildings += 1
                # rooftop box (AC unit / antenna base) on some towers
                if h > 40 and random.random() < 0.6:
                    rw = random.uniform(2, 4)
                    box(f"Roof_{n_buildings}", (bx, by, h + 1.2), (rw, rw, 2.4), roof_mat)
                # red beacon on the tallest
                if h > 60:
                    bpy.ops.mesh.primitive_uv_sphere_add(
                        radius=0.8, location=(bx, by, h + 2.6))
                    s = bpy.context.active_object
                    s.name = f"Beacon_{n_buildings}"
                    s.data.materials.append(
                        make_emission_material("Beacon", (1.0, 0.1, 0.1), 10.0))

print(f"buildings: {n_buildings}")

# street lamps along every street, both sides
for c in street_centers:
    for t in range(-int(HALF), int(HALF) + 1, 28):
        for side, along_x in ((1, True), (-1, True), (1, False), (-1, False)):
            if along_x:
                px, py = t, c + side * (STREET / 2.0 - 1.5)
            else:
                px, py = c + side * (STREET / 2.0 - 1.5), t
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.22, depth=9.0, location=(px, py, 4.5))
            pole = bpy.context.active_object
            pole.name = "Pole"
            pole.data.materials.append(pole_mat)
            bpy.ops.mesh.primitive_uv_sphere_add(
                radius=0.65, location=(px, py, 9.2))
            head = bpy.context.active_object
            head.name = "LampHead"
            head.data.materials.append(lamp_mat)

# ---------------------------------------------------------------- light & camera
world = bpy.context.scene.world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.006, 0.009, 0.025, 1.0)
bg.inputs["Strength"].default_value = 1.0

bpy.ops.object.light_add(type="SUN", location=(40, -60, 80))
sun = bpy.context.active_object
sun.data.energy = 1.5
sun.data.color = (0.65, 0.78, 1.0)
sun.rotation_euler = (math.radians(55), 0, math.radians(35))

bpy.ops.object.light_add(type="AREA", location=(0, 0, 120))
area = bpy.context.active_object
area.data.energy = 3000
area.data.size = 140
area.data.color = (0.55, 0.65, 1.0)

cam_data = bpy.data.cameras.new("CityCam")
cam = bpy.data.objects.new("CityCam", cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location = (105, -125, 78)
target = Vector((0, 0, 12))
direction = target - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
cam_data.lens = 32
bpy.context.scene.camera = cam

# ---------------------------------------------------------------- render setup
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.render.resolution_x = RES_X
scene.render.resolution_y = RES_Y
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
render_path = os.path.join(RENDER_DIR, "city_preview.png")
scene.render.filepath = render_path

# subtle bloom via compositor glare
try:
    scene.use_nodes = True
    ct = scene.node_tree
    ct.nodes.clear()
    rl = ct.nodes.new("CompositorNodeRLayers")
    glare = ct.nodes.new("CompositorNodeGlare")
    glare.glare_type = "FOG_GLOW"
    glare.threshold = 0.9
    glare.size = 8
    comp = ct.nodes.new("CompositorNodeComposite")
    ct.links.new(rl.outputs["Image"], glare.inputs["Image"])
    ct.links.new(glare.outputs["Image"], comp.inputs["Image"])
except Exception as e:
    print("compositor setup skipped:", e)

bpy.ops.wm.save_as_mainfile(
    filepath=os.path.join(OUT_DIR, "voxel_city.blend"))

bpy.ops.render.render(write_still=True)
print("rendered:", render_path)
