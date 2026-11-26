"""
Blender制作日記 2026-11-27 : サイバーなサイコロ (Cyber Dice)

使い方:
  Blender の Scripting タブでこのファイルを開いて「Run Script」。
  またはコマンドラインから:
    blender --background --python cyber_dice.py -- --render out.png

内容:
  - 角を丸めたガンメタの本体に、6面それぞれ黒い強化プレートをはめ込む
  - プレートの隙間からネオンがにじむシーム発光
  - 目（ピップ）は 穴あけ → 発光ディスク → アイリスリング → 金属ベゼル の4層
  - 目から伸びる回路パターン（45度配線・ビア・チップ部品）を面ごとに自動生成
  - 四隅のヘックスネジ、面番号の刻印、1の目はマゼンタの大きなレンズ
  - ネオングリッドのホリゾント、マゼンタ／シアンのリムライト、ブルーム
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
PIP_COLOR = (0.0, 0.85, 1.0, 1.0)      # 目の発光（シアン）
ONE_COLOR = (1.0, 0.05, 0.55, 1.0)     # 1の目の発光（マゼンタ）
SEAM_COLOR = (0.75, 0.10, 1.0, 1.0)    # プレート隙間の発光（バイオレット）
TRACE_COLOR = (0.0, 0.85, 1.0, 1.0)    # 回路パターンの発光
GRID_COLOR = (0.55, 0.10, 1.0, 1.0)    # 床グリッドの発光
GLOW = 1.0                             # 発光の強さ（全体倍率）
TRACE_COUNT = 14                       # 1面あたりの回路パターン本数
SEED = 27                              # 回路パターンの乱数シード
DIE_YAW = -16.0                        # メインのサイコロの向き（度）
BLOOM = True                           # コンポジットでブルームをかける
RESOLUTION = (1080, 1350)              # SNS向け 4:5
SAMPLES = 128
TURNTABLE = False                      # True で120フレームの回転アニメ

# ---------------------------------------------------------------
# 寸法（サイコロ1辺 = 1.0）
# ---------------------------------------------------------------
H = 0.5                 # 本体の半辺
BODY_BEVEL = 0.10       # 本体の角の丸み
PLATE_HALF = 0.37       # プレートの半辺
PLATE_R = 0.07          # プレートの角丸
PLATE_T = 0.035         # プレートの厚み
PLATE_Z0 = H + 0.004    # プレート下面
PLATE_Z1 = PLATE_Z0 + PLATE_T
PIP_R = 0.062           # 目の穴の半径
PIP_D = 0.19            # 目の間隔
ONE_R = 0.12            # 1の目の半径

# 目の配置（面ローカルUV、PIP_D 単位）
PIP_LAYOUT = {
    1: [(0, 0)],
    2: [(-1, 1), (1, -1)],
    3: [(-1, 1), (0, 0), (1, -1)],
    4: [(-1, -1), (-1, 1), (1, -1), (1, 1)],
    5: [(-1, -1), (-1, 1), (0, 0), (1, -1), (1, 1)],
    6: [(-1, -1), (-1, 0), (-1, 1), (1, -1), (1, 0), (1, 1)],
}

# 面の法線 → (目の数, 面の上方向)
FACES = [
    ((0, 0, 1), 5, (0, 1, 0)),
    ((0, 0, -1), 2, (0, 1, 0)),
    ((0, -1, 0), 4, (0, 0, 1)),
    ((0, 1, 0), 3, (0, 0, 1)),
    ((1, 0, 0), 1, (0, 0, 1)),
    ((-1, 0, 0), 6, (0, 0, 1)),
]


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


def mesh_from_bm(name, bm, mat, parent=None, smooth=False):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth
    me.materials.append(mat)
    return link_obj(bpy.data.objects.new(name, me), parent)


# --- bmesh プリミティブ（面ローカル座標で配置） ---
def bm_disc(bm, c, r, seg=48):
    bmesh.ops.create_circle(bm, cap_ends=True, segments=seg, radius=r,
                            matrix=Matrix.Translation(c))


def bm_cyl(bm, c, r, depth, seg=48):
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                          radius1=r, radius2=r, depth=depth,
                          matrix=Matrix.Translation(c))


def bm_box(bm, c, size, rot=0.0):
    m = (Matrix.Translation(c) @ Matrix.Rotation(rot, 4, "Z")
         @ Matrix.Diagonal((*size, 1)))
    bmesh.ops.create_cube(bm, size=1.0, matrix=m)


def bm_annulus(bm, c, r0, r1, seg=64):
    a = [bm.verts.new((c[0] + r0 * math.cos(t), c[1] + r0 * math.sin(t), c[2]))
         for t in (math.tau * i / seg for i in range(seg))]
    b = [bm.verts.new((c[0] + r1 * math.cos(t), c[1] + r1 * math.sin(t), c[2]))
         for t in (math.tau * i / seg for i in range(seg))]
    for i in range(seg):
        j = (i + 1) % seg
        bm.faces.new((a[i], a[j], b[j], b[i]))


def bm_torus(bm, c, R, r, seg=56, rseg=10):
    rings = []
    for i in range(seg):
        t = math.tau * i / seg
        ring = []
        for k in range(rseg):
            s = math.tau * k / rseg
            rr = R + r * math.cos(s)
            ring.append(bm.verts.new((c[0] + rr * math.cos(t),
                                      c[1] + rr * math.sin(t),
                                      c[2] + r * math.sin(s))))
        rings.append(ring)
    for i in range(seg):
        for k in range(rseg):
            a, b = rings[i], rings[(i + 1) % seg]
            bm.faces.new((a[k], b[k], b[(k + 1) % rseg], a[(k + 1) % rseg]))


def rounded_rect(half, r, seg=8):
    pts = []
    for cx, cy, a0 in ((half - r, half - r, 0), (-half + r, half - r, 90),
                       (-half + r, -half + r, 180), (half - r, -half + r, 270)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def bm_rounded_prism(bm, half, r, z0, z1):
    bot = [bm.verts.new((x, y, z0)) for x, y in rounded_rect(half, r)]
    top = [bm.verts.new((x, y, z1)) for x, y in rounded_rect(half, r)]
    bm.faces.new(list(reversed(bot)))
    bm.faces.new(top)
    n = len(bot)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bot[i], bot[j], top[j], top[i]))


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


def body_material():
    """ガンメタ。ノイズで細かなラフネスむらをつける"""
    m = principled("Body", (0.10, 0.105, 0.12, 1), 0.32, metallic=1.0)
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 60.0
    set_input(noise, ["Detail"], 8.0)
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["To Min"].default_value = 0.22
    ramp.inputs["To Max"].default_value = 0.45
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], b.inputs["Roughness"])
    return m


def backdrop_material():
    """暗いグロッシーな床 + ネオングリッド"""
    m = principled("Backdrop", (0.012, 0.010, 0.022, 1), 0.28)
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    set_input(b, ["Emission Color", "Emission"], GRID_COLOR)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])

    def line(sock, density, width):
        mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = density
        nt.links.new(sock, mul.inputs[0])
        fr = nt.nodes.new("ShaderNodeMath"); fr.operation = "FRACT"
        nt.links.new(mul.outputs[0], fr.inputs[0])
        sub = nt.nodes.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
        sub.inputs[1].default_value = 0.5
        nt.links.new(fr.outputs[0], sub.inputs[0])
        ab = nt.nodes.new("ShaderNodeMath"); ab.operation = "ABSOLUTE"
        nt.links.new(sub.outputs[0], ab.inputs[0])
        gt = nt.nodes.new("ShaderNodeMath"); gt.operation = "GREATER_THAN"
        gt.inputs[1].default_value = 0.5 - width
        nt.links.new(ab.outputs[0], gt.inputs[0])
        return gt.outputs[0]

    yz = nt.nodes.new("ShaderNodeMath"); yz.operation = "ADD"
    nt.links.new(sep.outputs["Y"], yz.inputs[0])
    nt.links.new(sep.outputs["Z"], yz.inputs[1])
    lx = line(sep.outputs["X"], 3.0, 0.014)
    ly = line(yz.outputs[0], 3.0, 0.014)
    mx = nt.nodes.new("ShaderNodeMath"); mx.operation = "MAXIMUM"
    nt.links.new(lx, mx.inputs[0]); nt.links.new(ly, mx.inputs[1])
    # 手前ほど明るく、奥はフェード
    fade = nt.nodes.new("ShaderNodeMapRange")
    fade.inputs["From Min"].default_value = 4.5
    fade.inputs["From Max"].default_value = -1.0
    fade.inputs["To Min"].default_value = 0.0
    fade.inputs["To Max"].default_value = 1.0
    nt.links.new(yz.outputs[0], fade.inputs["Value"])
    f2 = nt.nodes.new("ShaderNodeMath"); f2.operation = "MULTIPLY"
    nt.links.new(mx.outputs[0], f2.inputs[0]); nt.links.new(fade.outputs[0], f2.inputs[1])
    st = nt.nodes.new("ShaderNodeMath"); st.operation = "MULTIPLY"
    st.inputs[1].default_value = 1.2 * GLOW
    nt.links.new(f2.outputs[0], st.inputs[0])
    nt.links.new(st.outputs[0], get_input(b, ["Emission Strength"]))
    return m


def make_materials():
    return {
        "body": body_material(),
        "plate": principled("Plate", (0.012, 0.012, 0.016, 1), 0.25,
                            metallic=0.3, coat=1.0),
        "metal": principled("Bezel", (0.62, 0.64, 0.70, 1), 0.18, metallic=1.0),
        "dark": principled("Iris", (0.01, 0.01, 0.012, 1), 0.35, metallic=0.5),
        "chip": principled("Chip", (0.03, 0.03, 0.035, 1), 0.45),
        "pip": emissive("PipGlow", PIP_COLOR, 30.0),
        "one": emissive("OneGlow", ONE_COLOR, 30.0),
        "seam": emissive("SeamGlow", SEAM_COLOR, 18.0),
        "trace": emissive("TraceGlow", TRACE_COLOR, 5.0),
        "via": emissive("ViaGlow", ONE_COLOR, 10.0),
        "text": emissive("LabelGlow", TRACE_COLOR, 6.0),
    }


# ---------------------------------------------------------------
# 回路パターンの自動生成（面ローカル2D）
# ---------------------------------------------------------------
DIRS = [Vector((math.cos(math.radians(a)), math.sin(math.radians(a))))
        for a in range(0, 360, 45)]
CELL = 0.022


def gen_traces(rng, pips, pip_r, blocked, n):
    lim = PLATE_HALF - 0.045
    occ = set(blocked)
    traces = []

    def free(p, start_pip):
        if abs(p.x) > lim or abs(p.y) > lim:
            return False
        for i, c in enumerate(pips):
            rr = pip_r + 0.028 if i != start_pip else pip_r + 0.02
            if (p - c).length < rr:
                return False
        return (round(p.x / CELL), round(p.y / CELL)) not in occ

    for attempt in range(n * 30):
        if len(traces) >= n:
            break
        if pips and rng.random() < 0.65:
            sp = rng.randrange(len(pips))
            d = rng.randrange(8)
            start = pips[sp] + DIRS[d] * (pip_r + 0.022)
        else:
            sp = -1
            d = rng.randrange(8)
            start = Vector((rng.uniform(-lim, lim), rng.uniform(-lim, lim)))
        if not free(start, sp):
            continue
        pts = [start]
        p = start
        for _ in range(rng.randint(3, 9)):
            seg = rng.choice((0.044, 0.066, 0.088))
            q = p + DIRS[d] * seg
            ok = all(free(p.lerp(q, k / 8), sp) for k in range(1, 9))
            if not ok:
                break
            pts.append(q)
            p = q
            if rng.random() < 0.5:
                d = (d + rng.choice((-1, 1))) % 8
        if len(pts) < 3:
            continue
        # 占有セルを登録（隣接も埋めて線同士の間隔を確保）
        for a, b in zip(pts, pts[1:]):
            for k in range(13):
                s = a.lerp(b, k / 12)
                cx, cy = round(s.x / CELL), round(s.y / CELL)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        occ.add((cx + dx, cy + dy))
        traces.append((pts, sp >= 0))
    return traces, occ


def block_rect(occ, x0, y0, x1, y1):
    for i in range(round(x0 / CELL) - 1, round(x1 / CELL) + 2):
        for j in range(round(y0 / CELL) - 1, round(y1 / CELL) + 2):
            occ.add((i, j))


# ---------------------------------------------------------------
# 1面ぶんのパーツ
# ---------------------------------------------------------------
def build_face(root, normal, number, up, mats, rng, tag):
    n = Vector(normal)
    v = Vector(up)
    u = v.cross(n)
    face = bpy.data.objects.new(f"{tag}_Face{number}", None)
    link_obj(face, root)
    face.matrix_parent_inverse = Matrix.Identity(4)
    face.rotation_euler = Matrix((u, v, n)).transposed().to_euler()

    big = number == 1
    pr = ONE_R if big else PIP_R
    pips = [Vector((a * PIP_D, b * PIP_D)) for a, b in PIP_LAYOUT[number]]

    # --- プレート（目の穴をブーリアンで抜く） ---
    bm = bmesh.new()
    bm_rounded_prism(bm, PLATE_HALF, PLATE_R, PLATE_Z0, PLATE_Z1)
    plate = mesh_from_bm(f"{tag}_Plate{number}", bm, mats["plate"], face)
    bm = bmesh.new()
    for c in pips:
        bm_cyl(bm, (c.x, c.y, (PLATE_Z0 + PLATE_Z1) / 2), pr, PLATE_T * 3, 64)
    cutter = mesh_from_bm(f"{tag}_Cutter{number}", bm, mats["plate"], face)
    mod = plate.modifiers.new("Holes", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    try:
        mod.solver = "EXACT"
    except TypeError:
        pass
    dg = bpy.context.evaluated_depsgraph_get()
    new_me = bpy.data.meshes.new_from_object(plate.evaluated_get(dg))
    plate.modifiers.remove(mod)
    plate.data = new_me
    bpy.data.objects.remove(cutter, do_unlink=True)
    bev = plate.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.004
    bev.segments = 3
    bev.limit_method = "ANGLE"

    # --- シーム発光（プレートの下から少しはみ出す） ---
    bm = bmesh.new()
    bm_rounded_prism(bm, PLATE_HALF + 0.012, PLATE_R + 0.012, H - 0.002, PLATE_Z0)
    mesh_from_bm(f"{tag}_Seam{number}", bm, mats["seam"], face)

    # --- 目：発光ディスク / アイリス / ベゼル ---
    glow = mats["one"] if big else mats["pip"]
    bm_g, bm_i, bm_m = bmesh.new(), bmesh.new(), bmesh.new()
    for c in pips:
        bm_disc(bm_g, (c.x, c.y, PLATE_Z0 + 0.003), pr, 64)
        bm_annulus(bm_i, (c.x, c.y, PLATE_Z0 + 0.012), pr * 0.42, pr * 0.62)
        bm_annulus(bm_i, (c.x, c.y, PLATE_Z0 + 0.012), pr * 0.86, pr * 0.98)
        bm_disc(bm_i, (c.x, c.y, PLATE_Z0 + 0.013), pr * 0.16, 32)
        bm_torus(bm_m, (c.x, c.y, PLATE_Z1), pr + 0.002, 0.0065)
        if big:
            # 1の目：外周リングと照準の目盛り
            bm_annulus(bm_g, (0, 0, PLATE_Z1 + 0.0008), pr + 0.045, pr + 0.053, 96)
            for k in range(12):
                a = math.tau * k / 12
                L = 0.026 if k % 3 == 0 else 0.013
                r0 = pr + 0.062
                bm_box(bm_g, ((r0 + L / 2) * math.cos(a), (r0 + L / 2) * math.sin(a),
                              PLATE_Z1 + 0.001), (L, 0.004, 0.002), a)
    mesh_from_bm(f"{tag}_PipGlow{number}", bm_g, glow, face)
    mesh_from_bm(f"{tag}_Iris{number}", bm_i, mats["dark"], face)
    mesh_from_bm(f"{tag}_Bezel{number}", bm_m, mats["metal"], face, smooth=True)

    # --- 四隅のヘックスネジ ---
    bm_s, bm_sd = bmesh.new(), bmesh.new()
    sc = PLATE_HALF - 0.04
    blocked = set()
    for sx in (-1, 1):
        for sy in (-1, 1):
            c = (sx * sc, sy * sc)
            bm_cyl(bm_s, (c[0], c[1], PLATE_Z1 + 0.002), 0.017, 0.006, 6)
            bm_cyl(bm_sd, (c[0], c[1], PLATE_Z1 + 0.0035), 0.0075, 0.004, 6)
            block_rect(blocked, c[0] - 0.03, c[1] - 0.03, c[0] + 0.03, c[1] + 0.03)
    mesh_from_bm(f"{tag}_Screw{number}", bm_s, mats["metal"], face)
    mesh_from_bm(f"{tag}_ScrewHole{number}", bm_sd, mats["dark"], face)

    # --- 面番号の刻印 ---
    lab_y = -PLATE_HALF + 0.045
    block_rect(blocked, -0.11, lab_y - 0.02, 0.11, lab_y + 0.03)
    tc = bpy.data.curves.new(f"{tag}_Label{number}", "FONT")
    tc.body = f"D6 // 0{number}"
    tc.size = 0.032
    tc.align_x = "CENTER"
    tc.extrude = 0.0006
    tx = link_obj(bpy.data.objects.new(f"{tag}_Label{number}", tc), face)
    tx.location = (0, lab_y, PLATE_Z1 + 0.0007)
    tc.materials.append(mats["text"])
    if big:
        for c in pips:  # 外周リング・目盛りの分を予約
            block_rect(blocked, -0.21, -0.21, 0.21, 0.21)

    # --- 回路パターン ---
    traces, occ = gen_traces(rng, [] if big else pips, pr, blocked, TRACE_COUNT)
    cu = bpy.data.curves.new(f"{tag}_Traces{number}", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.0032
    cu.bevel_resolution = 2
    bm_v = bmesh.new()
    for pts, from_pip in traces:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for p, q in zip(sp.points, pts):
            p.co = (q.x, q.y, PLATE_Z1 + 0.0004, 1)
        ends = [pts[-1]] if from_pip else [pts[0], pts[-1]]
        for e in ends:
            bm_torus(bm_v, (e.x, e.y, PLATE_Z1 + 0.0008), 0.009, 0.0032, 24, 6)
    cu.materials.append(mats["trace"])
    link_obj(bpy.data.objects.new(f"{tag}_Traces{number}", cu), face)
    mesh_from_bm(f"{tag}_Vias{number}", bm_v, mats["via"], face, smooth=True)

    # --- チップ部品（空いた場所に数個） ---
    bm_c, bm_p = bmesh.new(), bmesh.new()
    lim = PLATE_HALF - 0.07
    placed = 0
    for _ in range(200):
        if placed >= 3:
            break
        c = Vector((rng.uniform(-lim, lim), rng.uniform(-lim, lim)))
        w, h = rng.choice(((0.07, 0.04), (0.05, 0.05), (0.04, 0.022)))
        cells = [(round(x / CELL), round(y / CELL))
                 for x in (c.x - w / 2 - 0.01, c.x, c.x + w / 2 + 0.01)
                 for y in (c.y - h / 2 - 0.01, c.y, c.y + h / 2 + 0.01)]
        if any(k in occ for k in cells):
            continue
        if any((c - p).length < pr + 0.06 for p in pips):
            continue
        bm_box(bm_c, (c.x, c.y, PLATE_Z1 + 0.005), (w, h, 0.01))
        npin = max(2, int(w / 0.014))
        for k in range(npin):
            x = c.x - w / 2 + w * (k + 0.5) / npin
            for sy in (-1, 1):
                bm_box(bm_p, (x, c.y + sy * (h / 2 + 0.004), PLATE_Z1 + 0.0015),
                       (0.005, 0.009, 0.003))
        block_rect(occ, c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2)
        placed += 1
    mesh_from_bm(f"{tag}_Chips{number}", bm_c, mats["chip"], face)
    mesh_from_bm(f"{tag}_Pins{number}", bm_p, mats["metal"], face)


def build_die(tag, location, scale, rot, mats, seed):
    rng = random.Random(seed)
    root = bpy.data.objects.new(f"{tag}_Root", None)
    link_obj(root)
    root.location = location
    root.scale = (scale,) * 3
    root.rotation_euler = rot

    bpy.ops.mesh.primitive_cube_add(size=2 * H)
    body = bpy.context.active_object
    body.name = f"{tag}_Body"
    body.parent = root
    body.data.materials.append(mats["body"])
    bev = body.modifiers.new("Bevel", "BEVEL")
    bev.width = BODY_BEVEL
    bev.segments = 8
    for p in body.data.polygons:
        p.use_smooth = True
    try:
        body.data.shade_smooth()
    except AttributeError:
        pass

    for normal, number, up in FACES:
        build_face(root, normal, number, up, mats, rng, tag)
    return root


# ---------------------------------------------------------------
# ステージ・ライト・カメラ・レンダー
# ---------------------------------------------------------------
def build_stage(mats):
    me = bpy.data.meshes.new("Backdrop")
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
    floor = mesh_from_bm("Backdrop", bm, backdrop_material(), smooth=True)
    return floor


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

    area("Key", (-2.5, -1.0, 5.0), 140, 5.0, (0.85, 0.9, 1.0))
    area("RimMagenta", (-3.0, 3.0, 3.5), 450, 1.5, (1.0, 0.15, 0.6))
    area("RimCyan", (3.5, 2.5, 3.0), 600, 1.5, (0.1, 0.8, 1.0))
    area("Fill", (3.5, -4.5, 1.2), 60, 4.0, (0.6, 0.5, 1.0))

    target = link_obj(bpy.data.objects.new("CamTarget", None))
    target.location = target_loc

    cd = bpy.data.cameras.new("Camera")
    cd.lens = 85
    cd.dof.use_dof = True
    cd.dof.focus_object = target
    cd.dof.aperture_fstop = 3.2
    cam = link_obj(bpy.data.objects.new("Camera", cd))
    cam.location = (2.9, -5.3, 4.3)
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
        set_input(gl, ["Strength"], 0.6)
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
    bg.inputs["Color"].default_value = (0.02, 0.015, 0.05, 1)
    bg.inputs["Strength"].default_value = 0.4
    if BLOOM:
        setup_bloom(sc)


# ---------------------------------------------------------------
def main():
    clear_scene()
    mats = make_materials()
    z = PLATE_Z1 - 0.003  # 下面プレートで床に乗る高さ
    build_die("Main", (0, 0, z), 1.0, (0, 0, math.radians(DIE_YAW)), mats, SEED)
    # 奥にもう1個（ボケ用）。90度倒して別の面を見せる
    s2 = 0.62
    build_die("Back", (-1.9, 1.9, z * s2), s2,
              (math.radians(-90), 0, math.radians(20)), mats, SEED + 1)
    build_stage(mats)
    build_lights_camera((0.0, 0.05, 0.42))
    setup_render()

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--render" in argv:
        out = argv[argv.index("--render") + 1]
        bpy.context.scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print("Rendered:", out)


if __name__ == "__main__":
    main()
