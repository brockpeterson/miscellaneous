#!/usr/bin/env python3
"""
vcf_logs_harvester_gui.py - Dark-mode desktop window for vcf_logs_harvester.py

Keep this file in the same folder as vcf_logs_harvester.py; it uses that
script's search code, so both behave identically.

Run:   python vcf_logs_harvester_gui.py      (Windows: py vcf_logs_harvester_gui.py)
Needs: Python 3.8+ with Tkinter. No extra packages (tzdata on Windows only if
       you type a named time zone such as America/Chicago).

Your server, username and search settings are remembered between runs in
~/.vcf_logs_harvester_gui.json. The password is never saved.
"""

import json
import math
import os
import queue
import random
import re
import sys
import threading
import time

# macOS's built-in Tk prints a generic deprecation notice; we print a clearer one
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

try:
    import vcf_logs_harvester as core
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import vcf_logs_harvester as core
    except ImportError:
        sys.exit("vcf_logs_harvester.py must be in the same folder as this file.")

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".vcf_logs_harvester_gui.json")
DISPLAY_LIMIT = 5000          # events shown in the window; Save writes all of them
UNITS = {"minutes": "m", "hours": "h", "days": "d", "weeks": "w"}

# ---------------------------------------------------------------- palette --
BG       = "#070a12"   # window
SURFACE  = "#0e1422"   # cards
SURFACE2 = "#151d30"   # inputs
BORDER   = "#243049"
TEXT     = "#e6ebf5"
MUTED    = "#7d89a3"
DIM      = "#4a5570"
VIOLET   = "#8b5cf6"
VIOLET_H = "#a78bfa"
CYAN     = "#22d3ee"
GREEN    = "#34d399"
AMBER    = "#fbbf24"
PINK     = "#f472b6"
RED      = "#f87171"
BAR      = "#0ea5c6"   # timeline bars (validated for the dark panel background)
MENU_BG  = "#1a123f"   # banner colour at the far right, behind the ⋮ button

IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d])")
STAMP = re.compile(r"^(?:[A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d|\d{4}-\d\d-\d\d[T ][\d:.]+Z?)")


# ------------------------------------------------------------------- logo --
LOGO_MAX_H, LOGO_MAX_W = 56, 240        # banner logo box, in pixels at 100% scaling
LOGO_CACHE = os.path.join(os.path.expanduser("~"), ".vcf_logs_harvester_cache")
LOGO_AUTO = ("banner_logo.png", "banner_logo.jpg", "banner_logo.jpeg", "banner_logo.gif")
LOGO_TYPES = (".png", ".jpg", ".jpeg", ".gif")

_PS_RESIZE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile($env:LOGO_IN)
try {
  $r = [Math]::Min(1.0, [Math]::Min([double]$env:LOGO_W / $src.Width, [double]$env:LOGO_H / $src.Height))
  $w = [Math]::Max(1, [int][Math]::Round($src.Width * $r))
  $h = [Math]::Max(1, [int][Math]::Round($src.Height * $r))
  $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $g.DrawImage($src, 0, 0, $w, $h)
  $g.Dispose()
  $bmp.Save($env:LOGO_OUT, [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
} finally { $src.Dispose() }
"""


def prepare_logo(src, max_w, max_h):
    """Return a PNG/GIF path Tk can show, resized to fit max_w x max_h.
    Uses Pillow if installed, else macOS 'sips' or Windows' built-in .NET
    imaging, so JPG works and edges stay smooth without installing anything."""
    import hashlib
    import subprocess
    src = os.path.abspath(os.path.expanduser(src))
    if not os.path.isfile(src):
        raise ValueError(f"Logo file not found:\n{src}")
    ext = os.path.splitext(src)[1].lower()
    if ext not in LOGO_TYPES:
        raise ValueError("Use a PNG, JPG or GIF image for the logo.")
    st = os.stat(src)
    key = f"{src}|{st.st_mtime}|{st.st_size}|{max_w}x{max_h}".encode()
    os.makedirs(LOGO_CACHE, exist_ok=True)
    out = os.path.join(LOGO_CACHE, f"logo-{hashlib.sha1(key).hexdigest()[:16]}.png")
    if os.path.isfile(out):
        return out

    try:                                    # 1. Pillow, if available
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGBA")
            im.thumbnail((max_w, max_h), Image.LANCZOS)
            im.save(out, "PNG")
        return out
    except ImportError:
        pass

    try:
        if sys.platform == "darwin":         # 2. macOS: sips is built in
            info = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", src],
                                  capture_output=True, text=True, check=True).stdout
            w = int(re.search(r"pixelWidth:\s*(\d+)", info).group(1))
            h = int(re.search(r"pixelHeight:\s*(\d+)", info).group(1))
            r = min(1.0, max_w / w, max_h / h)
            size = ["-z", str(max(1, round(h * r))), str(max(1, round(w * r)))] if r < 1 else []
            subprocess.run(["sips", "-s", "format", "png", *size, src, "--out", out],
                           capture_output=True, check=True)
            return out
        if sys.platform == "win32":          # 3. Windows: .NET System.Drawing
            env = dict(os.environ, LOGO_IN=src, LOGO_OUT=out,
                       LOGO_W=str(max_w), LOGO_H=str(max_h))
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command", _PS_RESIZE],
                           env=env, capture_output=True, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return out
    except Exception:
        pass                                 # fall through to Tk's own loader

    if ext in (".png", ".gif"):              # 4. Tk reads PNG/GIF itself
        return src
    raise ValueError("This computer can't convert JPG logos on its own.\n"
                     "Save the logo as PNG, or install Pillow (pip install pillow).")


def load_logo(src, scale=1.0):
    """PhotoImage of the logo sized for the banner (kept by the caller)."""
    max_w, max_h = round(LOGO_MAX_W * scale), round(LOGO_MAX_H * scale)
    path = prepare_logo(src, max_w, max_h)
    img = tk.PhotoImage(file=path)
    f = max(math.ceil(img.width() / max_w), math.ceil(img.height() / max_h))
    return img.subsample(f) if f > 1 else img     # only when no resizer was available


def pick_font(candidates, fallback):
    try:
        have = set(tkfont.families())
    except tk.TclError:
        return fallback
    for c in candidates:
        if c in have:
            return c
    return fallback


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# ------------------------------------------------------------------ theme --
def apply_theme(root):
    ui = pick_font(["Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue",
                    "Inter", "Ubuntu", "Cantarell", "DejaVu Sans"], "TkDefaultFont")
    mono = pick_font(["Cascadia Mono", "Cascadia Code", "JetBrains Mono", "SF Mono", "Menlo",
                      "Consolas", "DejaVu Sans Mono", "Liberation Mono"], "TkFixedFont")
    fonts = {"ui": (ui, 10), "ui_b": (ui, 10, "bold"), "small": (ui, 9),
             "card": (ui, 9, "bold"), "title": (ui, 20, "bold"), "sub": (ui, 10),
             "stat": (ui, 16, "bold"), "stat_l": (ui, 8, "bold"), "mono": (mono, 10),
             "mono_b": (mono, 10, "bold")}

    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", SURFACE2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", VIOLET)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", fonts["ui"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, font=fonts["ui"],
                bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                troughcolor=SURFACE, focuscolor=VIOLET, selectbackground=VIOLET,
                selectforeground="#ffffff", insertcolor=CYAN)

    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=SURFACE)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    s.configure("Hint.TLabel", background=SURFACE, foreground=DIM, font=fonts["small"])
    s.configure("Status.TLabel", background=BG, foreground=MUTED)
    s.configure("StatV.TLabel", background=SURFACE, foreground=TEXT, font=fonts["stat"])
    s.configure("StatL.TLabel", background=SURFACE, foreground=MUTED, font=fonts["stat_l"])

    s.configure("Card.TLabelframe", background=SURFACE, bordercolor=BORDER,
                lightcolor=SURFACE, darkcolor=SURFACE, relief="solid", borderwidth=1)
    s.configure("Card.TLabelframe.Label", background=SURFACE, foreground=CYAN,
                font=fonts["card"])

    field = dict(fieldbackground=SURFACE2, foreground=TEXT, background=SURFACE2,
                 bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2,
                 insertcolor=CYAN, arrowcolor=MUTED, padding=5)
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, **field)
        s.map(w, bordercolor=[("focus", VIOLET)], lightcolor=[("focus", VIOLET)],
              fieldbackground=[("disabled", SURFACE), ("readonly", SURFACE2)],
              foreground=[("disabled", DIM)], arrowcolor=[("disabled", DIM), ("active", CYAN)],
              background=[("active", SURFACE2), ("disabled", SURFACE)],
              selectbackground=[("!focus", SURFACE2)], selectforeground=[("!focus", TEXT)])

    for w in ("TRadiobutton", "TCheckbutton"):
        s.configure(w, background=SURFACE, foreground=TEXT, indicatorbackground=SURFACE2,
                    indicatorforeground=CYAN, indicatormargin=(0, 0, 6, 0), focuscolor=SURFACE,
                    bordercolor=BORDER, upperbordercolor=BORDER, lowerbordercolor=BORDER)
        s.map(w, background=[("active", SURFACE)], foreground=[("disabled", DIM)],
              indicatorbackground=[("selected", SURFACE2), ("active", SURFACE2)],
              indicatorforeground=[("selected", CYAN)])

    s.configure("Accent.TButton", background=VIOLET, foreground="#ffffff", font=fonts["ui_b"],
                bordercolor=VIOLET, lightcolor=VIOLET_H, darkcolor=VIOLET, padding=(18, 7),
                focuscolor=VIOLET)
    s.map("Accent.TButton",
          background=[("disabled", SURFACE2), ("pressed", "#7c3aed"), ("active", VIOLET_H)],
          foreground=[("disabled", DIM)],
          bordercolor=[("disabled", BORDER), ("active", VIOLET_H)],
          lightcolor=[("disabled", SURFACE2)], darkcolor=[("disabled", SURFACE2)])
    s.configure("Ghost.TButton", background=SURFACE2, foreground=TEXT, bordercolor=BORDER,
                lightcolor=SURFACE2, darkcolor=SURFACE2, padding=(14, 7), focuscolor=SURFACE2)
    s.map("Ghost.TButton",
          background=[("disabled", SURFACE), ("pressed", BORDER), ("active", "#1c2640")],
          foreground=[("disabled", DIM)], bordercolor=[("active", CYAN), ("disabled", BORDER)])

    s.configure("Neon.Horizontal.TProgressbar", troughcolor=SURFACE2, background=CYAN,
                bordercolor=SURFACE2, lightcolor=CYAN, darkcolor=VIOLET, thickness=4)

    s.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=MUTED, bordercolor=BG,
                lightcolor=BG, darkcolor=BG, padding=(16, 6), font=fonts["ui_b"])
    s.map("TNotebook.Tab", background=[("selected", SURFACE)],
          foreground=[("selected", CYAN), ("active", TEXT)],
          lightcolor=[("selected", VIOLET)], bordercolor=[("selected", BORDER)])

    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, rowheight=24,
                font=fonts["ui"])
    s.map("Treeview", background=[("selected", "#2a2150")], foreground=[("selected", "#ffffff")])
    s.configure("Treeview.Heading", background=SURFACE2, foreground=MUTED, font=fonts["stat_l"],
                bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2, relief="flat",
                padding=(6, 4))
    s.map("Treeview.Heading", background=[("active", SURFACE2)])
    s.configure("Sect.TLabel", background=SURFACE, foreground=TEXT, font=fonts["ui_b"])
    s.configure("SectHint.TLabel", background=SURFACE, foreground=MUTED, font=fonts["small"])
    s.configure("Banner.TFrame", background="#1b1640")
    s.configure("Banner.TLabel", background="#1b1640", foreground=TEXT)
    s.configure("Vertical.TScrollbar", background=SURFACE2, troughcolor=SURFACE,
                bordercolor=SURFACE, lightcolor=SURFACE2, darkcolor=SURFACE2,
                arrowcolor=MUTED, gripcount=0)
    s.map("Vertical.TScrollbar", background=[("active", BORDER)])
    return fonts


def dark_title_bar(root):
    """Ask Windows 10/11 for a dark title bar (no effect elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


# ----------------------------------------------------------------- header --
class NeuralHeader(tk.Canvas):
    """Gradient banner with a node network that pulses while a search runs."""

    def __init__(self, parent, fonts):
        super().__init__(parent, height=96, bg=BG, highlightthickness=0, bd=0)
        self.fonts = fonts
        self.active = False
        self.phase = 0.0
        self.nodes, self.edges, self._built_for = [], [], None
        self.logo, self.logo_plate = None, False
        self.on_menu = None
        self.bind("<Configure>", lambda e: self.redraw())
        for b in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):   # right-click (incl. macOS)
            self.bind(b, self._menu)

        # "⋮" menu button: its own little widget, so redrawing the banner never
        # touches it (deleting items under the pointer caused an event loop)
        self.menu_btn = tk.Canvas(self, width=28, height=34, highlightthickness=0, bd=0,
                                  bg=MENU_BG, cursor="hand2")
        for dy in (8, 17, 26):
            self.menu_btn.create_oval(12, dy - 2, 16, dy + 2, width=0, fill=MUTED, tags=("dot",))
        self.menu_btn.place(relx=1.0, x=-44, y=38)
        self.menu_btn.bind("<ButtonRelease-1>", self._menu)
        self.menu_btn.bind("<Enter>", lambda e: self._hot(True))
        self.menu_btn.bind("<Leave>", lambda e: self._hot(False))

    def _hot(self, on):
        self.menu_btn.configure(bg=SURFACE2 if on else MENU_BG)
        self.menu_btn.itemconfigure("dot", fill=TEXT if on else MUTED)

    def _menu(self, event):
        if self.on_menu:
            self.on_menu(event)

    def _layout(self, w, h):
        """Scatter nodes across the right side and link near neighbours."""
        if self._built_for == (w, h):
            return
        self._built_for = (w, h)
        rnd = random.Random(7)
        x0 = max(w * 0.45, self.title_end() + 70)
        if x0 > w - 120:          # window too narrow for the network; skip it
            self.nodes, self.edges = [], []
            return
        self.nodes = []
        for _ in range(60):
            for _try in range(30):   # keep nodes apart so the web looks even
                x, y = rnd.uniform(x0, w - 50), rnd.uniform(12, h - 14)
                if all(math.hypot(x - a, y - b) > 26 for a, b, _ in self.nodes):
                    self.nodes.append((x, y, rnd.uniform(0, 6.28)))
                    break
        self.edges = []
        for i, (x1, y1, _) in enumerate(self.nodes):
            near = sorted(((math.hypot(x1 - x2, y1 - y2), j)
                           for j, (x2, y2, _) in enumerate(self.nodes) if j != i))[:3]
            for d, j in near:
                if d < 95 and (j, i) not in self.edges:
                    self.edges.append((i, j))

    def redraw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 200), int(self["height"])
        steps = 64
        for i in range(steps):
            x0 = w * i / steps
            self.create_rectangle(x0, 0, x0 + w / steps + 1, h, width=0,
                                  fill=blend(BG, "#1a1240", (i / steps) ** 1.6))
        self.create_line(0, h - 1, w, h - 1, fill=BORDER)
        for x in range(0, w, 3):   # thin neon underline, violet -> cyan
            self.create_line(x, h - 2, x + 3, h - 2, fill=blend(VIOLET, CYAN, x / w))

        self._layout(w, h)
        pts = [(x, y) for x, y, _ in self.nodes]
        glow = 0.5 + 0.5 * math.sin(self.phase)
        for i, j in self.edges:
            (x1, y1), (x2, y2) = pts[i], pts[j]
            base = blend(VIOLET, CYAN, x1 / w)
            # while running, a wave of light travels along the links
            wave = 0.5 + 0.5 * math.sin(self.phase - x1 / 60) if self.active else 0
            col = blend("#1a1f35", base, 0.28 + 0.5 * wave)
            self.create_line(x1, y1, x2, y2, fill=col)
        for (x, y), (_, _, off) in zip(pts, self.nodes):
            pulse = 0.5 + 0.5 * math.sin(self.phase * 1.7 + off) if self.active else 0.35
            r = 2 + 1.6 * pulse
            col = blend(VIOLET, CYAN, x / w)
            self.create_oval(x - r - 3, y - r - 3, x + r + 3, y + r + 3, width=0,
                             fill=blend(BG, col, 0.18 + 0.25 * pulse))
            self.create_oval(x - r, y - r, x + r, y + r, width=0, fill=blend("#2a3150", col, 0.5 + 0.5 * pulse))

        tx = 24
        if self.logo is not None:   # corporate logo on the left, title beside it
            lw, lh = self.logo.width(), self.logo.height()
            if self.logo_plate:     # light backing so dark logos stay visible
                pad = 8
                self._rounded(tx - pad + 4, h / 2 - lh / 2 - pad, tx + lw + pad + 4,
                              h / 2 + lh / 2 + pad, 8, "#f4f6fb")
                tx += 4
            self.create_image(tx, h / 2, anchor="w", image=self.logo)
            tx += lw + (14 if self.logo_plate else 0) + 18
            self.create_line(tx, h / 2 - 24, tx, h / 2 + 24, fill=BORDER)
            tx += 18
        self.create_text(tx, 34, anchor="w", text="VCF Logs Harvester", fill=TEXT,
                         font=self.fonts["title"])
        self.create_text(tx + 2, 64, anchor="w", fill=MUTED, font=self.fonts["sub"],
                         text="query  ·  resolve  ·  extract")
        self.create_oval(w - 34, 18, w - 26, 26, width=0,
                         fill=CYAN if self.active else GREEN)

    def _rounded(self, x0, y0, x1, y1, r, fill):
        pts = []
        for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                           (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
            for k in range(7):
                a = math.radians(a0 + 15 * k)
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        self.create_polygon(pts, fill=fill, outline="")

    def title_end(self):
        """x where the title text ends, so the network starts after it."""
        tx = 24
        if self.logo is not None:
            tx += self.logo.width() + (18 if self.logo_plate else 0) + 36
        return tx + tkfont.Font(font=self.fonts["title"]).measure("VCF Logs Harvester")

    def set_logo(self, photo, plate=False):
        self.logo, self.logo_plate = photo, plate
        self._built_for = None   # re-lay out the network around the title
        self.redraw()

    def tick(self):
        if self.active:
            self.phase += 0.18
            self.redraw()
            self._tick_id = self.after(60, self.tick)

    def set_active(self, on):
        was = self.active
        self.active = on
        if on and not was:
            self.tick()
        else:
            self.redraw()


# -------------------------------------------------------------------- app --
class HarvesterApp:
    def __init__(self, root, logo_arg=None):
        self.root = root
        root.title("VCF Logs Harvester")
        root.minsize(980, 780)
        self.f = apply_theme(root)

        self.events = []                 # every matching event from the last run
        self.shown = 0                   # how many are in the results box
        self.pages = 0
        self.t0 = None
        self.hl_terms = None             # regex for the search terms
        self.hl_ips = None               # regex for resolved IPs
        self.msgs = queue.Queue()        # worker thread -> UI thread
        self.stop_flag = threading.Event()
        self.worker = None

        self.logo_path, self.logo_plate, self._logo_img = None, False, None
        self._logo_from_settings = False
        self._build()
        self._load_settings()
        self.header.on_menu = self._logo_menu
        self._init_logo(logo_arg)
        self._toggle_time()
        self._toggle_domain()
        self._placeholder()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":   # Cmd+Q / app menu Quit
            root.createcommand("::tk::mac::Quit", self._on_close)
        self._poll_id = root.after(100, self._poll)
        dark_title_bar(root)

    # ---------------------------------------------------------- widgets --
    def _text(self, parent, height, mono=True, wrap="none"):
        t = tk.Text(parent, height=height, wrap=wrap, undo=True, relief="flat", bd=0,
                    bg=SURFACE2, fg=TEXT, insertbackground=CYAN, selectbackground=VIOLET,
                    selectforeground="#ffffff", highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=VIOLET, padx=8, pady=6,
                    font=self.f["mono"] if mono else self.f["ui"])
        return t

    def _card(self, parent, title):
        lf = ttk.LabelFrame(parent, text=f"  ◆  {title}  ", style="Card.TLabelframe", padding=(12, 8))
        return lf

    def _build(self):
        pad = {"padx": 6, "pady": 4}
        self.header = NeuralHeader(self.root, self.f)
        self.header.pack(fill="x")

        outer = ttk.Frame(self.root, padding=(14, 12, 14, 12))
        outer.pack(fill="both", expand=True)

        self.form = ttk.Frame(outer)     # the input panels; can be collapsed
        self.form.pack(fill="x")
        top = ttk.Frame(self.form)
        top.pack(fill="x")
        top.columnconfigure(0, weight=3)
        top.columnconfigure(1, weight=2)

        # --- connection -------------------------------------------------
        conn = self._card(top, "CONNECTION")
        conn.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.v_host = tk.StringVar()
        self.v_port = tk.StringVar(value="9543")
        self.v_user = tk.StringVar()
        self.v_pass = tk.StringVar()
        self.v_provider = tk.StringVar(value="Local")
        self.v_domain = tk.StringVar()
        self.v_insecure = tk.BooleanVar(value=True)

        L = lambda p, t, r, c: ttk.Label(p, text=t, style="Card.TLabel").grid(row=r, column=c, sticky="w", **pad)
        L(conn, "Server", 0, 0)
        ttk.Entry(conn, textvariable=self.v_host).grid(row=0, column=1, sticky="we", **pad)
        L(conn, "Port", 0, 2)
        ttk.Entry(conn, textvariable=self.v_port, width=7).grid(row=0, column=3, sticky="w", **pad)
        L(conn, "Username", 1, 0)
        ttk.Entry(conn, textvariable=self.v_user).grid(row=1, column=1, sticky="we", **pad)
        L(conn, "Password", 1, 2)
        self.e_pass = ttk.Entry(conn, textvariable=self.v_pass, show="•", width=16)
        self.e_pass.grid(row=1, column=3, sticky="we", **pad)
        L(conn, "Sign in with", 2, 0)
        cb = ttk.Combobox(conn, textvariable=self.v_provider, state="readonly", width=16,
                          values=["Local", "ActiveDirectory", "vIDM"])
        cb.grid(row=2, column=1, sticky="w", **pad)
        cb.bind("<<ComboboxSelected>>", lambda e: self._toggle_domain())
        L(conn, "Domain", 2, 2)
        self.e_domain = ttk.Entry(conn, textvariable=self.v_domain, width=16)
        self.e_domain.grid(row=2, column=3, sticky="we", **pad)
        ttk.Checkbutton(conn, text="Skip certificate check (self-signed)",
                        variable=self.v_insecure).grid(row=3, column=1, columnspan=3, sticky="w", **pad)
        conn.columnconfigure(1, weight=1)

        # --- time window ------------------------------------------------
        tw = self._card(top, "TIME WINDOW")
        tw.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.v_mode = tk.StringVar(value="last")
        self.v_last_n = tk.StringVar(value="1")
        self.v_last_unit = tk.StringVar(value="hours")
        self.v_start = tk.StringVar()
        self.v_end = tk.StringVar()
        self.v_tz = tk.StringVar()

        ttk.Radiobutton(tw, text="Last", value="last", variable=self.v_mode,
                        command=self._toggle_time).grid(row=0, column=0, sticky="w", **pad)
        row = ttk.Frame(tw, style="Card.TFrame")
        row.grid(row=0, column=1, sticky="w")
        self.sp_last = ttk.Spinbox(row, from_=1, to=9999, textvariable=self.v_last_n, width=6)
        self.sp_last.pack(side="left", padx=6, pady=4)
        self.cb_unit = ttk.Combobox(row, textvariable=self.v_last_unit, state="readonly",
                                    width=9, values=list(UNITS))
        self.cb_unit.pack(side="left", pady=4)

        ttk.Radiobutton(tw, text="From", value="range", variable=self.v_mode,
                        command=self._toggle_time).grid(row=1, column=0, sticky="w", **pad)
        self.e_start = ttk.Entry(tw, textvariable=self.v_start)
        self.e_start.grid(row=1, column=1, sticky="we", **pad)
        ttk.Label(tw, text="To", style="Card.TLabel").grid(row=2, column=0, sticky="e", padx=(6, 10), pady=4)
        self.e_end = ttk.Entry(tw, textvariable=self.v_end)
        self.e_end.grid(row=2, column=1, sticky="we", **pad)
        ttk.Label(tw, text="Zone", style="Card.TLabel").grid(row=3, column=0, sticky="e", padx=(6, 10), pady=4)
        self.e_tz = ttk.Entry(tw, textvariable=self.v_tz)
        self.e_tz.grid(row=3, column=1, sticky="we", **pad)
        ttk.Label(tw, style="Hint.TLabel",
                  text="2026-09-28 13:00  ·  blank To = now  ·  blank Zone = local"
                  ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6)
        tw.columnconfigure(1, weight=1)

        # --- search -----------------------------------------------------
        srch = self._card(self.form, "QUERY")
        srch.pack(fill="x", pady=(12, 0))

        def box(label, hint, col):
            fr = ttk.Frame(srch, style="Card.TFrame")
            fr.grid(row=0, column=col, sticky="nsew", padx=6)
            ttk.Label(fr, text=label, style="Card.TLabel").pack(anchor="w", pady=(0, 4))
            t = self._text(fr, 4)
            t.pack(fill="both", expand=True)
            ttk.Label(fr, text=hint, style="Hint.TLabel").pack(anchor="w", pady=(3, 0))
            return t

        self.t_text = box("Text contains", "one per line  ·  e.g. DROP  ·  * = wildcard", 0)
        self.t_resolve = box("Hosts  →  resolved to IP", "short name, FQDN or IP, one per line", 1)
        self.t_fields = box("Field filters", "NAME=VALUE per line  ·  e.g. appname=vpxd", 2)
        for c in range(3):
            srch.columnconfigure(c, weight=1)

        opts = ttk.Frame(srch, style="Card.TFrame")
        opts.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.v_match = tk.StringVar(value="all")
        self.v_operator = tk.StringVar(value="CONTAINS")
        ttk.Label(opts, text="Several text lines must match", style="Card.TLabel").pack(side="left", padx=(6, 8))
        ttk.Radiobutton(opts, text="all", value="all", variable=self.v_match).pack(side="left")
        ttk.Radiobutton(opts, text="any", value="any", variable=self.v_match).pack(side="left", padx=(8, 24))
        ttk.Label(opts, text="Operator", style="Card.TLabel").pack(side="left", padx=(0, 8))
        ttk.Combobox(opts, textvariable=self.v_operator, state="readonly", width=15,
                     values=["CONTAINS", "HAS", "MATCHES_REGEX", "NOT_CONTAINS"]).pack(side="left")

        # --- action bar + stats -----------------------------------------
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(12, 10))
        self.bar = bar
        self.b_run = ttk.Button(bar, text="▶  Run query", style="Accent.TButton", command=self._start)
        self.b_run.pack(side="left")
        self.b_stop = ttk.Button(bar, text="■  Stop", style="Ghost.TButton", command=self._stop, state="disabled")
        self.b_stop.pack(side="left", padx=8)
        self.b_save = ttk.Button(bar, text="↓  Save results", style="Ghost.TButton",
                                 command=self._save, state="disabled")
        self.b_save.pack(side="left")
        self.v_fold = tk.StringVar(value="▴  Hide form")
        ttk.Button(bar, textvariable=self.v_fold, style="Ghost.TButton",
                   command=self._toggle_form).pack(side="left", padx=8)

        stats = ttk.Frame(bar)
        stats.pack(side="right")
        self.v_events = tk.StringVar(value="0")
        self.v_pages = tk.StringVar(value="0")
        self.v_elapsed = tk.StringVar(value="0.0s")
        for label, var, color in (("EVENTS", self.v_events, CYAN), ("PAGES", self.v_pages, VIOLET_H),
                                  ("ELAPSED", self.v_elapsed, GREEN)):
            chip = tk.Frame(stats, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
            chip.pack(side="left", padx=(8, 0))
            tk.Frame(chip, bg=color, width=3).pack(side="left", fill="y")
            inner = ttk.Frame(chip, style="Card.TFrame", padding=(10, 3, 14, 3))
            inner.pack(side="left")
            ttk.Label(inner, text=label, style="StatL.TLabel").pack(anchor="w")
            ttk.Label(inner, textvariable=var, style="StatV.TLabel").pack(anchor="w")

        self.progress = ttk.Progressbar(outer, mode="determinate", value=0,
                                        style="Neon.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 6))

        # --- results / activity ----------------------------------------
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        self.t_results = self._output(nb)
        self.t_log = self._output(nb)
        nb.add(self.t_results.tab_frame, text="  Results  ")
        nb.add(self._build_summary(nb), text="  Summary  ")
        nb.add(self.t_log.tab_frame, text="  Activity  ")
        self.nb = nb

        # "Filtered by …" banner above the results (hidden until a filter is applied)
        self.filter_idx = None
        self.v_filter = tk.StringVar()
        self.banner = ttk.Frame(self.t_results.tab_frame, style="Banner.TFrame", padding=(14, 6))
        ttk.Label(self.banner, textvariable=self.v_filter, style="Banner.TLabel").pack(side="left")
        ttk.Button(self.banner, text="✕  Clear filter", style="Ghost.TButton",
                   command=self._clear_filter).pack(side="right")

        for t in (self.t_results, self.t_log):
            t.tag_configure("ts", foreground="#5eead4")
            t.tag_configure("ip", foreground=PINK)
            t.tag_configure("target", foreground=GREEN, font=self.f["mono_b"])
            t.tag_configure("hit", foreground=AMBER, background="#2a2108", font=self.f["mono_b"])
            t.tag_configure("muted", foreground=MUTED)
            t.tag_configure("dim", foreground=DIM)
            t.tag_configure("accent", foreground=VIOLET_H)
            t.tag_configure("ok", foreground=GREEN)
            t.tag_configure("err", foreground=RED, font=self.f["mono_b"])
            t.tag_configure("center", justify="center")

        # --- status line -------------------------------------------------
        st = ttk.Frame(outer)
        st.pack(fill="x", pady=(8, 0))
        self.dot = tk.Canvas(st, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(2, 8))
        self.v_status = tk.StringVar(value="Ready")
        ttk.Label(st, textvariable=self.v_status, style="Status.TLabel").pack(side="left")
        ttk.Label(st, text="Enter = run  ·  password is never stored", style="Status.TLabel").pack(side="right")
        self._set_dot(GREEN)
        self.root.bind("<Return>", self._on_return)

    def _output(self, nb):
        fr = tk.Frame(nb, bg=SURFACE, highlightthickness=0)
        body = tk.Frame(fr, bg=SURFACE)
        body.pack(fill="both", expand=True)
        t = tk.Text(body, wrap="word", relief="flat", bd=0, bg=SURFACE, fg=TEXT,
                    insertbackground=CYAN, selectbackground=VIOLET, selectforeground="#ffffff",
                    padx=14, pady=10, font=self.f["mono"], state="disabled", spacing1=1)
        sb = ttk.Scrollbar(body, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        t.tab_frame, t.body = fr, body
        return t

    # ------------------------------------------------------ summary tab --
    def _build_summary(self, nb):
        fr = tk.Frame(nb, bg=SURFACE)
        # headline numbers
        row = tk.Frame(fr, bg=SURFACE)
        row.pack(fill="x", padx=14, pady=(12, 4))
        self.sum_vars = {}
        for key, label in (("events", "EVENTS"), ("patterns", "PATTERNS"), ("hosts", "HOSTS"),
                           ("rare", "SEEN ONCE"), ("peak", "BUSIEST")):
            cell = tk.Frame(row, bg=SURFACE)
            cell.pack(side="left", padx=(0, 34))
            ttk.Label(cell, text=label, style="StatL.TLabel").pack(anchor="w")
            v = tk.StringVar(value="–")
            ttk.Label(cell, textvariable=v, style="StatV.TLabel").pack(anchor="w")
            self.sum_vars[key] = v

        # timeline
        head = tk.Frame(fr, bg=SURFACE)
        head.pack(fill="x", padx=14, pady=(8, 0))
        ttk.Label(head, text="Events over time", style="Sect.TLabel").pack(side="left")
        self.v_tl_hint = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.v_tl_hint, style="SectHint.TLabel").pack(side="left", padx=10)
        self.tl = tk.Canvas(fr, height=132, bg=SURFACE, highlightthickness=0, cursor="hand2")
        self.tl.pack(fill="x", padx=8, pady=(4, 6))
        self.tl.bind("<Configure>", lambda e: self._draw_timeline())
        self.tl.bind("<Motion>", self._tl_motion)
        self.tl.bind("<Leave>", lambda e: self._draw_timeline(hover=None))
        self.tl.bind("<Button-1>", self._tl_click)
        self.tl_hover = None

        # tables
        panes = ttk.PanedWindow(fr, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=14, pady=(4, 12))

        left = tk.Frame(panes, bg=SURFACE)
        lh = tk.Frame(left, bg=SURFACE)
        lh.pack(fill="x", pady=(0, 4))
        ttk.Label(lh, text="Log patterns", style="Sect.TLabel").pack(side="left")
        ttk.Label(lh, text="similar lines grouped · click to filter",
                  style="SectHint.TLabel").pack(side="left", padx=10)
        self.v_psort = tk.StringVar(value="Most frequent")
        cb = ttk.Combobox(lh, textvariable=self.v_psort, state="readonly", width=14,
                          values=["Most frequent", "Rarest first"])
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self._fill_patterns())
        self.tv_pat = self._tree(left, [("count", "COUNT", 70, "e"), ("pattern", "PATTERN", 600, "w")])
        panes.add(left, weight=3)

        right = tk.Frame(panes, bg=SURFACE)
        ttk.Label(right, text="Highlights", style="Sect.TLabel").pack(anchor="w", pady=(0, 4))
        self.tv_hl = self._tree(right, [("count", "COUNT", 64, "e"), ("name", "WHAT", 190, "w"),
                                        ("hosts", "HOSTS", 150, "w")], height=6, expand=False)
        for sev, color in (("critical", RED), ("warning", AMBER), ("info", TEXT)):
            self.tv_hl.tag_configure(sev, foreground=color)
        th = tk.Frame(right, bg=SURFACE)
        th.pack(fill="x", pady=(12, 4))
        ttk.Label(th, text="Top", style="Sect.TLabel").pack(side="left")
        self.v_topkind = tk.StringVar(value="Hosts")
        cb2 = ttk.Combobox(th, textvariable=self.v_topkind, state="readonly", width=8,
                           values=["Hosts", "Apps", "Users", "IPs"])
        cb2.pack(side="left", padx=8)
        cb2.bind("<<ComboboxSelected>>", lambda e: self._fill_top())
        self.tv_top = self._tree(right, [("count", "COUNT", 64, "e"), ("value", "VALUE", 280, "w")])
        panes.add(right, weight=2)

        self.tv_pat.bind("<<TreeviewSelect>>", lambda e: self._tree_pick(self.tv_pat, "Pattern"))
        self.tv_hl.bind("<<TreeviewSelect>>", lambda e: self._tree_pick(self.tv_hl, "Highlight"))
        self.tv_top.bind("<<TreeviewSelect>>",
                         lambda e: self._tree_pick(self.tv_top, self.v_topkind.get().rstrip("s")))
        self.summary = None
        self._tree_rows = {}
        self._draw_timeline()
        return fr

    def _tree(self, parent, cols, height=10, expand=True):
        box = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="both", expand=expand)
        tv = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings",
                          height=height, selectmode="browse")
        for key, title, width, anchor in cols:
            tv.heading(key, text=title, anchor=anchor)
            tv.column(key, width=width, anchor=anchor, stretch=(anchor == "w"))
        sb = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True)
        return tv

    def _fill(self, tv, rows):
        """rows: [(values tuple, event indexes, tag or None)]"""
        tv.delete(*tv.get_children())
        for n, (vals, idx, tag) in enumerate(rows):
            iid = f"{id(tv)}-{n}"
            tv.insert("", "end", iid=iid, values=vals, tags=(tag,) if tag else ())
            self._tree_rows[iid] = (vals, idx)

    def _fill_patterns(self):
        if not self.summary:
            return
        pats = self.summary["patterns"]
        if self.v_psort.get() == "Rarest first":
            pats = sorted(pats, key=lambda kv: (len(kv[1]), kv[0]))
        self._fill(self.tv_pat, [((f"{len(i):,}", p), i, None) for p, i in pats[:2000]])

    def _fill_top(self):
        if not self.summary:
            return
        key = {"Hosts": "hosts", "Apps": "apps", "Users": "users", "IPs": "ips"}[self.v_topkind.get()]
        self._fill(self.tv_top, [((f"{len(i):,}", k), i, None) for k, i in self.summary[key][:500]])

    def _show_summary(self, s):
        self.summary = s
        self._tree_rows = {}
        counts = [len(b) for b in s["buckets"]]
        peak = max(counts) if counts else 0
        self.sum_vars["events"].set(f"{s['total']:,}")
        self.sum_vars["patterns"].set(f"{len(s['patterns']):,}")
        self.sum_vars["hosts"].set(f"{len(s['hosts']):,}")
        self.sum_vars["rare"].set(f"{sum(1 for _, i in s['patterns'] if len(i) == 1):,}")
        self.sum_vars["peak"].set(f"{peak:,} / {self._dur(s['bucket_ms'])}" if peak else "–")
        self.v_tl_hint.set(f"per {self._dur(s['bucket_ms'])}  ·  hover for detail, click a bar to filter")
        self._fill_patterns()
        self._fill_top()
        icon = {"critical": "▲", "warning": "●", "info": "○"}
        self._fill(self.tv_hl, [((f"{len(h['idx']):,}", f"{icon[h['severity']]}  {h['name']}",
                                  ", ".join(h["hosts"][:4]) + (" …" if len(h["hosts"]) > 4 else "")),
                                 h["idx"], h["severity"]) for h in s["highlights"]]
                  or [(("", "No highlights in these results", ""), [], "info")])
        self._draw_timeline()

    def _clear_summary(self):
        self.summary = None
        self._tree_rows = {}
        for v in self.sum_vars.values():
            v.set("–")
        self.v_tl_hint.set("")
        for tv in (self.tv_pat, self.tv_hl, self.tv_top):
            tv.delete(*tv.get_children())
        self._draw_timeline()

    @staticmethod
    def _dur(ms):
        for unit, size in (("d", 86400000), ("h", 3600000), ("min", 60000), ("s", 1000)):
            if ms >= size and ms % size == 0:
                return f"{ms // size} {unit}"
        return f"{ms} ms"

    # timeline chart -----------------------------------------------------
    TL_L, TL_R, TL_T, TL_B = 52, 14, 12, 24

    def _tl_geom(self):
        w, h = self.tl.winfo_width(), int(self.tl["height"])
        n = len(self.summary["buckets"])
        pw = max(w - self.TL_L - self.TL_R, 10)
        return w, h, n, pw, pw / n

    def _draw_timeline(self, hover="keep"):
        c = self.tl
        c.delete("all")
        if hover != "keep":
            self.tl_hover = hover
        w, h = c.winfo_width(), int(c["height"])
        if not self.summary or not self.summary["total"]:
            c.create_text(w / 2, h / 2, fill=DIM, font=self.f["ui"],
                          text="Run a query to see patterns, top hosts and highlights here")
            return
        s = self.summary
        w, h, n, pw, slot = self._tl_geom()
        counts = [len(b) for b in s["buckets"]]
        top = self._nice(max(counts))
        base = h - self.TL_B
        ph = base - self.TL_T
        for frac in (0, 0.5, 1):   # hairline grid + clean ticks
            y = base - ph * frac
            c.create_line(self.TL_L, y, w - self.TL_R, y, fill=BORDER if frac == 0 else "#182238")
            c.create_text(self.TL_L - 8, y, anchor="e", fill=MUTED, font=self.f["small"],
                          text=f"{int(top * frac):,}")
        bw = max(min(24, slot - 2), 1)
        for i, v in enumerate(counts):
            if not v:
                continue
            x0 = self.TL_L + i * slot + (slot - bw) / 2
            y = base - ph * v / top
            color = CYAN if i == self.tl_hover else ("#8b7cf6" if i == getattr(self, "tl_sel", None) else BAR)
            self._bar(x0, y, x0 + bw, base, color)
        # x labels: start, middle, end
        for i, anchor in ((0, "w"), (n // 2, "center"), (n, "e")):
            x = self.TL_L + i * slot
            c.create_text(x, base + 12, anchor=anchor, fill=MUTED, font=self.f["small"],
                          text=self._fmt_t(s["start"] + i * s["bucket_ms"], s))
        if self.tl_hover is not None and 0 <= self.tl_hover < n:
            self._tl_tip(self.tl_hover, counts[self.tl_hover], w, slot)

    def _bar(self, x0, y0, x1, y1, color):
        """Column with a 4px rounded top and a square base."""
        r = min(4, (x1 - x0) / 2, max(y1 - y0, 0))
        if r < 1:
            self.tl.create_rectangle(x0, y0, x1, y1, fill=color, width=0)
            return
        pts = [x0, y1, x0, y0 + r]
        for k in range(1, 5):
            a = math.pi / 2 * k / 4
            pts += [x0 + r - r * math.cos(a), y0 + r - r * math.sin(a)]
        pts += [x1 - r, y0]
        for k in range(1, 5):
            a = math.pi / 2 * k / 4
            pts += [x1 - r + r * math.sin(a), y0 + r - r * math.cos(a)]
        pts += [x1, y1]
        self.tl.create_polygon(pts, fill=color, outline="")

    def _tl_tip(self, i, count, w, slot):
        s = self.summary
        t0 = s["start"] + i * s["bucket_ms"]
        text = (f"{self._fmt_t(t0, s, full=True)} – {self._fmt_t(t0 + s['bucket_ms'], s, full=True)}\n"
                f"{count:,} event{'s' if count != 1 else ''}")
        x = self.TL_L + (i + 0.5) * slot
        tid = self.tl.create_text(0, 0, anchor="nw", text=text, fill=TEXT, font=self.f["small"])
        x0, y0, x1, y1 = self.tl.bbox(tid)
        tw, th = x1 - x0 + 16, y1 - y0 + 10
        tx = min(max(x - tw / 2, 4), w - tw - 4)
        self.tl.coords(tid, tx + 8, 8)
        box = self.tl.create_rectangle(tx, 3, tx + tw, 3 + th, fill=SURFACE2, outline=BORDER)
        self.tl.tag_raise(tid, box)

    def _tl_index(self, x):
        if not self.summary or not self.summary["total"]:
            return None
        w, h, n, pw, slot = self._tl_geom()
        i = int((x - self.TL_L) // slot)
        return i if 0 <= i < n else None

    def _tl_motion(self, e):
        i = self._tl_index(e.x)
        if i != self.tl_hover:
            self._draw_timeline(hover=i)

    def _tl_click(self, e):
        i = self._tl_index(e.x)
        if i is None or not self.summary["buckets"][i]:
            return
        s = self.summary
        t0 = s["start"] + i * s["bucket_ms"]
        self.tl_sel = i
        self._apply_filter(f"Time  {self._fmt_t(t0, s, full=True)} – "
                           f"{self._fmt_t(t0 + s['bucket_ms'], s, full=True)}", s["buckets"][i])

    @staticmethod
    def _nice(v):
        if v <= 5:
            return max(v, 1)
        mag = 10 ** (len(str(int(v))) - 1)
        for m in (1, 2, 2.5, 5, 10):
            if v <= m * mag:
                return int(m * mag)
        return v

    @staticmethod
    def _fmt_t(ms, s, full=False):
        lt = time.localtime(ms / 1000)
        span = s["end"] - s["start"]
        if span > 86400000:
            return time.strftime("%b %d %H:%M", lt)
        if s["bucket_ms"] < 60000 or full and s["bucket_ms"] < 3600000:
            return time.strftime("%H:%M:%S", lt)
        return time.strftime("%H:%M", lt)

    # filtering the results ---------------------------------------------
    def _tree_pick(self, tv, kind):
        sel = tv.selection()
        if not sel or sel[0] not in self._tree_rows:
            return
        vals, idx = self._tree_rows[sel[0]]
        if not idx:
            return
        label = vals[1].lstrip("▲●○ ").strip()
        self.tl_sel = None
        self._apply_filter(f"{kind}  {label[:110]}", idx)

    def _apply_filter(self, label, idx):
        self.filter_idx = list(idx)
        self.v_filter.set(f"Filtered by  {label}   ·   {len(idx):,} of {len(self.events):,} events")
        self.banner.pack(fill="x", before=self.t_results.body)
        self._render_results(self.filter_idx)
        self._draw_timeline()
        self.nb.select(0)

    def _clear_filter(self):
        self.filter_idx = None
        self.tl_sel = None
        self.banner.pack_forget()
        for tv in (self.tv_pat, self.tv_hl, self.tv_top):
            tv.selection_remove(*tv.selection())
        self._render_results(None)
        self._draw_timeline()

    def _render_results(self, idx):
        t = self.t_results
        self._clear(t)
        t.configure(state="normal")
        rows = idx if idx is not None else range(len(self.events))
        for n, i in enumerate(rows):
            if n >= DISPLAY_LIMIT:
                t.insert("end", f"\n… {len(rows) - DISPLAY_LIMIT:,} more (Save writes all of them)\n",
                         ("muted",))
                break
            self._insert_event((self.events[i].get("text") or "").rstrip(), n == 0)
        t.configure(state="disabled")
        t.see("1.0")

    def _set_dot(self, color):
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 9, 9, fill=color, width=0)

    def _on_return(self, event):
        # Enter inside a multi-line box adds a line; elsewhere it runs the query
        if isinstance(event.widget, tk.Text) and event.widget not in (self.t_results, self.t_log):
            return None
        if self.worker is None:
            self._start()
        return "break"

    def _toggle_time(self):
        last = self.v_mode.get() == "last"
        self.sp_last.configure(state="normal" if last else "disabled")
        self.cb_unit.configure(state="readonly" if last else "disabled")
        for e in (self.e_start, self.e_end, self.e_tz):
            e.configure(state="disabled" if last else "normal")

    def _toggle_domain(self):
        self.e_domain.configure(
            state="disabled" if self.v_provider.get() == "Local" else "normal")

    @staticmethod
    def _lines(textbox):
        return [l.strip() for l in textbox.get("1.0", "end").splitlines() if l.strip()]

    @staticmethod
    def _clear(textbox):
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.configure(state="disabled")

    def _placeholder(self):
        t = self.t_results
        self._clear(t)
        t.configure(state="normal")
        t.insert("end", "\n\n\n◇\n\n", ("accent", "center"))
        t.insert("end", "Awaiting query\n", ("muted", "center"))
        t.insert("end", "Set a time window and what to match, then press Run query.\n", ("dim", "center"))
        t.configure(state="disabled")

    # ------------------------------------------------------ highlighting --
    def _insert_event(self, text, first):
        """Append one event to the results with colour highlighting."""
        t = self.t_results
        if not first:
            t.insert("end", "\n")
        spans = []
        m = STAMP.match(text)
        if m:
            spans.append((m.start(), m.end(), "ts"))
        for m in IPV4.finditer(text):
            tag = "target" if self.hl_ips and self.hl_ips.fullmatch(m.group()) else "ip"
            spans.append((m.start(), m.end(), tag))
        if self.hl_terms:
            for m in self.hl_terms.finditer(text):
                spans.append((m.start(), m.end(), "hit"))
        start = t.index("end-1c")
        t.insert("end", text + "\n")
        for s, e, tag in spans:
            t.tag_add(tag, f"{start}+{s}c", f"{start}+{e}c")

    def _log_line(self, msg):
        t = self.t_log
        t.configure(state="normal")
        stamp = time.strftime("%H:%M:%S")
        tag = ("err" if msg.startswith("ERROR") else
               "accent" if msg.startswith(("Match:", "Server query:")) else
               "ok" if msg.startswith(("Authenticated", "Done", "Resolved")) or " is already an IP" in msg else
               "muted" if msg.lstrip().startswith("page") else None)
        t.insert("end", stamp + "  ", "dim")
        t.insert("end", msg + "\n", tag or ())
        t.see("end")
        t.configure(state="disabled")

    # --------------------------------------------------------------- logo --
    def _logo_scale(self):
        """Match the logo to the display's scaling (e.g. 150% on Windows)."""
        try:
            return max(1.0, min(3.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72)))
        except (tk.TclError, ValueError):
            return 1.0

    def _init_logo(self, logo_arg):
        """--logo beats the saved setting, which beats a banner_logo.* next to the script."""
        path = logo_arg
        if not path and not self._logo_from_settings:
            here = os.path.dirname(os.path.abspath(__file__))
            path = next((os.path.join(here, n) for n in LOGO_AUTO
                         if os.path.isfile(os.path.join(here, n))), None)
        elif not path:
            path = self.logo_path
        if path:
            self._apply_logo(path, quiet=not logo_arg)

    def _apply_logo(self, path, quiet=False):
        try:
            img = load_logo(path, self._logo_scale())
        except (ValueError, tk.TclError, OSError) as e:
            msg = str(e) if isinstance(e, ValueError) else f"Couldn't open that image:\n{e}"
            if quiet:
                self.v_status.set("Logo not shown: " + msg.replace("\n", " "))
            else:
                messagebox.showerror("Banner logo", msg, parent=self.root)
            return False
        self._logo_img = img             # keep a reference or Tk drops the image
        self.logo_path = os.path.abspath(os.path.expanduser(path))
        self.header.set_logo(img, self.logo_plate)
        return True

    def _logo_menu(self, event):
        m = tk.Menu(self.root, tearoff=0, bg=SURFACE2, fg=TEXT, activebackground=VIOLET,
                    activeforeground="#ffffff", disabledforeground=DIM, bd=0,
                    font=self.f["ui"])
        m.add_command(label="Set banner logo…", command=self._choose_logo)
        self.v_plate = tk.BooleanVar(value=self.logo_plate)
        m.add_checkbutton(label="Light background behind logo", variable=self.v_plate,
                          command=self._toggle_plate,
                          state="normal" if self._logo_img else "disabled")
        m.add_separator()
        m.add_command(label="Remove logo", command=self._remove_logo,
                      state="normal" if self._logo_img else "disabled")
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _choose_logo(self):
        start = os.path.dirname(self.logo_path) if self.logo_path else os.path.expanduser("~")
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose a banner logo", initialdir=start,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif"), ("All files", "*.*")])
        if path and self._apply_logo(path):
            self._save_settings()
            self.v_status.set(f"Banner logo set: {os.path.basename(path)}")

    def _toggle_plate(self):
        self.logo_plate = bool(self.v_plate.get())
        self.header.set_logo(self._logo_img, self.logo_plate)
        self._save_settings()

    def _remove_logo(self):
        self._logo_img, self.logo_path = None, None
        self.header.set_logo(None)
        self._save_settings()
        self.v_status.set("Banner logo removed")

    # ------------------------------------------------------------ settings --
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fh:
                s = json.load(fh)
        except Exception:
            return
        for key, var in (("host", self.v_host), ("port", self.v_port), ("user", self.v_user),
                         ("provider", self.v_provider), ("domain", self.v_domain),
                         ("match", self.v_match), ("operator", self.v_operator),
                         ("mode", self.v_mode), ("last_n", self.v_last_n),
                         ("last_unit", self.v_last_unit), ("tz", self.v_tz)):
            if key in s:
                var.set(s[key])
        if "logo" in s:              # "" means the user removed the logo
            self.logo_path = s["logo"] or None
            self._logo_from_settings = True
        self.logo_plate = bool(s.get("logo_plate", False))
        if "insecure" in s:
            self.v_insecure.set(bool(s["insecure"]))
        for key, box in (("text", self.t_text), ("resolve", self.t_resolve),
                         ("fields", self.t_fields)):
            if s.get(key):
                box.insert("1.0", "\n".join(s[key]))

    def _save_settings(self):
        try:
            s = self._settings_snapshot()
        except tk.TclError:
            return        # window already torn down; nothing to read
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(s, fh, indent=2)
        except Exception:
            pass  # remembering settings is only a convenience

    def _settings_snapshot(self):
        return {"host": self.v_host.get(), "port": self.v_port.get(), "user": self.v_user.get(),
             "provider": self.v_provider.get(), "domain": self.v_domain.get(),
             "insecure": self.v_insecure.get(), "match": self.v_match.get(),
             "operator": self.v_operator.get(), "mode": self.v_mode.get(),
             "last_n": self.v_last_n.get(), "last_unit": self.v_last_unit.get(),
             "tz": self.v_tz.get(), "text": self._lines(self.t_text),
             "resolve": self._lines(self.t_resolve), "fields": self._lines(self.t_fields),
             "logo": self.logo_path or "", "logo_plate": self.logo_plate}

    # ------------------------------------------------------------- search --
    def _collect(self):
        """Read and check the form. Returns a dict, or raises ValueError."""
        host = self.v_host.get().strip()
        user = self.v_user.get().strip()
        if not host:
            raise ValueError("Enter the Aria Operations for Logs server.")
        if not user:
            raise ValueError("Enter a username.")
        if not self.v_pass.get():
            raise ValueError("Enter the password.")
        try:
            port = int(self.v_port.get())
        except ValueError:
            raise ValueError("Port must be a number.")

        text, resolve = self._lines(self.t_text), self._lines(self.t_resolve)
        if not text and not resolve:
            raise ValueError("Enter at least one line under 'Text contains' or 'Hosts'.")

        fields = []
        for spec in self._lines(self.t_fields):
            name, sep, value = spec.partition("=")
            if not sep or not name.strip() or not value.strip():
                raise ValueError(f"Field filter must look like NAME=VALUE: '{spec}'")
            fields.append((name.strip(), "CONTAINS", value.strip()))

        now_ms = int(time.time() * 1000)
        try:
            if self.v_mode.get() == "last":
                n = int(self.v_last_n.get())
                if n <= 0:
                    raise ValueError
                dur = core.parse_duration(f"{n}{UNITS[self.v_last_unit.get()]}")
                start_ms, end_ms = now_ms - int(dur.total_seconds() * 1000), now_ms
            else:
                tz = core.get_tz(self.v_tz.get().strip() or None)
                if not self.v_start.get().strip():
                    raise ValueError("Enter a start time.")
                start_ms = core.parse_time(self.v_start.get(), tz)
                end_ms = core.parse_time(self.v_end.get(), tz) if self.v_end.get().strip() else now_ms
        except SystemExit as e:           # the core script's parsers exit on bad input
            raise ValueError(str(e))
        except ValueError as e:
            raise ValueError(str(e) or "Enter a whole number for the time window.")
        if start_ms >= end_ms:
            raise ValueError("The start of the window must be before the end.")

        return dict(host=host, port=port, user=user, password=self.v_pass.get(),
                    provider=self.v_provider.get(), domain=self.v_domain.get().strip() or None,
                    insecure=self.v_insecure.get(), text=text, resolve=resolve,
                    fields=fields, match=self.v_match.get(), operator=self.v_operator.get(),
                    start_ms=start_ms, end_ms=end_ms)

    def _start(self):
        if self.worker is not None:
            return
        try:
            params = self._collect()
        except ValueError as e:
            messagebox.showwarning("Check the form", str(e), parent=self.root)
            return
        self._save_settings()
        self.events, self.shown, self.pages = [], 0, 0
        self.filter_idx, self.tl_sel = None, None
        self.banner.pack_forget()
        self._clear_summary()
        self.t0 = time.time()
        terms = [t for t in params["text"] if params["operator"] in ("CONTAINS", "HAS")
                 and t.strip("*")]      # a bare "*" matches everything; don't highlight it
        self.hl_terms = (re.compile("|".join(core._term_regex(t).pattern for t in terms), re.I)
                         if terms else None)
        self.hl_ips = None
        self._clear(self.t_results)
        self._clear(self.t_log)
        self.v_events.set("0"); self.v_pages.set("0"); self.v_elapsed.set("0.0s")
        self.stop_flag.clear()
        self.b_run.configure(state="disabled")
        self.b_stop.configure(state="normal")
        self.b_save.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)
        self.header.set_active(True)
        self._set_dot(CYAN)
        self.v_status.set("Running query…")
        self.nb.select(0)
        self.worker = threading.Thread(target=self._work, args=(params,), daemon=True)
        self.worker.start()

    def _work(self, p):
        """Runs on a background thread so the window stays responsive."""
        core.log = lambda msg: self.msgs.put(("log", str(msg)))
        try:
            ips = []
            for name in p["resolve"]:
                for ip in core.resolve_name(name):
                    if ip not in ips:
                        ips.append(ip)
            self.msgs.put(("ips", ips))
            client = core.AriaLogsClient(p["host"], p["port"], p["user"], p["password"],
                                         p["provider"], domain=p["domain"],
                                         verify=not p["insecure"])
            client.login()
            core.log(f"Window: {core.ms_to_iso(p['start_ms'])} -> {core.ms_to_iso(p['end_ms'])}")
            collected = []

            def on_event(ev):
                collected.append(ev)
                self.msgs.put(("event", ev))
            total = core.harvest(client, p["text"], p["match"], p["operator"], p["fields"],
                                 ips, p["start_ms"], p["end_ms"], core.MAX_LIMIT,
                                 on_event, should_stop=self.stop_flag.is_set)
            if collected:
                core.log(f"Summarizing {len(collected):,} events…")
                self.msgs.put(("summary", core.summarize(collected, p["start_ms"], p["end_ms"])))
            self.msgs.put(("done", total))
        except SystemExit as e:
            self.msgs.put(("error", str(e)))
        except Exception as e:
            self.msgs.put(("error", f"{type(e).__name__}: {e}"))

    def _stop(self):
        self.stop_flag.set()
        self.v_status.set("Stopping…")

    def _poll(self):
        """Move messages from the worker thread into the window."""
        t = self.t_results
        opened = False
        try:
            for _ in range(2000):
                kind, val = self.msgs.get_nowait()
                if kind == "event":
                    self.events.append(val)
                    if self.shown < DISPLAY_LIMIT:
                        if not opened:
                            t.configure(state="normal")
                            opened = True
                        self._insert_event((val.get("text") or "").rstrip(), self.shown == 0)
                        self.shown += 1
                elif kind == "ips":
                    self.hl_ips = (re.compile("|".join(re.escape(i) for i in val)) if val else None)
                elif kind == "log":
                    if re.match(r"\s*page \d+", val):
                        self.pages += 1
                    self._log_line(val)
                elif kind == "summary":
                    self._show_summary(val)
                elif kind == "done":
                    self._finish(f"Complete  ·  {len(self.events):,} events", GREEN)
                elif kind == "error":
                    self._log_line("ERROR: " + val)
                    self._finish("Failed  ·  see Activity", RED)
                    self.nb.select(2)
                    messagebox.showerror("Query failed", val, parent=self.root)
        except queue.Empty:
            pass
        if opened:
            t.configure(state="disabled")
        if self.t0 is not None:
            self.v_events.set(f"{len(self.events):,}")
            self.v_pages.set(str(self.pages))
            if self.worker is not None:
                self.v_elapsed.set(f"{time.time() - self.t0:.1f}s")
                self.v_status.set(f"Running query…  {len(self.events):,} events")
        if not getattr(self, "_closing", False):
            self._poll_id = self.root.after(100, self._poll)

    def _finish(self, status, color):
        self.worker = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.header.set_active(False)
        self.v_elapsed.set(f"{time.time() - self.t0:.1f}s")
        self.b_run.configure(state="normal")
        self.b_stop.configure(state="disabled")
        self.b_save.configure(state="normal" if self.events else "disabled")
        if self.stop_flag.is_set():
            status, color = f"Stopped  ·  {len(self.events):,} events", AMBER
        if not self.events and color == GREEN:
            self.t_results.configure(state="normal")
            self.t_results.insert("end", "\n\n\nNo matching events in this window.\n", ("muted", "center"))
            self.t_results.configure(state="disabled")
        if len(self.events) > DISPLAY_LIMIT:
            status += f"  ·  showing first {DISPLAY_LIMIT:,}, Save writes all"
        self._set_dot(color)
        self.v_status.set(status)

    # --------------------------------------------------------------- save --
    def _toggle_form(self):
        if self.form.winfo_ismapped():
            self.form.pack_forget()
            self.v_fold.set("▾  Show form")
        else:
            self.form.pack(fill="x", before=self.bar)
            self.v_fold.set("▴  Hide form")

    def _save(self):
        if not self.events:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save filtered results" if self.filter_idx is not None else "Save results",
            defaultextension=".log",
            filetypes=[("Log lines", "*.log"), ("Text", "*.txt"),
                       ("JSON Lines (all fields)", "*.jsonl"), ("CSV (all fields)", "*.csv")])
        if not path:
            return
        # with a filter active, save just the filtered events
        events = ([self.events[i] for i in self.filter_idx] if self.filter_idx is not None
                  else self.events)
        low = path.lower()
        fmt = "csv" if low.endswith(".csv") else "jsonl" if low.endswith((".json", ".jsonl")) else "text"
        try:
            w = core.Writer(path, fmt)
            for ev in events:
                w.write(ev)
            w.close()
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self.root)
            return
        what = "filtered " if self.filter_idx is not None else ""
        self.v_status.set(f"Saved {len(events):,} {what}events to {os.path.basename(path)}")

    def _on_close(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self.stop_flag.set()
        for owner, attr in ((self.root, "_poll_id"), (self.header, "_tick_id")):
            try:
                owner.after_cancel(getattr(owner if owner is self.header else self, attr))
            except (AttributeError, tk.TclError, ValueError):
                pass
        self.header.active = False
        try:
            self._save_settings()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass


OLD_TK_MSG = """
  ! This Python uses Tk {ver}, the outdated version that ships with macOS,
    and no newer Python was found to switch to automatically.
    The window may be blank, black or unresponsive, and may not close.

    Fix: install Python from https://www.python.org/downloads/macos/
         (includes a current Tk), then run it with that Python, e.g.
           python3.14 vcf_logs_harvester_gui.py
    or with Homebrew:  brew install python python-tk
                       /opt/homebrew/bin/python3.14 vcf_logs_harvester_gui.py

    The command-line script (vcf_logs_harvester.py) is unaffected.
"""


# Where python.org and Homebrew install Python on macOS
NEWER_PYTHON_GLOBS = ("/opt/homebrew/bin/python3.*", "/usr/local/bin/python3.*",
                      "/Library/Frameworks/Python.framework/Versions/3.*/bin/python3.*")


def find_newer_python():
    """A python3.x on this Mac whose Tk is 8.6 or newer, newest first, or None."""
    import glob
    import subprocess
    found = {}
    for pattern in NEWER_PYTHON_GLOBS:
        for path in glob.glob(pattern):
            m = re.search(r"/python3\.(\d+)$", path)
            if m and os.access(path, os.X_OK):
                found.setdefault(os.path.realpath(path), (int(m.group(1)), path))
    for _, path in sorted(found.values(), reverse=True):
        try:
            ok = subprocess.run(
                [path, "-c", "import tkinter, sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)"],
                capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return path
    return None


def relaunch_if_old_tk():
    """On macOS, 'python3' is usually Apple's Python 3.9 with the outdated Tk 8.5.
    If a newer Python is installed, restart this script with it automatically."""
    if sys.platform != "darwin" or tk.TkVersion >= 8.6 or os.environ.get("VCF_LH_RELAUNCHED"):
        return
    newer = find_newer_python()
    if newer:
        print(f"This Python ({sys.executable}) has the outdated Tk {tk.TkVersion}; "
              f"restarting with {newer}.", file=sys.stderr, flush=True)
        os.environ["VCF_LH_RELAUNCHED"] = "1"
        os.execv(newer, [newer, os.path.abspath(__file__)] + sys.argv[1:])
    print(OLD_TK_MSG.format(ver=tk.TkVersion), file=sys.stderr, flush=True)


def main():
    relaunch_if_old_tk()
    try:   # crisp text on high-DPI Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description="VCF Logs Harvester window")
    ap.add_argument("--logo", metavar="IMAGE",
                    help="PNG, JPG or GIF to show in the banner (remembered for next time)")
    args, _ = ap.parse_known_args()
    root = tk.Tk()
    HarvesterApp(root, logo_arg=args.logo)
    root.mainloop()


if __name__ == "__main__":
    main()
