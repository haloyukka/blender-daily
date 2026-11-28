"""
Blender制作日記 2026-11-28 : サイバティックなりぼん (Cybertic Ribbon)

使い方:
  Blender の Scripting タブでこのファイルを開いて「Run Script」。
  またはコマンドラインから:
    blender --background --python cyber_ribbon.py -- --render out.png

内容:
  - 女の子のイラストの髪飾りにも使える、パステルピンクのりぼん
  - ループ・たれは「中心線に沿って帯を掃引」して作る（幅・ふくらみ・ねじれをパラメータで制御）
  - ふちにネオンのパイピング、帯の中央に点線のデータライン、薄膜干渉でほんのり虹色
  - 結び目はパールホワイトのカプセル型モジュール。前面の黒いガラスにハートのLED
  - まわりに小さなキラキラ、ラベンダーのホリゾントとネオングリッド
  Blender 4.x / 5.x 対応
"""

import bpy
import bmesh
import math
import random
import sys
from mathutils import Vector, Matrix

# ---------------------------------------------------------------
# パラメータ（毎日いじる用）
# ---------------------------------------------------------------
RIBBON_COLOR = (1.0, 0.30, 0.62, 1.0)   # りぼんの地色（パステルピンク）
EDGE_COLOR = (0.15, 0.85, 1.0, 1.0)     # ふちのパイピングの発光（シアン）
LINE_COLOR = (0.35, 0.95, 1.0, 1.0)     # 帯の中央の点線の発光
HEART_COLOR = (1.0, 0.04, 0.42, 1.0)    # 結び目のハートLED（マゼンタピンク）
SPARKLE_COLOR = (1.0, 0.85, 1.0, 1.0)   # キラキラの発光
GRID_COLOR = (0.85, 0.35, 1.0, 1.0)     # 床グリッドの発光
GLOW = 1.0                              # 発光の強さ（全体倍率）
IRIDESCENCE = 260.0                     # 薄膜干渉の膜厚(nm)。0で虹色なし
LOOP_LENGTH = 1.25                      # ループの長さ
LOOP_WIDTH = 0.95                       # ループ外側の帯の幅
LOOP_TILT = 14.0                        # ループの跳ね上がり（度）
LOOP_PUFF = 0.14                        # ループのふくらみ
TAIL_LENGTH = 1.55                      # たれの長さ
TAIL_SPREAD = 0.55                      # たれの開き
SPARKLE_COUNT = 7                       # キラキラの数
SEED = 28                               # キラキラ配置の乱数シード
BLOOM = True                            # コンポジットでブルームをかける
RESOLUTION = (1080, 1350)               # SNS向け 4:5
SAMPLES = 128
TURNTABLE = False                       # True で120フレームの回転アニメ

THICK = 0.018            # 帯の厚み
PIPE_R = 0.013           # パイピングの太さ
BOW_Z = 1.75             # りぼん中心の高さ
KNOT_SCALE = 1.35        # 結び目の大きさ


# ---------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------
def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
                 bpy.data.lights, bpy.data.cameras):
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


def link_obj(ob, parent=None):
    bpy.context.collection.objects.link(ob)
    if parent:
        ob.parent = parent
    return ob


def mesh_from_bm(name, bm, mat, parent=None, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    me.materials.append(mat)
    return link_obj(bpy.data.objects.new(name, me), parent)


def smoothstep(e0, e1, x):
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------
# マテリアル
# ---------------------------------------------------------------
def principled(name, color, rough, metallic=0.0, coat=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = color
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    set_input(b, ["Coat Weight", "Clearcoat"], coat)
    set_input(b, ["Coat Roughness", "Clearcoat Roughness"], 0.05)
    return m


def emissive(name, color, strength):
    m = principled(name, (0.0, 0.0, 0.0, 1.0), 0.4)
    b = m.node_tree.nodes["Principled BSDF"]
    set_input(b, ["Emission Color", "Emission"], color)
    set_input(b, ["Emission Strength"], strength * GLOW)
    return m


def math_node(nt, op, a, b=None, val=None):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    if isinstance(a, (int, float)):
        n.inputs[0].default_value = a
    else:
        nt.links.new(a, n.inputs[0])
    if b is not None:
        if isinstance(b, (int, float)):
            n.inputs[1].default_value = b
        else:
            nt.links.new(b, n.inputs[1])
    return n.outputs[0]


def ribbon_material():
    """パステルピンクのサテン＋薄膜干渉＋中央の点線データライン（UVは実寸）"""
    m = principled("Ribbon", RIBBON_COLOR, 0.30, coat=0.6)
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    set_input(b, ["Sheen Weight", "Sheen"], 0.35)
    set_input(b, ["Sheen Tint"], (1.0, 0.55, 0.75, 1.0))
    set_input(b, ["Thin Film Thickness"], IRIDESCENCE)
    set_input(b, ["Thin Film IOR"], 1.45)
    set_input(b, ["Subsurface Weight"], 0.15)
    set_input(b, ["Emission Color", "Emission"], LINE_COLOR)

    uv = nt.nodes.new("ShaderNodeUVMap")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs["UV"], sep.inputs[0])
    u, v = sep.outputs["X"], sep.outputs["Y"]
    # 中央の点線: |v| < 幅 かつ fract(u/ピッチ) < 0.55
    av = math_node(nt, "ABSOLUTE", v)
    on_line = math_node(nt, "LESS_THAN", av, 0.007)
    fr = math_node(nt, "FRACT", math_node(nt, "DIVIDE", u, 0.075))
    dash = math_node(nt, "LESS_THAN", fr, 0.55)
    # サイドの細いライン（ふちから少し内側）
    side = math_node(nt, "LESS_THAN",
                     math_node(nt, "ABSOLUTE", math_node(nt, "SUBTRACT", av, 0.075)), 0.0025)
    mask = math_node(nt, "MAXIMUM", math_node(nt, "MULTIPLY", on_line, dash),
                     math_node(nt, "MULTIPLY", side, 0.45))
    strength = math_node(nt, "MULTIPLY", mask, 5.0 * GLOW)
    nt.links.new(strength, get_input(b, ["Emission Strength"]))
    return m


def pearl_material():
    m = principled("Pearl", (0.93, 0.90, 0.96, 1.0), 0.18, metallic=0.15, coat=1.0)
    set_input(m.node_tree.nodes["Principled BSDF"], ["Thin Film Thickness"], 300.0)
    return m


def backdrop_material():
    """ラベンダーのホリゾント + 細いネオングリッド"""
    m = principled("Backdrop", (0.06, 0.035, 0.09, 1), 0.3)
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    set_input(b, ["Emission Color", "Emission"], GRID_COLOR)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])

    def line(sock, density, width):
        fr = math_node(nt, "FRACT", math_node(nt, "MULTIPLY", sock, density))
        ab = math_node(nt, "ABSOLUTE", math_node(nt, "SUBTRACT", fr, 0.5))
        return math_node(nt, "GREATER_THAN", ab, 0.5 - width)

    yz = math_node(nt, "ADD", sep.outputs["Y"], sep.outputs["Z"])
    g = math_node(nt, "MAXIMUM", line(sep.outputs["X"], 1.25, 0.008),
                  line(yz, 1.25, 0.008))
    # 手前と奥でフェード
    fade = math_node(nt, "MULTIPLY",
                     math_node(nt, "SUBTRACT", 1.0,
                               math_node(nt, "MINIMUM",
                                         math_node(nt, "DIVIDE",
                                                   math_node(nt, "ABSOLUTE", sep.outputs["X"]), 6.0), 1.0)),
                     0.9 * GLOW)
    nt.links.new(math_node(nt, "MULTIPLY", g, fade), get_input(b, ["Emission Strength"]))
    return m


def make_materials():
    return {
        "ribbon": ribbon_material(),
        "edge": emissive("Edge", EDGE_COLOR, 4.0),
        "heart": emissive("Heart", HEART_COLOR, 9.0),
        "ring": emissive("Ring", EDGE_COLOR, 9.0),
        "sparkle": emissive("Sparkle", SPARKLE_COLOR, 12.0),
        "pearl": pearl_material(),
        "glass": principled("BlackGlass", (0.01, 0.008, 0.02, 1), 0.08, coat=1.0),
        "metal": principled("Silver", (0.85, 0.82, 0.88, 1), 0.22, metallic=1.0),
    }


# ---------------------------------------------------------------
# 帯（ストリップ）の掃引
# ---------------------------------------------------------------
def build_strip(name, frames, mat, parent, nu=13):
    """frames: [(中心点, 幅ベクトル(半幅), ふくらみ方向, ふくらみ量, 長さ方向の実長)]
    幅方向に nu 列の頂点を並べ、UV は実寸（u=長さ, v=幅方向の位置）"""
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    rows, uvs = [], []
    for c, w, nrm, puff, arc in frames:
        row, uvrow = [], []
        for k in range(nu):
            t = -1.0 + 2.0 * k / (nu - 1)
            p = c + w * t + nrm * (puff * (1.0 - t * t))
            row.append(bm.verts.new(p))
            uvrow.append((arc, w.length * t))
        rows.append(row)
        uvs.append(uvrow)
    for i in range(len(rows) - 1):
        for k in range(nu - 1):
            f = bm.faces.new((rows[i][k], rows[i][k + 1], rows[i + 1][k + 1], rows[i + 1][k]))
            for lp, (ii, kk) in zip(f.loops, ((i, k), (i, k + 1), (i + 1, k + 1), (i + 1, k))):
                lp[uvl].uv = uvs[ii][kk]
    ob = mesh_from_bm(name, bm, mat, parent)
    sol = ob.modifiers.new("Thickness", "SOLIDIFY")
    sol.thickness = THICK
    sol.offset = 0.0
    return ob


def edge_pipe(name, pts, mat, parent):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = PIPE_R
    cu.bevel_resolution = 3
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, q in zip(sp.points, pts):
        p.co = (q.x, q.y, q.z, 1.0)
    cu.materials.append(mat)
    return link_obj(bpy.data.objects.new(name, cu), parent)


def accumulate(points):
    arc, out = 0.0, []
    for i, p in enumerate(points):
        if i:
            arc += (p - points[i - 1]).length
        out.append(arc)
    return out


def build_loop(side, mats, parent):
    """ループ1枚。中心線は結び目でとがるしずく形（前の帯→先端で折り返し→後ろの帯）"""
    n = 180
    L, D = LOOP_LENGTH, 0.42
    tilt = Matrix.Rotation(math.radians(-LOOP_TILT * side), 3, "Y")
    cs, ws, ns, puffs = [], [], [], []
    for i in range(n + 1):
        s = 0.04 + (math.tau - 0.08) * i / n
        open_ = (1 - math.cos(s)) / 2
        x = L * open_ * side
        y = -D * math.sin(s) * open_
        z = 0.05 * math.sin(s) * open_       # 前の帯を少しだけ上に
        c = tilt @ Vector((x, y, z))
        # 帯の幅：結び目でしぼって外側で広がる
        wid = 0.10 + (LOOP_WIDTH / 2 - 0.10) * smoothstep(0.0, 1.0, open_ ** 0.8)
        wv = tilt @ Vector((0.0, 0.0, wid))
        cs.append(c)
        ws.append(wv)
    for i in range(n + 1):
        a, b = cs[max(i - 1, 0)], cs[min(i + 1, n)]
        t = (b - a).normalized()
        nrm = t.cross(ws[i].normalized()).normalized()
        centroid = tilt @ Vector((L / 2 * side, 0, 0))
        if nrm.dot(cs[i] - centroid) < 0:
            nrm = -nrm
        s = 0.04 + (math.tau - 0.08) * i / n
        open_ = (1 - math.cos(s)) / 2
        ns.append(nrm)
        puffs.append(LOOP_PUFF * smoothstep(0.05, 0.6, open_))
    arcs = accumulate(cs)
    frames = list(zip(cs, ws, ns, puffs, arcs))
    tag = "R" if side > 0 else "L"
    build_strip(f"Loop{tag}", frames, mats["ribbon"], parent)
    for sgn in (-1, 1):
        pts = [c + w * sgn for c, w in zip(cs, ws)]
        edge_pipe(f"LoopPipe{tag}{sgn}", pts, mats["edge"], parent)


def build_tail(side, mats, parent):
    """たれ。先端はV字カット。ゆるく波打たせる"""
    n, nu = 120, 13
    start = Vector((0.06 * side, -0.10, -0.05))
    end = Vector((TAIL_SPREAD * side, 0.05, -TAIL_LENGTH))
    d = (end - start)
    fwd = Vector((0, -1, 0))
    vcut = 0.16

    def center(t):
        c = start + d * t
        c.x += 0.10 * side * math.sin(t * math.pi)              # 外へふくらむ
        c.y += -0.08 * math.sin(t * math.pi * 1.6 + 0.3)        # 前後に波打つ
        return c

    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    rows, uvs = [], []
    edges = {-1: [], 1: []}
    for i in range(n + 1):
        row, uvrow = [], []
        for k in range(nu):
            u = -1.0 + 2.0 * k / (nu - 1)
            # V字カット：中央ほど短い
            t = (i / n) * (1 - vcut * (1 - abs(u)))
            c = center(t)
            tan = (center(min(t + 0.01, 1)) - center(max(t - 0.01, 0))).normalized()
            wdir = fwd.cross(tan).normalized()
            # すこしねじる
            tw = Matrix.Rotation(0.35 * math.sin(t * math.pi) * side, 3, tan)
            wdir = tw @ wdir
            half = 0.17 + 0.13 * t
            nrm = tan.cross(wdir).normalized()
            p = c + wdir * half * u + nrm * 0.03 * (1 - u * u)
            row.append(bm.verts.new(p))
            uvrow.append((t * TAIL_LENGTH, half * u))
            if k == 0:
                edges[-1].append(p)
            elif k == nu - 1:
                edges[1].append(p)
        rows.append(row)
        uvs.append(uvrow)
    for i in range(n):
        for k in range(nu - 1):
            f = bm.faces.new((rows[i][k], rows[i][k + 1], rows[i + 1][k + 1], rows[i + 1][k]))
            for lp, (ii, kk) in zip(f.loops, ((i, k), (i, k + 1), (i + 1, k + 1), (i + 1, k))):
                lp[uvl].uv = uvs[ii][kk]
    tip = [v.co.copy() for v in rows[-1]]
    tag = "R" if side > 0 else "L"
    ob = mesh_from_bm(f"Tail{tag}", bm, mats["ribbon"], parent)
    sol = ob.modifiers.new("Thickness", "SOLIDIFY")
    sol.thickness = THICK
    sol.offset = 0.0
    # パイピング：両ふち＋V字の先端をつなぐ
    pts = edges[-1] + tip[1:-1] + list(reversed(edges[1]))
    edge_pipe(f"TailPipe{tag}", pts, mats["edge"], parent)


def heart_pts(scale, seg=64):
    pts = []
    for i in range(seg):
        t = math.tau * i / seg
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x * scale, y * scale + 0.1 * scale))
    return pts


def build_knot(mats, parent):
    """結び目：パールのカプセル＋黒ガラスの窓＋ハートLED＋発光リング"""
    knot = link_obj(bpy.data.objects.new("Knot", None), parent)
    knot.scale = (KNOT_SCALE,) * 3
    knot.location = (0, -0.02, 0)
    parent = knot
    # カプセル本体（つぶした球）
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * 0.25, v.co.y * 0.17, v.co.z * 0.31))
    shell = mesh_from_bm("KnotShell", bm, mats["pearl"], parent)

    # 前面の窓（角丸の板、カプセル前面に沿わせて少し湾曲）
    bm = bmesh.new()
    hw, hh, r = 0.15, 0.19, 0.07
    outline = []
    for cx, cz, a0 in ((hw - r, hh - r, 0), (-hw + r, hh - r, 90),
                       (-hw + r, -hh + r, 180), (hw - r, -hh + r, 270)):
        for i in range(9):
            a = math.radians(a0 + 90 * i / 8)
            outline.append((cx + r * math.cos(a), cz + r * math.sin(a)))

    def front_y(x, z, off):
        # 楕円体の表面の y（前面）
        q = 1 - (x / 0.25) ** 2 - (z / 0.31) ** 2
        return -0.17 * math.sqrt(max(q, 0.0)) - off

    center = bm.verts.new((0, front_y(0, 0, 0.004), 0))
    ring = [bm.verts.new((x, front_y(x, z, 0.004), z)) for x, z in outline]
    for i in range(len(ring)):
        bm.faces.new((center, ring[i], ring[(i + 1) % len(ring)]))
    win = mesh_from_bm("KnotWindow", bm, mats["glass"], parent)
    win.modifiers.new("Thick", "SOLIDIFY").thickness = 0.006

    # ハートLED（押し出し）
    bm = bmesh.new()
    hp = heart_pts(0.0072)
    y0 = front_y(0, 0, 0.012)
    bot = [bm.verts.new((x, y0 + 0.01, z)) for x, z in hp]
    top = [bm.verts.new((x, y0, z)) for x, z in hp]
    bm.faces.new(bot)
    bm.faces.new(list(reversed(top)))
    for i in range(len(hp)):
        j = (i + 1) % len(hp)
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh_from_bm("HeartLED", bm, mats["heart"], parent, smooth=False)

    # 窓のふちの発光ライン
    pts = [Vector((x * 1.08, front_y(x * 1.08, z * 1.06, 0.006), z * 1.06)) for x, z in outline]
    pts.append(pts[0])
    pipe = edge_pipe("WindowRim", pts, mats["ring"], parent)
    pipe.data.bevel_depth = 0.006

    # 上下の発光リング（カプセルの腰）
    for zz in (-0.24, 0.24):
        k = math.sqrt(1 - (zz / 0.31) ** 2)
        pts = [Vector((0.25 * k * 1.02 * math.cos(t), 0.17 * k * 1.02 * math.sin(t), zz))
               for t in (math.tau * i / 96 for i in range(97))]
        p = edge_pipe(f"WaistRing{zz:+.2f}", pts, mats["ring"], parent)
        p.data.bevel_depth = 0.008

    # 両脇の金属ジョイント（帯がささる口）
    for side in (-1, 1):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=0.085,
                              radius2=0.085, depth=0.07,
                              matrix=Matrix.Translation((0.235 * side, 0, 0))
                              @ Matrix.Rotation(math.pi / 2, 4, "Y")
                              @ Matrix.Diagonal((1.0, 1.35, 1.0, 1.0)))
        mesh_from_bm(f"Joint{side}", bm, mats["metal"], parent)
        mod = bpy.data.objects[f"Joint{side}"].modifiers.new("Bevel", "BEVEL")
        mod.width = 0.01
        mod.segments = 3


def build_sparkles(mats, rng):
    """4方向に光るキラキラ（カメラ向き）"""
    for i in range(SPARKLE_COUNT):
        bm = bmesh.new()
        size = rng.uniform(0.05, 0.12)
        pts = []
        for k in range(16):
            a = math.tau * k / 16
            rr = size if k % 4 == 0 else (size * 0.22 if k % 2 == 0 else size * 0.12)
            pts.append((rr * math.cos(a), rr * math.sin(a)))
        c = bm.verts.new((0, 0, 0))
        vs = [bm.verts.new((x, 0, z)) for x, z in pts]
        for k in range(16):
            bm.faces.new((c, vs[(k + 1) % 16], vs[k]))
        ob = mesh_from_bm(f"Sparkle{i}", bm, mats["sparkle"], smooth=False)
        ang = rng.uniform(0, math.tau)
        rad = rng.uniform(1.4, 1.7)
        ob.location = (rad * math.cos(ang) * 0.95, rng.uniform(-0.6, 0.4),
                       BOW_Z - 0.25 + rad * math.sin(ang) * 0.95)
        ob.rotation_euler = (0, rng.uniform(-0.3, 0.3), 0)


def build_bow(mats):
    root = link_obj(bpy.data.objects.new("Ribbon", None))
    root.location = (0, 0, BOW_Z)
    for side in (-1, 1):
        build_loop(side, mats, root)
        build_tail(side, mats, root)
    build_knot(mats, root)
    return root


# ---------------------------------------------------------------
# 床・ライト・カメラ
# ---------------------------------------------------------------
def build_stage(mats):
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
    return mesh_from_bm("Backdrop", bm, backdrop_material())


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

    area("Key", (-2.0, -4.5, 4.5), 260, 4.0, (1.0, 0.95, 0.97))
    area("RimMagenta", (-3.0, 2.5, 3.5), 500, 1.5, (1.0, 0.25, 0.7))
    area("RimCyan", (3.2, 2.5, 3.0), 550, 1.5, (0.2, 0.8, 1.0))
    area("Fill", (3.5, -4.0, 1.0), 90, 4.0, (0.85, 0.7, 1.0))

    target = link_obj(bpy.data.objects.new("CamTarget", None))
    target.location = target_loc

    cd = bpy.data.cameras.new("Camera")
    cd.lens = 70
    cd.dof.use_dof = True
    cd.dof.focus_object = target
    cd.dof.aperture_fstop = 4.0
    cam = link_obj(bpy.data.objects.new("Camera", cd))
    cam.location = (1.2, -8.6, 2.6)
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
        set_input(gl, ["Threshold"], 1.2)
        set_input(gl, ["Strength"], 0.55)
        set_input(gl, ["Size"], 0.6)
    else:
        gl.glare_type = "BLOOM" if "BLOOM" in [i.identifier for i in
                                               gl.bl_rna.properties["glare_type"].enum_items] else "FOG_GLOW"
        gl.threshold = 1.2
        gl.size = 8
        try:
            gl.mix = -0.4
        except AttributeError:
            pass
    ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
    ng.links.new(gl.outputs["Image"], out.inputs[0])


def setup_render():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 8
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
    bg.inputs["Color"].default_value = (0.06, 0.035, 0.09, 1)
    bg.inputs["Strength"].default_value = 0.5
    if BLOOM:
        setup_bloom(sc)


# ---------------------------------------------------------------
def main():
    clear_scene()
    mats = make_materials()
    build_bow(mats)
    build_sparkles(mats, random.Random(SEED))
    build_stage(mats)
    build_lights_camera((0.0, 0.0, BOW_Z - 0.3))
    setup_render()

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--render" in argv:
        out = argv[argv.index("--render") + 1]
        bpy.context.scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print("Rendered:", out)


if __name__ == "__main__":
    main()
