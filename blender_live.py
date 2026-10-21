"""
Live Blender view of a Python file, rebuilt every time the file is saved.

    blender --python blender_live.py -- demo.py                         (live window)
    blender -b --python blender_live.py -- demo.py --render shot.png    (one snapshot)

Blender only draws what analyzer.py produces: cells, axes, colors, labels.
It never decides what a shape "is".

In the window: click any structure, then press N -> "CodeViz" tab shows its
source, count law and relations; "Focus" zooms to it, "Open in editor" jumps
to the line in VS Code.
"""
import ast
import json
import shutil
import subprocess
import importlib
import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import analyzer  # noqa: E402
import explain  # noqa: E402  (only reads its cache here; the API call runs outside Blender)

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SRC = os.path.abspath(ARGS[0]) if ARGS and not ARGS[0].startswith("--") else os.path.join(HERE, "demo.py")
RENDER = ARGS[ARGS.index("--render") + 1] if "--render" in ARGS else None
ANALYZER = os.path.join(HERE, "analyzer.py")
EXPLAIN_CACHE = explain.cache_path(SRC)
CLAUDE_COLOR = (0.80, 0.62, 1.0)

COLL = "CodeViz"
CUBE = 0.88          # cell size (gap = 1 - CUBE)
GAP = 5.0            # space between structures
ROW_WIDTH = 75.0
VIEW_DIR = Vector((0.30, 0.78, -0.62)).normalized()   # from front-left, looking down
MAX_EXTENT = 22.0    # bigger structures are scaled down (caption says so)
TEXT_COLOR = (0.92, 0.92, 0.95)
DIM_TEXT = (0.60, 0.62, 0.68)


# ------------------------------------------------------------------ scene helpers

def collection():
    c = bpy.data.collections.get(COLL)
    if c is None:
        c = bpy.data.collections.new(COLL)
        bpy.context.scene.collection.children.link(c)
    return c


def clear():
    c = collection()
    for ob in list(c.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
    for m in list(bpy.data.materials):
        if m.name.startswith("cv_") and m.users == 0:
            bpy.data.materials.remove(m)


def material(rgb):
    name = "cv_%02x%02x%02x" % tuple(int(255 * v) for v in rgb)
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.diffuse_color = (*rgb, 1.0)
        bsdf = m.node_tree.nodes.get("Principled BSDF") if m.node_tree else None
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.55
    return m


PIECE_TINTS = [(0.95, 0.45, 0.30), (0.30, 0.65, 0.95), (0.95, 0.85, 0.30), (0.45, 0.85, 0.50)]


def shade(rgb, k, n):
    """k-th of n pieces: piece 0 keeps the structure colour, others get a tint."""
    if n <= 1 or k == 0:
        return rgb
    t = PIECE_TINTS[(k - 1) % len(PIECE_TINTS)]
    return tuple(0.35 * a + 0.65 * b for a, b in zip(rgb, t))


def cubes_mesh(name, cells, origin, rgb, n_regions, parent=None, scale=1.0):
    verts, faces, mat_idx = [], [], []
    h = CUBE / 2
    corners = [(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
               (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    for c in cells:
        x, y, z, region = c
        base = len(verts)
        verts += [(x + 0.5 + dx, y + 0.5 + dy, z + 0.5 + dz) for dx, dy, dz in corners]
        faces += [tuple(base + i for i in q) for q in quads]
        mat_idx += [region] * 6
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    for k in range(max(1, n_regions)):
        me.materials.append(material(shade(rgb, k, n_regions)))
    me.polygons.foreach_set("material_index", mat_idx)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = origin
    ob.scale = (scale, scale, scale)
    ob.parent = parent
    collection().objects.link(ob)
    return ob


def text(body, loc, size=0.6, rgb=TEXT_COLOR, rot=(0, 0, 0), align="LEFT", parent=None):
    cu = bpy.data.curves.new("cv_text", type="FONT")
    cu.body = body
    cu.size = size
    cu.align_x = align
    ob = bpy.data.objects.new("cv_text", cu)
    ob.location = loc
    ob.rotation_euler = rot
    ob.data.materials.append(material(rgb))
    ob.parent = parent
    collection().objects.link(ob)
    return ob


# ------------------------------------------------------------------ build

def cell3(c, dims):
    xyz = list(c[:dims]) + [0] * (3 - dims)
    return xyz[:3] + [c[dims] if len(c) > dims else 0]


def source_block(tree, line):
    """The top-level statement (usually a def) that contains `line`."""
    for node in tree.body:
        if node.lineno <= line <= (node.end_lineno or node.lineno):
            return node.lineno, node.end_lineno or node.lineno
    return line, line


def build(scene, src, expl=None):
    clear()
    names = {e["id"]: e for e in (expl or {}).get("structures", [])}
    structs = scene["structures"]
    src_lines = src.splitlines()
    tree = ast.parse(src)
    x0, y0, row_depth = 0.0, 0.0, 0.0
    bounds = [math.inf, math.inf, -math.inf, -math.inf, 0.0]

    for idx, st in enumerate(structs):
        dims = len(st["axes"])
        cells = [cell3(c, dims) for c in st["cells"]]
        ext = [max((c[k] for c in cells), default=0) + 1 for k in range(3)] if cells else [1, 1, 1]
        sc = min(1.0, MAX_EXTENT / max(ext))
        ext = [e * sc for e in ext]
        w, d = max(ext[0], 6), ext[1]
        if x0 > 0 and x0 + w > ROW_WIDTH:
            x0, y0, row_depth = 0.0, y0 - row_depth - GAP - 4, 0.0
        oy = y0 - d                                    # rows grow downward (towards camera)
        rgb = tuple(st["color"])

        # one empty per structure: clicking any child selects into this group
        root = bpy.data.objects.new(f"cv_struct_{idx}", None)
        root.empty_display_size = 0.01
        root.location = (x0, oy, 0)
        a0, a1 = source_block(tree, st["line"])
        root["cv_index"] = idx
        root["cv_title"] = st["title"]
        root["cv_line"] = st["line"]
        root["cv_law"] = st["law"] or (str(st["count"]) if st["count"] is not None else "")
        root["cv_pieces"] = ", ".join(st["regions"]) if len(st["regions"]) > 1 else ""
        root["cv_shape"] = ", ".join(st["same_shape_as"])
        root["cv_same_law"] = ", ".join(st["same_law_as"])
        root["cv_axes"] = " x ".join(st["axes"])
        e = names.get(st["id"])
        root["cv_name"] = f"{e['shape_name']} · {e['concept']}" if e else ""
        root["cv_expl"] = e["explanation"] if e else ""
        root["cv_mismatch"] = bool(e) and not e["matches_code"]
        root["cv_src"] = "\n".join(f"{n:>3} {src_lines[n - 1]}" for n in range(a0, a1 + 1))
        collection().objects.link(root)

        if cells:
            cubes_mesh(f"cv_{st['title']}", cells, (0, 0, 0), rgb, len(st["regions"]), root, sc)
        axes = st["axes"]
        if dims >= 1:
            text(axes[0], (ext[0] / 2, -0.9, 0), 0.45, DIM_TEXT, align="CENTER", parent=root)
        if dims >= 2:
            text(axes[1], (-0.5, ext[1] / 2, 0), 0.45, DIM_TEXT, rot=(0, 0, math.pi / 2), align="CENTER", parent=root)
        if dims >= 3:
            text(axes[2], (-0.5, ext[1], ext[2] / 2), 0.45, DIM_TEXT, rot=(math.pi / 2, 0, math.pi / 2), align="CENTER", parent=root)

        # caption under the shape: title, count law, discovered relations
        lines = [f"L{st['line']}  {st['title']}"]
        if st["law"]:
            lines.append(f"count = {st['law']}")
        elif st["count"] is not None:
            lines.append(f"count = {st['count']}")
        if sc < 1.0:
            lines.append(f"(shown at 1:{1 / sc:.1f})")
        cap_y = -2.0
        text(lines[0], (0, cap_y, 0), 0.6, parent=root)
        for k, ln in enumerate(lines[1:]):
            text(ln, (0, cap_y - 0.85 * (k + 1), 0), 0.5, DIM_TEXT, parent=root)
        k = len(lines)
        if st["same_shape_as"]:
            text("= same shape as " + ", ".join(st["same_shape_as"])[:60], (0, cap_y - 0.85 * k, 0), 0.5, rgb, parent=root)
            k += 1
        if st["same_law_as"]:
            text("= same count as " + ", ".join(st["same_law_as"])[:60], (0, cap_y - 0.85 * k, 0), 0.5, (1.0, 0.85, 0.35), parent=root)
            k += 1
        if e:                                    # named by Claude after the shape existed
            mark = "  (!)" if not e["matches_code"] else ""
            text(f"Claude: {e['shape_name']}{mark}"[:60], (0, cap_y - 0.85 * k, 0), 0.5, CLAUDE_COLOR, parent=root)
            k += 1

        caption_w = max(len(t) for t in lines) * 0.36
        bounds[0] = min(bounds[0], x0)
        bounds[1] = min(bounds[1], oy + cap_y - 0.85 * k)
        bounds[2] = max(bounds[2], x0 + max(w, caption_w))
        bounds[3] = max(bounds[3], oy + d)
        bounds[4] = max(bounds[4], ext[2])
        row_depth = max(row_depth, d + 0.85 * k + 2)
        x0 += max(w, caption_w) + GAP

    head = f"{os.path.basename(SRC)}   ·   inputs: " + ", ".join(f"{k}={v}" for k, v in scene["inputs"].items())
    text(head, (0, 6.0, 0), 0.8)
    if scene["errors"]:
        text("! " + " | ".join(scene["errors"])[:160], (0, 4.8, 0), 0.45, (1.0, 0.4, 0.35))
    bounds[3] = max(bounds[3], 7.0)
    return bounds


def status(msg):
    text("! " + msg[:160], (0, 4.8, 0), 0.45, (1.0, 0.4, 0.35))


# ------------------------------------------------------------------ live loop

state = {"src": None, "an": None, "ex": None, "framed": False, "bounds": None,
         "relations": [], "stale": False, "asking": None, "ask_error": ""}


def rebuild():
    global analyzer
    try:
        with open(SRC, encoding="utf-8") as f:
            src = f.read()
        scene = analyzer.build_scene(src)
        expl = None
        try:
            with open(EXPLAIN_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("key") == explain.fingerprint(explain.describe(scene, src), "zh"):
                expl = cached["result"]
        except (OSError, ValueError):
            pass
        state["relations"] = (expl or {}).get("relations", [])
        state["stale"] = expl is None and os.path.exists(EXPLAIN_CACHE)
    except SyntaxError as e:          # mid-typing: keep the last good scene
        status(f"syntax error line {e.lineno}: {e.msg}  (showing last good version)")
        return
    except Exception as e:
        status(f"{type(e).__name__}: {e}")
        return
    state["bounds"] = build(scene, src, expl)


def tick():
    try:
        m_src, m_an = os.path.getmtime(SRC), os.path.getmtime(ANALYZER)
    except OSError:
        return 1.0
    m_ex = os.path.getmtime(EXPLAIN_CACHE) if os.path.exists(EXPLAIN_CACHE) else None
    if m_an != state["an"] and state["an"] is not None:
        importlib.reload(analyzer)
        importlib.reload(explain)
    job = state["asking"]
    if job is not None and job.poll() is not None:
        state["asking"] = None
        if job.returncode != 0:
            err = job.stderr.read().decode("utf-8", "replace").strip().splitlines()
            state["ask_error"] = err[-1] if err else f"exit code {job.returncode}"
    if (m_src, m_an, m_ex) != (state["src"], state["an"], state["ex"]):
        state["src"], state["an"], state["ex"] = m_src, m_an, m_ex
        rebuild()
        if not state["framed"]:
            state["framed"] = True
            try:
                frame_viewports()
            except Exception as e:      # cosmetic only; never stop the live loop
                print("[codeviz] could not frame view:", e)
    return 0.5


def frame_viewports():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.shading.type = "SOLID"
            space.shading.color_type = "MATERIAL"
            space.overlay.show_floor = False
            space.overlay.show_axis_x = space.overlay.show_axis_y = False
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            space.region_3d.view_rotation = VIEW_DIR.to_track_quat("-Z", "Y")
            with bpy.context.temp_override(window=window, area=area, region=region):
                for ob in bpy.context.scene.objects:
                    ob.select_set(ob.name in collection().objects)
                bpy.ops.view3d.view_selected()
                bpy.ops.object.select_all(action="DESELECT")


# ------------------------------------------------------------------ sidebar UI

def struct_root(ob):
    while ob is not None and "cv_index" not in ob:
        ob = ob.parent
    return ob


def focus(index):
    root = bpy.data.objects.get(f"cv_struct_{index}")
    if root is None:
        return None
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    for ob in [root, *root.children_recursive]:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = root
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                area.spaces.active.region_3d.view_rotation = VIEW_DIR.to_track_quat("-Z", "Y")
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_selected()
    for ob in [root, *root.children_recursive]:      # framing needs a selection; reading does not
        ob.select_set(False)
    return root


class CV_OT_focus(bpy.types.Operator):
    bl_idname = "codeviz.focus"
    bl_label = "Focus"
    bl_description = "Zoom to this structure"
    index: bpy.props.IntProperty()

    def execute(self, context):
        focus(self.index)
        return {"FINISHED"}


class CV_OT_overview(bpy.types.Operator):
    bl_idname = "codeviz.overview"
    bl_label = "Show all"

    def execute(self, context):
        frame_viewports()
        return {"FINISHED"}


class CV_OT_open(bpy.types.Operator):
    bl_idname = "codeviz.open_editor"
    bl_label = "Open in editor"
    bl_description = "Open the source file at this structure's line"
    line: bpy.props.IntProperty()

    def execute(self, context):
        code = shutil.which("code")
        if code:
            subprocess.Popen([code, "-g", f"{SRC}:{self.line}"], shell=os.name == "nt")
        elif os.name == "nt":
            os.startfile(SRC)
        else:
            subprocess.Popen(["xdg-open", SRC])
        return {"FINISHED"}


def system_python():
    """Blender's bundled Python has no `anthropic`; use the user's own interpreter."""
    return os.environ.get("CODEVIZ_PYTHON") or shutil.which("python") or shutil.which("py")


class CV_OT_ask(bpy.types.Operator):
    bl_idname = "codeviz.ask_claude"
    bl_label = "Ask Claude to explain"
    bl_description = "Name and explain every structure (runs in the background; result is cached)"

    def execute(self, context):
        py = system_python()
        if py is None:
            self.report({"ERROR"}, "No system Python found; set CODEVIZ_PYTHON")
            return {"CANCELLED"}
        state["ask_error"] = ""
        state["asking"] = subprocess.Popen([py, os.path.join(HERE, "explain.py"), SRC],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return {"FINISHED"}


class CV_PT_panel(bpy.types.Panel):
    bl_label = "CodeViz"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Item"            # the tab N opens by default; scripts cannot switch tabs
    bl_order = -100                 # above Transform

    def draw(self, context):
        lay = self.layout
        lay.label(text=os.path.basename(SRC), icon="FILE_SCRIPT")
        row = lay.row(align=True)
        row.operator("codeviz.overview", icon="ZOOM_ALL")
        if state["asking"] is not None:
            row.label(text="Claude is thinking...", icon="SORTTIME")
        else:
            row.operator("codeviz.ask_claude", icon="LIGHT")
        if state["ask_error"]:
            lay.label(text=state["ask_error"][:90], icon="ERROR")
        elif state["stale"]:
            lay.label(text="Code changed since the last explanation", icon="INFO")
        root = struct_root(context.active_object)
        if root is None:
            lay.label(text="Click a structure to inspect it")
        else:
            box = lay.box()
            box.label(text=root["cv_title"], icon="MESH_GRID")
            if root["cv_axes"]:
                box.label(text="directions: " + root["cv_axes"])
            if root["cv_law"]:
                box.label(text="count = " + root["cv_law"])
            if root["cv_pieces"]:
                box.label(text="pieces: " + root["cv_pieces"])
            if root["cv_shape"]:
                box.label(text="same shape as: " + root["cv_shape"], icon="LINKED")
            if root["cv_same_law"]:
                box.label(text="same count as: " + root["cv_same_law"], icon="LINKED")
            if root["cv_name"]:
                cbox = box.box()
                cbox.label(text="Claude: " + root["cv_name"], icon="LIGHT")
                if root["cv_mismatch"]:
                    cbox.label(text="The code's names disagree with the geometry", icon="ERROR")
                for ln in wrap(root["cv_expl"], 30):
                    cbox.label(text=ln)
                for rel in state["relations"]:
                    if root["cv_title"] in rel["members"]:
                        rbox = box.box()
                        rbox.label(text=" = ".join(rel["members"])[:60], icon="LINKED")
                        for ln in wrap(rel["insight"], 30):
                            rbox.label(text=ln)
            row = box.row()
            row.operator("codeviz.focus", icon="VIEWZOOM").index = root["cv_index"]
            row.operator("codeviz.open_editor", icon="TEXT").line = root["cv_line"]
            code = lay.box()
            code.scale_y = 0.75
            for ln in root["cv_src"].split("\n")[:30]:
                r = code.row()
                r.alert = ln[:3].strip() == str(root["cv_line"])     # highlight the structure's own line
                r.label(text=ln.replace("\t", "    "))
        lay.separator()
        col = lay.column(align=True)
        for ob in sorted((o for o in collection().objects if "cv_index" in o), key=lambda o: o["cv_index"]):
            col.operator("codeviz.focus", text=ob["cv_title"], depress=root is ob).index = ob["cv_index"]


def wrap(text_, width):
    """Blender labels do not wrap; CJK characters count double."""
    lines, cur, w = [], "", 0
    for ch in text_ or "":
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > width or ch == "\n":
            lines.append(cur)
            cur, w = "", 0
            if ch == "\n":
                continue
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines


UI_CLASSES = (CV_OT_focus, CV_OT_overview, CV_OT_open, CV_OT_ask, CV_PT_panel)


def register_ui():
    for cls in UI_CLASSES:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
        bpy.utils.register_class(cls)


# ------------------------------------------------------------------ self-test (GUI screenshot)

def selftest():
    """CODEVIZ_SELFTEST=out.png [CODEVIZ_FOCUS=k]: focus a structure, open the
    sidebar, screenshot the window, quit. Lets the GUI path be checked unattended."""
    out = os.environ["CODEVIZ_SELFTEST"]
    k = os.environ.get("CODEVIZ_FOCUS")
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
    if k is not None:
        focus(int(k))
    else:
        frame_viewports()

    def shoot():
        win = bpy.context.window_manager.windows[0]
        with bpy.context.temp_override(window=win):
            bpy.ops.screen.screenshot(filepath=os.path.abspath(out))
            bpy.ops.wm.quit_blender()
        return None
    bpy.app.timers.register(shoot, first_interval=1.5)
    return None


# ------------------------------------------------------------------ snapshot

def snapshot(path, bounds):
    sc = bpy.context.scene
    minx, miny, maxx, maxy, maxz = bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    w, d = maxx - minx, maxy - miny
    cam_data = bpy.data.cameras.new("cv_cam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("cv_cam", cam_data)
    sc.collection.objects.link(cam)
    direction = VIEW_DIR
    cam.location = Vector((cx, cy, maxz / 2)) - direction * 200
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    sc.render.resolution_x, sc.render.resolution_y = 2000, 1250
    aspect = sc.render.resolution_x / sc.render.resolution_y
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    pts = [inv @ (ob.matrix_world @ Vector(c)) for ob in collection().objects for c in ob.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts)))
    mid = (lo + hi) / 2
    cam.location = cam.matrix_world @ Vector((mid.x, mid.y, 0))
    cam_data.ortho_scale = max(hi.x - lo.x, (hi.y - lo.y) * aspect) * 1.06
    sc.camera = cam
    sc.render.engine = "BLENDER_WORKBENCH"
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL"
    sh.show_shadows = True
    sh.show_cavity = True
    sh.background_type = "VIEWPORT"
    sh.background_color = (0.07, 0.075, 0.09)
    if sc.world is None:
        sc.world = bpy.data.worlds.new("cv_world")
    sc.world.color = (0.07, 0.075, 0.09)
    sc.render.filepath = os.path.abspath(path)
    bpy.ops.render.render(write_still=True)


# ------------------------------------------------------------------ entry

for name in ("Cube", "Light", "Camera"):          # factory startup objects
    ob = bpy.data.objects.get(name)
    if ob is not None:
        bpy.data.objects.remove(ob, do_unlink=True)

if bpy.app.background:
    rebuild()
    if RENDER and state["bounds"]:
        snapshot(RENDER, state["bounds"])
else:
    register_ui()
    bpy.app.timers.register(tick, first_interval=0.2, persistent=True)
    if os.environ.get("CODEVIZ_SELFTEST"):
        bpy.app.timers.register(selftest, first_interval=2.0)
    print(f"[codeviz] watching {SRC} - edit and save to update")
