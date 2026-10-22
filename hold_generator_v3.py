"""
Climbing Hold Generator  -  procedural 3D climbing holds -> STL   (v3)
---------------------------------------------------------------------
Windows / macOS / Linux, Python 3.8+, standard library only (Tkinter).

Inputs : seed (any text or number), difficulty (Easy/Medium/Hard),
         hold type (Jug, Crimp, Sloper, Pinch, Pocket, Edge)
Output : watertight binary STL in millimetres, solid hold, flat back face
         on Z = 0 (print with the back face on the bed).

How the geometry works
  X = across the wall, Y = up the wall, Z = standoff from the wall.
  Each hold is a *loft* of closed side-profiles (Y-Z plane) swept along X.
  Because a profile is a closed curve (not a height map) it can overhang,
  so real features are possible: incut jug lips, undercut pocket roofs,
  hooked crimp ledges, etc.

Dimensions are based on published specifications of commercial holds
(e.g. Atomik Climbing Holds catalogue sizes) and hangboard edge depths
(6-20 mm edges, ~5 mm edge radius).  See SPECS below.

The same seed + type + difficulty always gives the same hold.
"""
import math
import os
import random
import struct
import zlib

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
except ImportError:          # allows importing the generator headless
    tk = None

NU = 56      # columns along the width (X)
NF = 64      # samples along the front curve of each profile
NB = 6       # samples along the flat back of each profile

HOLD_TYPES = ["Jug", "Crimp", "Sloper", "Pinch", "Pocket", "Edge"]
HOLD_INFO = {
    "Jug":    "Big incut bucket you can wrap your hand into",
    "Crimp":  "Small flat ledge, 6-20 mm deep, fingertips only",
    "Sloper": "Smooth rounded cobble with no positive edge",
    "Pinch":  "Parallel-sided loaf squeezed between thumb and fingers",
    "Pocket": "Rounded block with a 1-, 2- or 3-finger hole",
    "Edge":   "Deeper ledge (20-40 mm) that fits four fingers",
}
DIFFICULTY_LIST = ["Easy", "Medium", "Hard"]
PALETTE = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (241, 196, 15),
           (155, 89, 182), (230, 126, 34), (26, 188, 156)]

# ----------------------------------------------------------------------------
# Real-world derived specifications (all mm / degrees).
#   W = width across wall, H = height up the wall, D = standoff from wall
#   "Hard" = smaller, shallower, less incut, rounder  (harder to hold)
# ----------------------------------------------------------------------------
SPECS = {
    # tl = lip thickness, bd = bowl depth, lean = lip overhang angle
    "Jug": {
        "Easy":   dict(W=150, H=125, D=78, tl=36, bd=48, lean=35, p=2.5),
        "Medium": dict(W=128, H=110, D=66, tl=30, bd=40, lean=28, p=2.5),
        "Hard":   dict(W=102, H=92,  D=50, tl=24, bd=30, lean=18, p=2.5)},
    # D = edge (ledge) depth, inc = ledge tilt (+ = lip higher / incut), r = lip radius
    "Crimp": {
        "Easy":   dict(W=105, H=38, D=20, inc=6,  r=6.0, p=3.8),
        "Medium": dict(W=88,  H=32, D=13, inc=2,  r=4.5, p=3.8),
        "Hard":   dict(W=70,  H=28, D=7,  inc=-3, r=3.5, p=3.8)},
    "Edge": {
        "Easy":   dict(W=125, H=62, D=40, inc=12, r=8.0, p=3.4),
        "Medium": dict(W=110, H=55, D=30, inc=5,  r=7.0, p=3.4),
        "Hard":   dict(W=95,  H=48, D=22, inc=-5, r=9.0, p=3.4)},
    # nt / nb = superellipse exponent of the top / bottom half
    # (higher nt = flatter top = more positive).  p = footprint exponent
    "Sloper": {
        "Easy":   dict(W=150, H=125, D=85, nt=3.4, nb=2.4, p=2.2),
        "Medium": dict(W=135, H=110, D=78, nt=2.7, nb=2.3, p=2.2),
        "Hard":   dict(W=120, H=100, D=70, nt=2.1, nb=2.1, p=2.2)},
    # W = pinch gap (typ. 64-89 mm), H = length up the wall, p = side steepness
    "Pinch": {
        "Easy":   dict(W=62, H=125, D=48, p=8.0),
        "Medium": dict(W=78, H=145, D=55, p=5.5),
        "Hard":   dict(W=90, H=175, D=65, p=3.2)},
    # d = pocket depth, hy = opening height, a = incut angle of pocket axis
    "Pocket": {
        "Easy":   dict(W=135, H=110, D=58, d=44, hy=30, a=45, fingers=(3,),   p=2.6),
        "Medium": dict(W=125, H=105, D=44, d=29, hy=26, a=35, fingers=(2, 3), p=2.6),
        "Hard":   dict(W=110, H=95,  D=30, d=17, hy=22, a=20, fingers=(1, 2), p=2.6)},
}
FINGER_W = {1: 24.0, 2: 44.0, 3: 64.0}      # pocket opening width (assumed)
ROUGH = {"Easy": 0.5, "Medium": 0.9, "Hard": 1.4}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def clamp(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v


def seed_to_int(text):
    text = str(text).strip()
    try:
        return int(text)
    except ValueError:
        return zlib.crc32(text.encode("utf-8"))


def round_poly(pts, seg=5):
    """pts = [(y, z, radius)]; returns a dense polyline with rounded corners."""
    out = [(pts[0][0], pts[0][1])]
    for i in range(1, len(pts) - 1):
        (y0, z0, _), (y1, z1, r), (y2, z2, _) = pts[i - 1], pts[i], pts[i + 1]
        v1 = (y0 - y1, z0 - z1)
        v2 = (y2 - y1, z2 - z1)
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        if r <= 1e-6 or l1 < 1e-9 or l2 < 1e-9:
            out.append((y1, z1))
            continue
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        ang = math.acos(clamp(u1[0] * u2[0] + u1[1] * u2[1], -1.0, 1.0))
        if ang < 1e-3 or ang > math.pi - 1e-3:
            out.append((y1, z1))
            continue
        t = r / math.tan(ang / 2)
        tmax = 0.5 * min(l1, l2)
        if t > tmax:
            t = tmax
            r = t * math.tan(ang / 2)
        bx, bz = u1[0] + u2[0], u1[1] + u2[1]
        bl = math.hypot(bx, bz)
        bx, bz = bx / bl, bz / bl
        dist = r / math.sin(ang / 2)
        cy, cz = y1 + bx * dist, z1 + bz * dist
        a1 = math.atan2(y1 + u1[0] * t - cy, z1 + u1[1] * t - cz)
        a2 = math.atan2(y1 + u2[0] * t - cy, z1 + u2[1] * t - cz)
        d = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        n = max(2, int(math.ceil(abs(d) / (math.pi / 2) * seg)))
        for k in range(n + 1):
            a = a1 + d * k / n
            out.append((cy + r * math.sin(a), cz + r * math.cos(a)))
    out.append((pts[-1][0], pts[-1][1]))
    return out


def resample(poly, n):
    """n points equally spaced by arc length along a polyline."""
    d = [0.0]
    for a, b in zip(poly, poly[1:]):
        d.append(d[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = d[-1]
    out, k = [], 0
    for i in range(n):
        s = total * i / (n - 1)
        while k < len(d) - 2 and d[k + 1] < s:
            k += 1
        seg = d[k + 1] - d[k]
        t = 0.0 if seg < 1e-12 else (s - d[k]) / seg
        a, b = poly[k], poly[k + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


# ----------------------------------------------------------------------------
# Profile builders:  (u, local standoff D) -> list of (y, z, radius) from the
# bottom wall corner, over the front, to the top wall corner.
# ----------------------------------------------------------------------------
def jug_profile(S):
    H, tl, bd = S["H"], S["tl"], S["bd"]
    lean = math.radians(S["lean"])

    def f(D):
        yA = -H / 2
        yc = yA + 0.80 * H                       # crest of the lip
        yT = yA + 0.92 * H                       # rear wall stops just above the lip
        zin = D - tl                             # inner face of the lip at the crest
        zb = max(10.0, zin - bd * math.tan(lean))
        return [(yA, 0, 0),
                (yA + 0.10 * H, 0.92 * D, 0.45 * D),
                (yc, D, 0.45 * tl),              # lip front/top
                (yc, zin, 0.45 * tl),            # lip top/inner
                (yc - bd, zb, 0.50 * bd),        # bowl bottom
                (yT - 0.05 * H, max(zb + 4.0, 16.0), 6.0),   # back rim
                (yT, 0, 0)]
    return f


def ledge_profile(S):
    """Crimp and Edge: sloped belly, rounded lip, flat (tilted) ledge top."""
    H, inc, rl = S["H"], math.radians(S["inc"]), S["r"]

    def f(D):
        yA, yT = -H / 2, H / 2
        yE = yT + D * math.tan(inc)              # lip height (higher = incut)
        return [(yA, 0, 0),
                (yA + 0.30 * (yE - yA), 0.80 * D, 0.30 * H),
                (yE, D, min(rl, 0.45 * D)),
                (yT, 0, 0)]
    return f


def sloper_profile(S):
    b = S["H"] / 2
    nt, nb = S["nt"], S["nb"]

    def f(D):
        pts = []
        N = 160
        for t in range(N + 1):
            th = math.pi * (1 - t / N)
            c, s = math.cos(th), math.sin(th)
            n = nt if c > 0 else nb
            y = b * math.copysign(abs(c) ** (2 / n), c)
            z = D * abs(s) ** (2 / n)
            pts.append((y, z, 0))
        pts[0] = (-b, 0.0, 0)
        pts[-1] = (b, 0.0, 0)
        return pts
    return f


def pinch_profile(S, crown):
    L = S["H"]

    def f(D):
        yA, yT = -L / 2, L / 2
        return [(yA, 0, 0),
                (yA + 0.03 * L, 0.90 * D, 0.50 * D),
                (crown * L, D, 0),
                (yT - 0.03 * L, 0.90 * D, 0.50 * D),
                (yT, 0, 0)]
    return f


def pocket_profile(S, pocket):
    H = S["H"]
    d, hy, a = pocket["d"], pocket["hy"], math.radians(pocket["a"])
    wx, xc = pocket["wx"], pocket["xc"]
    yT, yA = H / 2, -H / 2
    dyin = min(d * math.tan(a), 0.9 * ((yT - 12) - (yA + 12 + hy)))
    yp = clamp(pocket["yp"] * H, yA + 12 + hy / 2, yT - 12 - hy / 2 - dyin)

    def f(D, x=0.0, scale=1.0):
        pts = [(yA, 0, 0), (yA + 0.03 * H, D, 0.5 * D)]
        t = (x - xc) / (wx * scale / 2.0)
        sd = max(0.0, 1.0 - t * t) ** 0.5 if abs(t) < 1 else 0.0
        if sd > 0.06:
            de = min(d * scale, D - 9.0 * scale) * sd
            dy = min(de * math.tan(a), dyin * scale)
            h2 = hy * scale / 2.0 * min(1.0, 0.35 + sd)
            y0, y1 = yp * scale - h2, yp * scale + h2
            pts += [(y0, D, 4.0 * sd * scale),
                    (y0 + dy, D - de, 0.45 * hy * scale * sd),
                    (y1 + dy, D - de, 0.45 * hy * scale * sd),
                    (y1, D, 4.0 * sd * scale)]
        pts += [(yT - 0.03 * H, D, 0.5 * D), (yT, 0, 0)]
        return pts
    return f


# ----------------------------------------------------------------------------
# Hold generation
# ----------------------------------------------------------------------------
def generate_hold(seed, htype, difficulty):
    """Return (vertices, triangles, info). Triangles have outward normals."""
    rng = random.Random(seed_to_int(seed))
    S = dict(SPECS[htype][difficulty])
    for key in ("W", "H", "D", "tl", "bd", "d", "hy", "r"):
        if key in S:
            S[key] *= rng.uniform(0.93, 1.07)
    for key in ("lean", "inc", "a"):
        if key in S:
            S[key] += rng.uniform(-3.0, 3.0)
    p = S["p"]
    ax = S["W"] / 2.0
    Dn = S["D"]

    # organic variation ------------------------------------------------------
    ph = [rng.uniform(0, 2 * math.pi) for _ in range(4)]
    dz_amp = rng.uniform(0.04, 0.10)
    s1, s2 = rng.uniform(-1, 1) * 0.10, rng.uniform(-1, 1) * 0.06

    def dzf(u):
        return 1.0 + dz_amp * math.sin(1.7 * u + ph[0])

    def shear(u):
        return 0.5 * S["H"] * (s1 * u + s2 * (u * u - 0.4))

    amp = ROUGH[difficulty] * min(1.0, Dn / 25.0)
    bumps = []
    for _ in range(5):
        ang = rng.uniform(0, 2 * math.pi)
        f = rng.uniform(0.04, 0.12)
        bumps.append((f * math.cos(ang), f * math.sin(ang),
                      rng.uniform(0, 2 * math.pi), amp * rng.uniform(0.3, 1.0)))

    notes = ""
    pocket = None
    if htype == "Jug":
        prof = jug_profile(S)
        notes = "Lip %.0f mm, bowl %.0f mm deep, lean %.0f deg" % (S["tl"], S["bd"], S["lean"])
    elif htype in ("Crimp", "Edge"):
        prof = ledge_profile(S)
        notes = "Ledge depth %.0f mm, lip radius %.1f mm, tilt %+.0f deg" % (S["D"], S["r"], S["inc"])
    elif htype == "Sloper":
        prof = sloper_profile(S)
        zz = 0.45
        nt = S["nt"]
        b = S["H"] / 2.0
        slope = math.degrees(math.atan(b / S["D"] * zz ** (nt - 1)
                                       * (1 - zz ** nt) ** (1.0 / nt - 1)))
        notes = "Slope at grip area approx %.0f deg" % slope
    elif htype == "Pinch":
        prof = pinch_profile(S, rng.uniform(-0.06, 0.06))
        notes = "Pinch gap %.0f mm (typical 64-89), length %.0f mm" % (S["W"], S["H"])
    else:
        nf = rng.choice(S["fingers"])
        pocket = dict(d=min(S["d"], S["D"] - 9.0), hy=S["hy"], a=S["a"],
                      wx=FINGER_W[nf], xc=rng.uniform(-0.10, 0.10) * S["W"],
                      yp=rng.uniform(-0.15, 0.10))
        prof = pocket_profile(S, pocket)
        names = {1: "Mono (1-finger)", 2: "2-finger", 3: "3-finger"}
        notes = "%s pocket, %.0f mm deep (~%.1f pads), incut %.0f deg" % (
            names[nf], pocket["d"], pocket["d"] / 20.0, S["a"])

    # build the columns -----------------------------------------------------------
    verts = [(-ax, 0.0, 0.0)]
    for i in range(1, NU):
        u = -math.cos(math.pi * i / NU)
        x = ax * u
        k = (1.0 - abs(u) ** p) ** (1.0 / p)
        D = Dn * dzf(u)
        pts = prof(D, x, 1.0) if htype == "Pocket" else prof(D)
        pts = [(k * y, k * z, k * r) for y, z, r in pts]
        poly = resample(round_poly(pts), NF)
        sh = shear(u) * math.sqrt(k)
        col = []
        for j, (y, z) in enumerate(poly):
            if 0 < j < NF - 1:
                w = math.sin(math.pi * j / (NF - 1))
                z += w * k * sum(a * math.sin(fx * x + fy * y + q)
                                 for fx, fy, q, a in bumps)
                z = max(z, 0.3)
            col.append((x, y + sh, z))
        (_, yT, _), (_, yA, _) = col[-1], col[0]
        for m in range(1, NB + 1):
            t = m / (NB + 1.0)
            col.append((x, yT + (yA - yT) * t, 0.0))
        verts.extend(col)
    verts.append((ax, 0.0, 0.0))

    NS = NF + NB
    last = len(verts) - 1

    def V(i, j):
        if i == 0:
            return 0
        if i == NU:
            return last
        return 1 + (i - 1) * NS + (j % NS)

    tris = []
    for i in range(NU):
        for j in range(NS):
            a, b, c, d = V(i, j), V(i + 1, j), V(i + 1, j + 1), V(i, j + 1)
            for t in ((a, b, c), (a, c, d)):
                if len(set(t)) == 3:
                    tris.append(t)

    if signed_volume(verts, tris) < 0:
        tris = [(a, c, b) for a, b, c in tris]

    xs, ys, zs = zip(*verts)
    info = dict(w=max(xs) - min(xs), h=max(ys) - min(ys), t=max(zs),
                tris=len(tris), notes=notes,
                color=PALETTE[seed_to_int(seed) % len(PALETTE)])
    return verts, tris, info


def signed_volume(v, tris):
    s = 0.0
    for a, b, c in tris:
        x0, y0, z0 = v[a]
        x1, y1, z1 = v[b]
        x2, y2, z2 = v[c]
        s += (x0 * (y1 * z2 - z1 * y2) - y0 * (x1 * z2 - z1 * x2)
              + z0 * (x1 * y2 - y1 * x2))
    return s / 6.0


def triangle_normal(v0, v1, v2):
    ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    vx, vy, vz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / ln, ny / ln, nz / ln


def write_stl(path, verts, tris, name="climbing_hold"):
    with open(path, "wb") as f:
        f.write(name.encode("ascii", "replace")[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            va, vb, vc = verts[a], verts[b], verts[c]
            n = triangle_normal(va, vb, vc)
            f.write(struct.pack("<12fH", *n, *va, *vb, *vc, 0))


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Climbing Hold Generator")
        root.geometry("1040x660")
        root.minsize(860, 580)

        self.seed = tk.StringVar(value="1234")
        self.diff = tk.StringVar(value="Medium")
        self.htype = tk.StringVar(value="Jug")
        self.az, self.tilt, self.zoom = 0.6, 0.5, 1.0
        self.mesh = None
        self._last = None

        side = ttk.Frame(root, padding=12)
        side.pack(side="left", fill="y")

        ttk.Label(side, text="Random seed", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(side)
        row.pack(fill="x", pady=(2, 10))
        e = ttk.Entry(row, textvariable=self.seed, width=14)
        e.pack(side="left")
        e.bind("<Return>", lambda _e: self.regen())
        ttk.Button(row, text="Randomise", command=self.randomise).pack(side="left", padx=6)

        ttk.Label(side, text="Difficulty", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for d in DIFFICULTY_LIST:
            ttk.Radiobutton(side, text=d, value=d, variable=self.diff,
                            command=self.regen).pack(anchor="w")

        ttk.Label(side, text="Hold type", font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(10, 0))
        for t in HOLD_TYPES:
            ttk.Radiobutton(side, text=t, value=t, variable=self.htype,
                            command=self.regen).pack(anchor="w")
        self.desc = ttk.Label(side, text="", wraplength=210, foreground="#555")
        self.desc.pack(anchor="w", pady=(4, 8))

        ttk.Label(side, text="View", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        vrow = ttk.Frame(side)
        vrow.pack(fill="x", pady=(2, 8))
        for text, az, tl in (("Front", 0.0, 0.0), ("Profile", math.pi / 2, 0.0),
                             ("Top", 0.0, math.pi / 2), ("3D", 0.6, 0.5)):
            ttk.Button(vrow, text=text, width=7,
                       command=lambda a=az, t=tl: self.set_view(a, t)).pack(side="left")

        ttk.Button(side, text="Export STL...", command=self.export).pack(fill="x", pady=2)
        ttk.Button(side, text="Batch export (many seeds)...",
                   command=self.batch).pack(fill="x", pady=2)

        self.info = ttk.Label(side, text="", justify="left", wraplength=210)
        self.info.pack(anchor="w", pady=10)
        ttk.Label(side, text="Drag = rotate   Wheel = zoom", foreground="#777").pack(
            anchor="w", side="bottom")

        self.canvas = tk.Canvas(root, bg="#1e2228", highlightthickness=0)
        self.canvas.pack(side="right", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._zoom(1.1))
        self.canvas.bind("<Button-5>", lambda _e: self._zoom(0.9))
        self.regen()

    # ---- generation ----------------------------------------------------------------
    def randomise(self):
        self.seed.set(str(random.randint(0, 999999)))
        self.regen()

    def regen(self):
        t, d = self.htype.get(), self.diff.get()
        self.desc.config(text=HOLD_INFO[t])
        self.mesh = generate_hold(self.seed.get() or "0", t, d)
        v, _, inf = self.mesh
        xs, ys, zs = zip(*v)
        self.center = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, max(zs) / 2)
        self.extent = max(inf["w"], inf["h"], inf["t"])
        self.info.config(text="Size: %.0f W x %.0f H x %.0f D mm\n%s\nTriangles: %d"
                         % (inf["w"], inf["h"], inf["t"], inf["notes"], inf["tris"]))
        self.redraw()

    # ---- preview -------------------------------------------------------------------
    def set_view(self, az, tilt):
        self.az, self.tilt = az, tilt
        self.redraw()

    def _press(self, e):
        self._last = (e.x, e.y)

    def _drag(self, e):
        if self._last:
            self.az += (e.x - self._last[0]) * 0.01
            self.tilt = clamp(self.tilt + (e.y - self._last[1]) * 0.01,
                              -math.pi / 2, math.pi / 2)
            self._last = (e.x, e.y)
            self.redraw()

    def _wheel(self, e):
        self._zoom(1.1 if e.delta > 0 else 0.9)

    def _zoom(self, f):
        self.zoom = clamp(self.zoom * f, 0.3, 5.0)
        self.redraw()

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if not self.mesh:
            return
        verts, tris, inf = self.mesh
        w, h = c.winfo_width() or 700, c.winfo_height() or 600
        scale = self.zoom * min(w, h) / (self.extent * 1.5)
        ca, sa = math.cos(self.az), math.sin(self.az)
        cb, sb = math.cos(self.tilt), math.sin(self.tilt)
        cx0, cy0, cz0 = self.center
        pts = []
        for x, y, z in verts:
            x -= cx0
            y -= cy0
            z -= cz0
            x1, z1 = x * ca + z * sa, -x * sa + z * ca
            pts.append((x1, y * cb - z1 * sb, y * sb + z1 * cb))
        L = (-0.35, 0.55, 0.75)
        ln = math.sqrt(sum(v * v for v in L))
        L = tuple(v / ln for v in L)
        br, bg, bb = inf["color"]
        faces = []
        for a, b, d in tris:
            pa, pb, pd = pts[a], pts[b], pts[d]
            nx, ny, nz = triangle_normal(pa, pb, pd)
            if nz <= 0:
                continue
            lit = 0.25 + 0.75 * max(0.0, nx * L[0] + ny * L[1] + nz * L[2])
            faces.append((pa[2] + pb[2] + pd[2], lit, pa, pb, pd))
        faces.sort(key=lambda f: f[0])
        mx, my = w / 2, h / 2
        for _, lit, pa, pb, pd in faces:
            col = "#%02x%02x%02x" % (int(br * lit), int(bg * lit), int(bb * lit))
            c.create_polygon(mx + pa[0] * scale, my - pa[1] * scale,
                             mx + pb[0] * scale, my - pb[1] * scale,
                             mx + pd[0] * scale, my - pd[1] * scale,
                             fill=col, outline=col)

    # ---- export --------------------------------------------------------------------
    def _fname(self, seed):
        return "hold_%s_%s_%s.stl" % (self.htype.get().lower(), self.diff.get().lower(), seed)

    def export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".stl", filetypes=[("STL file", "*.stl")],
            initialfile=self._fname(self.seed.get()))
        if not path:
            return
        v, t, _ = self.mesh
        write_stl(path, v, t)
        messagebox.showinfo("Exported", "Saved:\n" + path)

    def batch(self):
        n = simpledialog.askinteger("Batch export", "How many designs?",
                                    initialvalue=100, minvalue=1, maxvalue=5000)
        if not n:
            return
        folder = filedialog.askdirectory(title="Choose output folder")
        if not folder:
            return
        start = seed_to_int(self.seed.get())
        for i in range(n):
            s = start + i
            v, t, _ = generate_hold(s, self.htype.get(), self.diff.get())
            write_stl(os.path.join(folder, self._fname(s)), v, t)
            if i % 5 == 0:
                self.info.config(text="Batch: %d / %d" % (i + 1, n))
                self.root.update()
        self.regen()
        messagebox.showinfo("Batch complete",
                            "%d STL files written to:\n%s" % (n, folder))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
