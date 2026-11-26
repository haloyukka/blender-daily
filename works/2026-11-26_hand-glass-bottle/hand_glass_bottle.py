"""
Blender制作日記 2026-11-26 : 手のかたちのガラス瓶 (Hand Glass Bottle)

使い方:
  Blender の Scripting タブでこのファイルを開いて「Run Script」。
  またはコマンドラインから:
    blender --background --python hand_glass_bottle.py -- --render out.png

内容:
  - メタボールで手（手のひら・5本の指・手首）とボトルの首を一体成形
  - Solidify でガラスの肉厚をつけ、透過マテリアルを適用
  - 内側に一回り小さい手を作り、水位でカットして液体に
  - コルク栓・飲み口のリップ・床・ライト・カメラ・Cycles設定まで自動構築
  Blender 4.x / 5.x 対応
"""

import bpy
import math
import sys
from mathutils import Vector, Quaternion

# ---------------------------------------------------------------
# パラメータ（毎日いじる用）
# ---------------------------------------------------------------
GLASS_COLOR = (0.82, 0.95, 1.0, 1.0)    # ガラスのわずかな色味
LIQUID_COLOR = (0.15, 0.75, 0.70, 1.0)  # 中身の液体（ミクっぽいティール）
FILL_LEVEL = 0.30                        # 液体の水位（Z）
GLASS_THICKNESS = 0.035                  # ガラスの肉厚
FINGER_SPREAD = 1.0                      # 指の開き具合（倍率）
RESOLUTION = (1080, 1350)                # SNS向け 4:5
SAMPLES = 128
TURNTABLE = False                        # True で120フレームの回転アニメ


# ---------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.metaballs, bpy.data.materials,
                 bpy.data.curves, bpy.data.lights, bpy.data.cameras):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def rot_x_to(direction: Vector) -> Quaternion:
    """ローカルX軸を direction に向ける回転"""
    return Vector((1, 0, 0)).rotation_difference(direction.normalized())


def add_capsule(mb, p0, p1, radius, stiffness=2.0):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    el = mb.elements.new()
    el.type = "CAPSULE"
    el.co = (p0 + p1) / 2
    el.radius = radius
    el.size_x = d.length / 2
    el.rotation = rot_x_to(d)
    el.stiffness = stiffness
    return el


def add_ellipsoid(mb, co, size, radius=1.0, stiffness=2.0):
    el = mb.elements.new()
    el.type = "ELLIPSOID"
    el.co = co
    el.radius = radius
    el.size_x, el.size_y, el.size_z = size
    el.stiffness = stiffness
    return el


def set_input(node, names, value):
    """Blenderのバージョン差でソケット名が違うものに対応"""
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return


# ---------------------------------------------------------------
# 手の骨格（ポイント列）を定義
#   座標系: 手のひらを下にして床に置いた右手。指先が +Y、手首が -Y
#   半径はすべて「見た目の半径」。メタボールの影響半径へは META_K で換算
# ---------------------------------------------------------------
META_K = 0.575  # threshold 0.6 / stiffness 2 のとき 見た目半径 ≒ 影響半径 × 0.575
PALM_Z = 0.30


def hand_chains():
    chains = []  # (points, radii)

    # 4本の指: (付け根X, 付け根Y, 長さ, 開き角度deg, 太さ)
    fingers = [
        (-0.31, 0.46, 0.98, 11, 0.085),   # 人差し指
        (-0.10, 0.52, 1.08, 3, 0.088),    # 中指
        (0.11, 0.48, 1.00, -6, 0.084),    # 薬指
        (0.30, 0.38, 0.78, -16, 0.074),   # 小指
    ]
    seg_ratio = (0.45, 0.32, 0.23)
    for bx, by, length, ang, r in fingers:
        yaw = math.radians(ang * FINGER_SPREAD)
        pitch = math.radians(-3)
        p = Vector((bx, by, PALM_Z + 0.02))
        pts, rad = [p.copy()], [r * 1.05]
        for i, ratio in enumerate(seg_ratio):
            pitch -= math.radians(6)  # 指先に向かって床へ自然にカール
            d = Vector((-math.sin(yaw) * math.cos(pitch),
                        math.cos(yaw) * math.cos(pitch),
                        math.sin(pitch)))
            p = p + d * length * ratio
            pts.append(p.copy())
            rad.append(r * (0.97 - 0.07 * i))
        chains.append((pts, rad))

    # 親指（手のひらの左下から外へ）
    t_pts = [Vector((-0.30, -0.22, PALM_Z)),
             Vector((-0.55, 0.00, PALM_Z - 0.03)),
             Vector((-0.75, 0.26, PALM_Z - 0.08)),
             Vector((-0.86, 0.48, PALM_Z - 0.13))]
    chains.append((t_pts, [0.13, 0.105, 0.095, 0.085]))

    # 手首 → ボトルの首（上にカーブして立ち上がる）
    n_pts = [Vector((0.0, -0.40, PALM_Z)),
             Vector((0.0, -0.90, PALM_Z + 0.03)),
             Vector((0.0, -1.22, PALM_Z + 0.18)),
             Vector((0.0, -1.40, PALM_Z + 0.50)),
             Vector((0.0, -1.47, PALM_Z + 0.88)),
             Vector((0.0, -1.48, PALM_Z + 1.10))]
    chains.append((n_pts, [0.24, 0.22, 0.16, 0.115, 0.10, 0.10]))
    return chains


NECK_TOP = Vector((0.0, -1.48, PALM_Z + 1.10))


def build_hand_meta(name):
    mb = bpy.data.metaballs.new(name)
    mb.resolution = 0.02
    mb.render_resolution = 0.02
    mb.threshold = 0.6
    obj = bpy.data.objects.new(name, mb)
    bpy.context.collection.objects.link(obj)

    # 手のひら（平たい楕円体を2つ重ねて四角っぽく）。sizeも見た目寸法で指定
    for co, half in (((0.0, 0.08, PALM_Z), (0.36, 0.40, 0.10)),
                     ((0.0, -0.22, PALM_Z), (0.28, 0.22, 0.11))):
        add_ellipsoid(mb, co, tuple(h / META_K for h in half), radius=1.0)

    for pts, rad in hand_chains():
        for a, b, ra, rb in zip(pts[:-1], pts[1:], rad[:-1], rad[1:]):
            add_capsule(mb, a, b, (ra + rb) / 2 / META_K)
    return obj


def meta_to_mesh(obj, new_name):
    dg = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(dg)
    mesh = bpy.data.meshes.new_from_object(eval_obj)
    mesh.name = new_name
    mesh_obj = bpy.data.objects.new(new_name, mesh)
    bpy.context.collection.objects.link(mesh_obj)
    meta_data = obj.data
    bpy.data.objects.remove(obj)
    bpy.data.metaballs.remove(meta_data)
    for p in mesh.polygons:
        p.use_smooth = True
    return mesh_obj


# ---------------------------------------------------------------
# マテリアル
# ---------------------------------------------------------------
def principled(name, color, rough, transmission=0.0, ior=1.45):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    set_input(bsdf, ["Base Color"], color)
    set_input(bsdf, ["Roughness"], rough)
    set_input(bsdf, ["Transmission Weight", "Transmission"], transmission)
    set_input(bsdf, ["IOR"], ior)
    return mat


def glass_material():
    return principled("Glass", GLASS_COLOR, 0.02, transmission=1.0, ior=1.5)


def liquid_material():
    mat = principled("Liquid", LIQUID_COLOR, 0.0, transmission=1.0, ior=1.33)
    nt = mat.node_tree
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.inputs["Color"].default_value = LIQUID_COLOR
    vol.inputs["Density"].default_value = 2.5
    out = nt.nodes.get("Material Output")
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def cork_material():
    mat = bpy.data.materials.new("Cork")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 60.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.30, 0.18, 0.09, 1)
    ramp.color_ramp.elements[1].color = (0.62, 0.43, 0.25, 1)
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    set_input(bsdf, ["Roughness"], 0.85)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.3
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


# ---------------------------------------------------------------
# パーツ生成
# ---------------------------------------------------------------
def build_glass():
    meta = build_hand_meta("HandGlassMeta")
    glass = meta_to_mesh(meta, "HandGlassBottle")
    # 関節のつなぎ目のコブをならす
    sm = glass.modifiers.new("Smooth", "SMOOTH")
    sm.factor = 0.6
    sm.iterations = 6
    sol = glass.modifiers.new("Thickness", "SOLIDIFY")
    sol.thickness = GLASS_THICKNESS
    sol.offset = -1.0
    sol.use_even_offset = True
    glass.data.materials.append(glass_material())
    return glass


def build_liquid(glass):
    # ガラスと同じ形を、肉厚＋すき間ぶん内側へオフセットして液体にする
    liquid = bpy.data.objects.new("Liquid", glass.data.copy())
    bpy.context.collection.objects.link(liquid)
    liquid.data.materials.clear()
    sm = liquid.modifiers.new("Smooth", "SMOOTH")
    sm.factor = 0.6
    sm.iterations = 6
    disp = liquid.modifiers.new("Inset", "DISPLACE")
    disp.mid_level = 0.0
    disp.strength = -(GLASS_THICKNESS + 0.008)

    # モディファイアを適用したメッシュを水位で二分し、上側を捨てて断面をふさぐ
    import bmesh
    dg = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(liquid.evaluated_get(dg))
    liquid.modifiers.clear()
    old = liquid.data
    liquid.data = baked
    bpy.data.meshes.remove(old)
    baked.name = "Liquid"

    bm = bmesh.new()
    bm.from_mesh(baked)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-5,
                                 plane_co=(0, 0, FILL_LEVEL),
                                 plane_no=(0, 0, 1), clear_outer=True)
    cut_edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
    filled = bmesh.ops.holes_fill(bm, edges=cut_edges, sides=0)
    for f in filled["faces"]:
        f.smooth = False  # 液面はフラットに
    bm.to_mesh(baked)
    bm.free()
    liquid.data.materials.append(liquid_material())
    return liquid


def build_lip_and_cork():
    top = NECK_TOP.copy()
    # 飲み口のリップ
    bpy.ops.mesh.primitive_torus_add(major_radius=0.095, minor_radius=0.028,
                                     major_segments=64, minor_segments=24,
                                     location=top + Vector((0, 0, 0.01)))
    lip = bpy.context.active_object
    lip.name = "Lip"
    bpy.ops.object.shade_smooth()
    lip.data.materials.append(bpy.data.materials["Glass"])

    # コルク（上が少し太いテーパー）
    bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=0.075, radius2=0.095,
                                    depth=0.26, location=top + Vector((0, 0, 0.08)))
    cork = bpy.context.active_object
    cork.name = "Cork"
    bev = cork.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.012
    bev.segments = 3
    bpy.ops.object.shade_smooth()
    cork.data.materials.append(cork_material())


def build_stage():
    # 床から奥へ立ち上がるホリゾント
    bpy.ops.mesh.primitive_plane_add(size=1)
    floor = bpy.context.active_object
    floor.name = "Backdrop"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.delete(type="VERT")
    bpy.ops.object.mode_set(mode="OBJECT")
    import bmesh
    bm = bmesh.new()
    prof = []
    for i in range(25):
        t = i / 24
        if t < 0.5:
            y, z = -6 + t * 2 * 8, 0.0
        else:
            a = (t - 0.5) * 2 * (math.pi / 2)
            y, z = 2 + math.sin(a) * 3, 3 - math.cos(a) * 3
        prof.append((y, z))
    prof.append((5, 8))
    rows = []
    for x in (-8, 8):
        rows.append([bm.verts.new((x, y, z)) for y, z in prof])
    for i in range(len(prof) - 1):
        bm.faces.new((rows[0][i], rows[1][i], rows[1][i + 1], rows[0][i + 1]))
    bm.to_mesh(floor.data)
    bm.free()
    for p in floor.data.polygons:
        p.use_smooth = True
    floor.data.materials.append(
        principled("Backdrop", (0.92, 0.90, 0.88, 1), 0.6))


def build_lights_camera():
    def area(name, loc, energy, size, color=(1, 1, 1)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy = energy
        ld.size = size
        ld.color = color
        lo = bpy.data.objects.new(name, ld)
        bpy.context.collection.objects.link(lo)
        lo.location = loc
        lo.visible_camera = False  # ライト自体は写さない
        d = Vector((0, -0.4, 0.4)) - Vector(loc)
        lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        return lo

    area("Key", (-3.5, -3.0, 4.5), 900, 3.0, (1.0, 0.96, 0.9))
    area("Rim", (3.0, 3.5, 3.0), 700, 2.0, (0.75, 0.9, 1.0))
    area("Fill", (4.0, -4.0, 1.5), 200, 4.0)

    # カメラ: 斜め上から3/4ビュー
    target = bpy.data.objects.new("CamTarget", None)
    bpy.context.collection.objects.link(target)
    target.location = (-0.05, -0.20, 0.45)

    cd = bpy.data.cameras.new("Camera")
    cd.lens = 55
    cd.dof.use_dof = True
    cd.dof.focus_object = target
    cd.dof.aperture_fstop = 4.0
    cam = bpy.data.objects.new("Camera", cd)
    bpy.context.collection.objects.link(cam)
    cam.location = (3.3, -5.0, 3.0)
    tc = cam.constraints.new("TRACK_TO")
    tc.target = target
    tc.track_axis = "TRACK_NEGATIVE_Z"
    tc.up_axis = "UP_Y"
    bpy.context.scene.camera = cam

    if TURNTABLE:
        pivot = bpy.data.objects.new("Pivot", None)
        bpy.context.collection.objects.link(pivot)
        pivot.location = target.location
        cam.parent = pivot
        cam.location = Vector(cam.location) - Vector(pivot.location)
        sc = bpy.context.scene
        sc.frame_start, sc.frame_end = 1, 120
        pivot.rotation_euler = (0, 0, 0)
        pivot.keyframe_insert("rotation_euler", frame=1)
        pivot.rotation_euler = (0, 0, math.tau)
        pivot.keyframe_insert("rotation_euler", frame=121)
        for fc in getattr(pivot.animation_data.action, "fcurves", []):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"


def setup_render():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 16
    sc.cycles.transmission_bounces = 16
    sc.cycles.transparent_max_bounces = 16
    sc.cycles.caustics_refractive = True
    sc.render.resolution_x, sc.render.resolution_y = RESOLUTION
    sc.render.resolution_percentage = 100
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass

    world = sc.world or bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.85, 0.88, 0.92, 1)
    bg.inputs["Strength"].default_value = 0.35


# ---------------------------------------------------------------
def main():
    clear_scene()
    glass = build_glass()
    build_liquid(glass)
    build_lip_and_cork()
    build_stage()
    build_lights_camera()
    setup_render()

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--render" in argv:
        out = argv[argv.index("--render") + 1]
        bpy.context.scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print("Rendered:", out)


if __name__ == "__main__":
    main()
