"""
Blender制作日記 2026-11-27 : 光るルビーの原石 (Glowing Ruby Crystal)

使い方:
  Blender の Scripting タブでこのファイルを開いて「Run Script」。
  またはコマンドラインから:
    blender --background --python glowing_ruby.py -- --render out.png

内容:
  - 六角柱＋錐面の結晶を凸包で作り、ランダムな平面カットで欠けた面を足す
  - ルビー色のガラス（IOR 1.76）＋ Volume Absorption で奥ほど濃い赤
  - 内部に枝分かれする発光のひび（稲妻状のカーブ）を自動生成
  - 根元を白い母岩（リメッシュ＋ディスプレイス）で包む。母岩の上端は斜めにカット
  - グレーのホリゾント、柔らかいキーライト、ブルーム
  Blender 4.x / 5.x 対応
"""

import bpy
import bmesh
import math
import random
import sys
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

# ---------------------------------------------------------------
# パラメータ（毎日いじる用）
# ---------------------------------------------------------------
RUBY_COLOR = (0.95, 0.0, 0.62, 1.0)    # 宝石の色（ルビーピンク）
ABSORB_DENSITY = 2.6                   # 奥の色の濃さ（Volume Absorption）
VEIN_COLOR = (1.0, 0.62, 0.25, 1.0)    # ひびの発光色（金オレンジ）
VEIN_GLOW = 40.0                       # ひびの発光の強さ
VEIN_BRANCHES = 9                      # 枝分かれの数
ROCK_COLOR = (0.82, 0.74, 0.72, 1.0)   # 母岩の色
ROCK_HEIGHT = 0.72                     # 母岩の高さ（低い側）
ROCK_TILT = 38.0                       # 母岩の上端の傾き（度）
CRYSTAL_HEIGHT = 3.0                   # 結晶の高さ
CRYSTAL_RADIUS = 0.56                  # 結晶の太さ
SEED = 7                               # 形・ひびの乱数シード
BLOOM = True                           # コンポジットでブルーム
RESOLUTION = (1080, 1350)              # SNS向け 4:5
SAMPLES = 128
TURNTABLE = False                      # True で120フレームの回転アニメ


# ---------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------
def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
                 bpy.data.lights, bpy.data.cameras, bpy.data.textures):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def set_input(node, names, value):
    """Blenderのバージョン差でソケット名が違うものに対応"""
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return node.inputs[n]
    return None


def get_input(node, names):
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def link_obj(ob):
    bpy.context.collection.objects.link(ob)
    return ob


def mesh_from_bm(name, bm, mat, smooth=False):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    me.materials.append(mat)
    return link_obj(bpy.data.objects.new(name, me))


# ---------------------------------------------------------------
# マテリアル
# ---------------------------------------------------------------
def ruby_material():
    m = bpy.data.materials.new("Ruby")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    tint = tuple(0.35 + 0.65 * c for c in RUBY_COLOR[:3]) + (1.0,)
    bsdf.inputs["Base Color"].default_value = tint
    set_input(bsdf, ["Transmission Weight", "Transmission"], 1.0)
    bsdf.inputs["IOR"].default_value = 1.76
    # 面ごとに少しずつ曇らせる（内部の霜のような質感）
    tc = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.5
    set_input(noise, ["Detail"], 8.0)
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = 0.35
    ramp.inputs["From Max"].default_value = 0.75
    ramp.inputs["To Min"].default_value = 0.02
    ramp.inputs["To Max"].default_value = 0.16
    nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], bsdf.inputs["Roughness"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])

    # 中身の色は Volume Absorption ＋ ごく薄い散乱
    absorb = nt.nodes.new("ShaderNodeVolumeAbsorption")
    inv = tuple(max(0.02, c) for c in RUBY_COLOR[:3]) + (1.0,)
    absorb.inputs["Color"].default_value = inv
    absorb.inputs["Density"].default_value = ABSORB_DENSITY
    scat = nt.nodes.new("ShaderNodeVolumeScatter")
    scat.inputs["Color"].default_value = RUBY_COLOR
    scat.inputs["Density"].default_value = 0.35
    add = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(absorb.outputs[0], add.inputs[0])
    nt.links.new(scat.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Volume"])
    return m


def vein_material():
    m = bpy.data.materials.new("Vein")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = VEIN_COLOR
    em.inputs["Strength"].default_value = VEIN_GLOW
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m


def rock_material():
    m = bpy.data.materials.new("Rock")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.85
    set_input(bsdf, ["Subsurface Weight", "Subsurface"], 0.35)
    set_input(bsdf, ["Subsurface Radius"], (0.15, 0.06, 0.05))
    set_input(bsdf, ["Subsurface Scale"], 0.08)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    # 色ムラ：白〜ピンクがかった灰色
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 4.0
    set_input(noise, ["Detail"], 10.0)
    cr = nt.nodes.new("ShaderNodeValToRGB")
    cr.color_ramp.elements[0].position = 0.35
    cr.color_ramp.elements[0].color = tuple(c * 0.75 for c in ROCK_COLOR[:3]) + (1,)
    cr.color_ramp.elements[1].position = 0.7
    cr.color_ramp.elements[1].color = (0.93, 0.9, 0.88, 1)
    nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], cr.inputs["Fac"])
    # 上端（結晶に近い側）ほど赤みを足す
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = ROCK_HEIGHT + 0.3
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs[7].default_value = (1.0, 0.72, 0.68, 1)
    nt.links.new(mr.outputs["Result"], mix.inputs[0])
    nt.links.new(cr.outputs["Color"], mix.inputs[6])
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    # 細かい凹凸
    bn = nt.nodes.new("ShaderNodeTexNoise")
    bn.inputs["Scale"].default_value = 60.0
    set_input(bn, ["Detail"], 6.0)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    nt.links.new(tc.outputs["Object"], bn.inputs["Vector"])
    nt.links.new(bn.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return m


def backdrop_material():
    m = bpy.data.materials.new("Backdrop")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.09, 0.09, 0.095, 1)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 18.0
    set_input(noise, ["Detail"], 6.0)
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["To Min"].default_value = 0.45
    mr.inputs["To Max"].default_value = 0.8
    nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], mr.inputs["Value"])
    nt.links.new(mr.outputs["Result"], bsdf.inputs["Roughness"])
    return m


# ---------------------------------------------------------------
# 結晶
# ---------------------------------------------------------------
def build_crystal(rng, mat):
    """六角柱＋錐面の点群を凸包にして、平面カットで欠けを足す"""
    R, Hc = CRYSTAL_RADIUS, CRYSTAL_HEIGHT
    pts = []
    base_rot = rng.uniform(0, math.tau)
    radii = [R * rng.uniform(0.82, 1.12) for _ in range(6)]
    # 下から肩まで：中ほどが一番太い
    for z, s in ((-0.2, 0.62), (0.8, 0.98), (1.5, 1.04), (2.0, 0.96)):
        for i in range(6):
            a = base_rot + i * math.tau / 6 + rng.uniform(-0.08, 0.08)
            r = radii[i] * s
            pts.append(Vector((math.cos(a) * r, math.sin(a) * r * 0.85,
                               z + rng.uniform(-0.08, 0.08))))
    # 先端：少し右奥にずらし、片側に大きな錐面ができるようにする
    apex = Vector((R * 0.18, R * 0.05, Hc))
    pts.append(apex)
    pts.append(apex + Vector((-R * 0.22, -R * 0.1, -0.12)))

    bm = bmesh.new()
    verts = [bm.verts.new(p) for p in pts]
    bmesh.ops.convex_hull(bm, input=verts)
    # 凸包の内側に残った頂点を消す
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")

    # ランダムな平面カット（欠けた面）。凸のまま面が増える
    for _ in range(7):
        d = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.2, 0.9))).normalized()
        c = Vector((0, 0, rng.uniform(0.4, Hc * 0.85)))
        # 中心からの距離：表面から少しだけ内側
        reach = max((v.co - c).dot(d) for v in bm.verts)
        co = c + d * reach * rng.uniform(0.86, 0.95)
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        res = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=d,
                                     clear_outer=True)
        edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
        if edges:
            bmesh.ops.holes_fill(bm, edges=edges, sides=0)
    bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(1.0),
                             verts=bm.verts[:], edges=bm.edges[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    # 角にわずかなベベル（ハイライト用）
    bmesh.ops.bevel(bm, geom=bm.edges[:], offset=0.008, segments=2,
                    affect="EDGES", profile=0.5)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
    ob = mesh_from_bm("Crystal", bm, mat)
    return ob


def bvh_of(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    tree = BVHTree.FromBMesh(bm)
    bm.free()
    return tree


def inside(tree, p, margin):
    """BVHの最近点の法線で内外判定し、表面から margin 以上離れているか"""
    hit = tree.find_nearest(p)
    if hit[0] is None:
        return False
    loc, nor, _, dist = hit
    return (p - loc).dot(nor) < 0 and dist > margin


# ---------------------------------------------------------------
# 発光のひび（稲妻状の枝分かれ）
# ---------------------------------------------------------------
def walk(rng, tree, start, direction, length, step, jitter):
    pts = [start.copy()]
    p = start.copy()
    d = direction.normalized()
    n = int(length / step)
    for _ in range(n):
        for _try in range(8):
            nd = (d + Vector((rng.gauss(0, jitter), rng.gauss(0, jitter * 0.6),
                              rng.gauss(0, jitter)))).normalized()
            q = p + nd * step
            if inside(tree, q, 0.04):
                break
        else:
            break
        d = (d * 0.6 + nd * 0.4).normalized()
        p = q
        pts.append(p.copy())
    return pts


def add_curve(name, pts, r0, r1, mat):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 1.0
    cu.bevel_resolution = 2
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        t = i / max(1, len(pts) - 1)
        sp.points[i].co = (p.x, p.y, p.z, 1.0)
        sp.points[i].radius = r0 + (r1 - r0) * t
    cu.materials.append(mat)
    return link_obj(bpy.data.objects.new(name, cu))


def build_veins(rng, tree, mat):
    # 幹：根元の手前から斜め上へ（画像の稲妻のように1本太く）
    start = Vector((0.05, -0.2, 0.25))
    trunk = walk(rng, tree, start, Vector((-0.3, -0.05, 1.0)), 2.2, 0.07, 0.45)
    add_curve("VeinTrunk", trunk, 0.013, 0.003, mat)
    # 根元の光の芯（小さな発光球）
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.018)
    core = mesh_from_bm("VeinCore", bm, mat, smooth=True)
    core.location = start
    branches = [trunk]
    for k in range(VEIN_BRANCHES):
        src = rng.choice(branches)
        if len(src) < 4:
            continue
        i = rng.randrange(1, len(src) - 2)
        dirv = Vector((rng.uniform(-1, 1), rng.uniform(-0.6, 0.4), rng.uniform(-0.2, 1.0)))
        b = walk(rng, tree, src[i], dirv, rng.uniform(0.3, 0.9), 0.06, 0.5)
        if len(b) > 2:
            add_curve(f"Vein{k:02d}", b, 0.005, 0.0015, mat)
            branches.append(b)


# ---------------------------------------------------------------
# 母岩
# ---------------------------------------------------------------
def build_rock(rng, mat):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=1.0)
    bmesh.ops.scale(bm, vec=(0.78, 0.62, 1.1), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.06, 0.02, 0.5), verts=bm.verts)
    # 下は床で、上端は斜めにカット（右が高い）
    for co, no in (((0, 0, -0.02), (0, 0, -1)),
                   ((0, 0, ROCK_HEIGHT),
                    (-math.sin(math.radians(ROCK_TILT)), 0.12,
                     math.cos(math.radians(ROCK_TILT))))):
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        res = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co,
                                     plane_no=Vector(no).normalized(), clear_outer=True)
        edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
        bmesh.ops.holes_fill(bm, edges=edges, sides=0)
    ob = mesh_from_bm("Rock", bm, mat, smooth=True)

    rem = ob.modifiers.new("Remesh", "REMESH")
    rem.mode = "VOXEL"
    rem.voxel_size = 0.018
    for name, kind, size, strength, depth in (
            ("Big", "CLOUDS", 0.45, 0.16, 3),
            ("Fine", "CLOUDS", 0.09, 0.035, 2)):
        tex = bpy.data.textures.new("Rock" + name, kind)
        tex.noise_scale = size
        tex.noise_depth = depth
        dm = ob.modifiers.new("Disp" + name, "DISPLACE")
        dm.texture = tex
        dm.texture_coords = "LOCAL"
        dm.strength = strength
        dm.mid_level = 0.5
    sm = ob.modifiers.new("Smooth", "SMOOTH")
    sm.iterations = 2
    sm.factor = 0.4
    return ob


# ---------------------------------------------------------------
# ステージ・ライト・カメラ・レンダー
# ---------------------------------------------------------------
def build_stage():
    bm = bmesh.new()
    prof = []
    for i in range(25):
        t = i / 24
        if t < 0.5:
            y, z = -8 + t * 2 * 11, 0.0
        else:
            a = (t - 0.5) * 2 * (math.pi / 2)
            y, z = 3 + math.sin(a) * 3, 3 - math.cos(a) * 3
        prof.append((y, z))
    prof.append((6, 10))
    rows = [[bm.verts.new((x, y, z)) for y, z in prof] for x in (-10, 10)]
    for i in range(len(prof) - 1):
        bm.faces.new((rows[0][i], rows[1][i], rows[1][i + 1], rows[0][i + 1]))
    return mesh_from_bm("Backdrop", bm, backdrop_material(), smooth=True)


def build_lights_camera(target_loc):
    def area(name, loc, energy, size, color, aim=target_loc):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy = energy
        ld.size = size
        ld.color = color
        lo = link_obj(bpy.data.objects.new(name, ld))
        lo.location = loc
        lo.visible_camera = False  # ライト自体は写さない
        d = Vector(aim) - Vector(loc)
        lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        return lo

    area("Key", (-3.0, -2.5, 5.5), 220, 3.0, (1.0, 0.97, 0.95))
    area("Top", (0.5, 0.5, 6.0), 90, 2.0, (1.0, 1.0, 1.0))
    area("RimBack", (2.0, 4.0, 3.5), 350, 1.5, (1.0, 0.75, 0.85))
    area("Fill", (4.0, -3.5, 1.5), 40, 4.0, (0.9, 0.9, 1.0))
    # ひびの出口付近の母岩を暖色で照らす（結晶の外に置く）
    pd = bpy.data.lights.new("CrackGlow", "POINT")
    pd.energy = 4
    pd.color = VEIN_COLOR[:3]
    pd.shadow_soft_size = 0.1
    pl = link_obj(bpy.data.objects.new("CrackGlow", pd))
    pl.location = (0.35, -0.75, 0.95)
    for attr in ("visible_camera", "visible_glossy", "visible_transmission"):
        setattr(pl, attr, False)

    target = link_obj(bpy.data.objects.new("CamTarget", None))
    target.location = target_loc
    cd = bpy.data.cameras.new("Camera")
    cd.lens = 85
    cd.dof.use_dof = True
    cd.dof.focus_object = target
    cd.dof.aperture_fstop = 5.6
    cam = link_obj(bpy.data.objects.new("Camera", cd))
    cam.location = (0.5, -11.0, 2.4)
    tcn = cam.constraints.new("TRACK_TO")
    tcn.target = target
    tcn.track_axis = "TRACK_NEGATIVE_Z"
    tcn.up_axis = "UP_Y"
    bpy.context.scene.camera = cam

    if TURNTABLE:
        pivot = link_obj(bpy.data.objects.new("Pivot", None))
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


def setup_bloom(sc):
    """コンポジットでブルーム（5.x はノードグループ方式、4.x は node_tree）"""
    if hasattr(sc, "compositing_node_group"):
        ng = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        out = ng.nodes.new("NodeGroupOutput")
        sc.compositing_node_group = ng
    else:
        sc.use_nodes = True
        ng = sc.node_tree
        for nd in list(ng.nodes):
            ng.nodes.remove(nd)
        out = ng.nodes.new("CompositorNodeComposite")
    rl = ng.nodes.new("CompositorNodeRLayers")
    gl = ng.nodes.new("CompositorNodeGlare")
    if "Type" in gl.inputs:
        gl.inputs["Type"].default_value = "Bloom"
        set_input(gl, ["Threshold"], 1.0)
        set_input(gl, ["Strength"], 0.7)
        set_input(gl, ["Size"], 0.5)
    else:
        gl.glare_type = "BLOOM" if "BLOOM" in [i.identifier for i in
                                               gl.bl_rna.properties["glare_type"].enum_items] else "FOG_GLOW"
        gl.threshold = 1.0
        gl.size = 7
        try:
            gl.mix = -0.3
        except AttributeError:
            pass
    ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
    ng.links.new(gl.outputs["Image"], out.inputs[0])


def setup_render():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 16
    sc.cycles.transmission_bounces = 16
    sc.cycles.volume_bounces = 2
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
    bg.inputs["Color"].default_value = (0.05, 0.05, 0.055, 1)
    bg.inputs["Strength"].default_value = 0.25
    if BLOOM:
        setup_bloom(sc)


# ---------------------------------------------------------------
def main():
    clear_scene()
    rng = random.Random(SEED)
    crystal = build_crystal(rng, ruby_material())
    tree = bvh_of(crystal)
    build_veins(rng, tree, vein_material())
    build_rock(rng, rock_material())
    build_stage()
    build_lights_camera((0.0, 0.0, 1.45))
    setup_render()

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--render" in argv:
        out = argv[argv.index("--render") + 1]
        bpy.context.scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print("Rendered:", out)


if __name__ == "__main__":
    main()
