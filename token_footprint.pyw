"""Token Footprint: a desktop widget that turns Claude Code token usage into
estimated electricity, carbon, and water.

Reads the usage records Claude Code writes to ~/.claude/projects/**/*.jsonl,
plus Cowork sessions from the Claude desktop app, which use the same format.
Conversion factors and settings live in %APPDATA%/TokenFootprint/config.json.
"""
import json
import os
import subprocess
import sys
import shutil
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta, timezone
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # running as a PyInstaller .exe
APP_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))  # bundled icon + README
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "TokenFootprint"
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"
README_PATH = DATA_DIR / "README.md"
ICON_PATH = BUNDLE_DIR / "icon.ico"


def log_roots():
    """(source label, folder) pairs holding usage logs."""
    roots = [("code", Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects")]
    appdata = Path(os.environ.get("APPDATA", ""))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    # The Store install of the desktop app redirects its AppData into a package folder
    cowork = [appdata / "Claude"] + list(local.glob("Packages/Claude_*/LocalCache/Roaming/Claude"))
    roots += [("cowork", d / "local-agent-mode-sessions") for d in cowork]
    return [(src, d) for src, d in roots if d.is_dir()]

STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\Token Footprint.lnk"


def launch_command():
    """(target, arguments) that start this widget without a console window."""
    if FROZEN:
        return sys.executable, ""
    return str(Path(sys.executable).with_name("pythonw.exe")), f'"{Path(__file__).resolve()}"'


def make_shortcut(lnk, desktop=False):
    """Create a .lnk to this widget; with desktop=True, lnk is a file name placed on the user's Desktop."""
    target, args = launch_command()
    ps = (
        "$p=$env:LNK; if ($env:DESKTOP) { $p=Join-Path ([Environment]::GetFolderPath('Desktop')) $p };"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
        "$s.TargetPath=$env:TGT;$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:WD;"
        "if (Test-Path $env:ICO) { $s.IconLocation=$env:ICO+',0' };$s.Save()"
    )
    icon = sys.executable if FROZEN else str(ICON_PATH)
    env = dict(os.environ, LNK=str(lnk), DESKTOP="1" if desktop else "", TGT=target, ARGS=args,
               WD=str(Path(target).parent if FROZEN else APP_DIR), ICO=icon)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, creationflags=0x08000000)


def prepare_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Carry over settings from the pre-packaging layout (files next to the script)
    for name in ("config.json", "state.json"):
        old = APP_DIR / name
        if not FROZEN and old.exists() and not (DATA_DIR / name).exists():
            shutil.copy2(old, DATA_DIR / name)
    readme = BUNDLE_DIR / "README.md"
    if readme.exists():
        shutil.copy2(readme, README_PATH)

DEFAULT_CONFIG = {
    "_note": "Estimates only. Anthropic does not publish per-token energy data; see README.md for sources and reasoning.",
    "energy_wh_per_token": {
        "output": 0.001,
        "input": 0.0002,
        "cache_write": 0.0002,
        "cache_read": 0.00002,
    },
    "model_multipliers": {"opus": 1.0, "fable": 1.0, "sonnet": 0.5, "haiku": 0.15, "default": 1.0},
    "pue": 1.2,
    "grid_kg_co2_per_kwh": 0.37,
    "water_onsite_l_per_kwh": 0.5,
    "water_offsite_l_per_kwh": 3.1,
    "equivalents": {
        "wh_per_phone_charge": 15,
        "g_co2_per_mile_driven": 400,
        "liters_per_water_bottle": 0.5,
    },
    "refresh_seconds": 30,
}

PERIODS = [("Today", "today"), ("7 days", "7d"), ("30 days", "30d"), ("All", "all")]

# Colors
BG = "#141d19"
CARD = "#1d2923"
TEXT = "#e8efe9"
MUTED = "#8fa39a"
TOKEN_C = "#7bc47f"
POWER_C = "#f2c14e"
CARBON_C = "#b8c2cc"
WATER_C = "#5ab8e0"
FONT = "Segoe UI"


def deep_merge(base, override):
    out = dict(base)
    for k, v in override.items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return deep_merge(DEFAULT_CONFIG, load_json(CONFIG_PATH, {}))


class UsageReader:
    """Incrementally reads Claude Code JSONL logs, keeping one record per API response."""

    def __init__(self):
        self.offsets = {}   # file path -> bytes already read
        self.records = {}   # message id + request id -> (datetime, source, model, in, out, cache_write, cache_read)

    def refresh(self):
        for source, root in log_roots():
            self._scan(source, root)

    def _scan(self, source, root):
        for path in root.rglob("*.jsonl"):
            key = str(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            start = self.offsets.get(key, 0)
            if size < start:  # file was rewritten; read it again
                start = 0
            if size == start:
                continue
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read()
            # Only consume complete lines so a half-written line is retried next time
            end = data.rfind(b"\n") + 1
            self.offsets[key] = start + end
            for line in data[:end].splitlines():
                self._ingest(line, source)

    def _ingest(self, line, source):
        if b'"usage"' not in line:
            return
        try:
            d = json.loads(line)
        except ValueError:
            return
        msg = d.get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            return
        u = msg["usage"]
        ts = d.get("timestamp")
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        except ValueError:
            when = None
        if when is None:
            return
        # Streaming writes one line per content block with the same id; keep the latest
        rid = f'{msg.get("id")}|{d.get("requestId")}' if msg.get("id") else d.get("uuid")
        self.records[rid] = (
            when,
            source,
            (msg.get("model") or "").lower(),
            u.get("input_tokens") or 0,
            u.get("output_tokens") or 0,
            u.get("cache_creation_input_tokens") or 0,
            u.get("cache_read_input_tokens") or 0,
        )

    def totals(self, period, cfg):
        now = datetime.now().astimezone()
        if period == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "7d":
            since = now - timedelta(days=7)
        elif period == "30d":
            since = now - timedelta(days=30)
        else:
            since = datetime.min.replace(tzinfo=timezone.utc)

        e = cfg["energy_wh_per_token"]
        mults = cfg["model_multipliers"]
        t = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "responses": 0, "wh_it": 0.0, "code": 0, "cowork": 0}
        for when, source, model, i, o, cw, cr in self.records.values():
            if when < since or model == "<synthetic>":
                continue
            m = next((v for k, v in mults.items() if k != "default" and k in model), mults["default"])
            t["input"] += i
            t["output"] += o
            t["cache_write"] += cw
            t["cache_read"] += cr
            t["responses"] += 1
            t[source] += i + o + cw + cr
            t["wh_it"] += m * (i * e["input"] + o * e["output"] + cw * e["cache_write"] + cr * e["cache_read"])

        wh = t["wh_it"] * cfg["pue"]
        kwh = wh / 1000
        t["total"] = t["input"] + t["output"] + t["cache_write"] + t["cache_read"]
        t["wh"] = wh
        t["g_co2"] = kwh * cfg["grid_kg_co2_per_kwh"] * 1000
        t["liters"] = kwh * (cfg["water_onsite_l_per_kwh"] + cfg["water_offsite_l_per_kwh"])
        return t


def fmt_tokens(n):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}{suf}"
    return str(n)


def fmt_num(x):
    if x >= 100:
        return f"{x:,.0f}"
    if x >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


def fmt_energy(wh):
    return (fmt_num(wh / 1000), "kWh") if wh >= 1000 else (fmt_num(wh), "Wh")


def fmt_carbon(g):
    return (fmt_num(g / 1000), "kg CO₂e") if g >= 1000 else (fmt_num(g), "g CO₂e")


def fmt_water(l):
    return (fmt_num(l), "L") if l >= 1 else (fmt_num(l * 1000), "mL")


class Widget:
    def __init__(self):
        prepare_data_dir()
        self.cfg = load_config()
        self.state = load_json(STATE_PATH, {})
        self.period = self.state.get("period", "today")
        self.reader = UsageReader()

        try:  # crisp text and correct sizing on high-DPI displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        root = self.root = tk.Tk()
        self.scale = root.winfo_fpixels("1i") / 96
        root.title("Token Footprint")
        if ICON_PATH.exists():
            root.iconbitmap(default=str(ICON_PATH))
        root.overrideredirect(True)
        root.configure(bg=BG)
        root.attributes("-topmost", self.state.get("topmost", True))
        root.attributes("-alpha", 0.95)
        self._build()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = self.state.get("x"), self.state.get("y")
        if x is None:
            x, y = sw - w - int(40 * self.scale), int(60 * self.scale)
        x, y = max(0, min(x, sw - w)), max(0, min(y, sh - h))  # never start off-screen
        root.geometry(f"+{x}+{y}")
        self._bind_drag(root)
        root.bind("<Button-3>", self._menu)
        self.tick()
        if FROZEN and not self.state.get("offered_shortcut"):
            root.after(800, self.first_run)

    def first_run(self):
        self.state["offered_shortcut"] = True
        self.save_state()
        if messagebox.askyesno(
            "Token Footprint",
            "Add a Token Footprint shortcut to your desktop?\n\n"
            "Tip: right-click the widget any time for more options, including Start with Windows.",
            parent=self.root,
        ):
            make_shortcut("Token Footprint.lnk", desktop=True)

    # ---------- layout ----------
    def _build(self):
        r = self.root
        outer = tk.Frame(r, bg=BG, padx=14, pady=12)
        outer.pack(fill="both", expand=True)
        self.outer = outer

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="AI Footprint", fg=TEXT, bg=BG, font=(FONT, 11, "bold")).pack(side="left")
        close = tk.Label(head, text="✕", fg=MUTED, bg=BG, font=(FONT, 10), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.quit())

        tabs = tk.Frame(outer, bg=BG)
        tabs.pack(fill="x", pady=(8, 10))
        self.tab_labels = {}
        for label, key in PERIODS:
            t = tk.Label(tabs, text=label, bg=BG, font=(FONT, 9), padx=7, pady=2, cursor="hand2")
            t.pack(side="left", padx=(0, 4))
            t.bind("<Button-1>", lambda e, k=key: self.set_period(k))
            self.tab_labels[key] = t

        # Token summary
        tok = tk.Frame(outer, bg=CARD, padx=12, pady=8)
        tok.pack(fill="x")
        self.tok_value = tk.Label(tok, fg=TOKEN_C, bg=CARD, font=(FONT, 20, "bold"), anchor="w")
        self.tok_value.pack(fill="x")
        self.tok_sub = tk.Label(tok, fg=MUTED, bg=CARD, font=(FONT, 8), anchor="w", justify="left")
        self.tok_sub.pack(fill="x")

        self.rows = {}
        for key, title, color in (
            ("power", "ELECTRICITY", POWER_C),
            ("carbon", "CARBON", CARBON_C),
            ("water", "WATER", WATER_C),
        ):
            row = tk.Frame(outer, bg=CARD)
            row.pack(fill="x", pady=(6, 0))
            tk.Frame(row, bg=color, width=4).pack(side="left", fill="y")
            body = tk.Frame(row, bg=CARD, padx=10, pady=6)
            body.pack(side="left", fill="x", expand=True)
            tk.Label(body, text=title, fg=MUTED, bg=CARD, font=(FONT, 7, "bold"), anchor="w").pack(fill="x")
            line = tk.Frame(body, bg=CARD)
            line.pack(fill="x")
            val = tk.Label(line, fg=color, bg=CARD, font=(FONT, 15, "bold"))
            val.pack(side="left")
            unit = tk.Label(line, fg=TEXT, bg=CARD, font=(FONT, 9))
            unit.pack(side="left", padx=(4, 0), pady=(5, 0))
            eq = tk.Label(body, fg=MUTED, bg=CARD, font=(FONT, 8), anchor="w")
            eq.pack(fill="x")
            self.rows[key] = (val, unit, eq)

        self.footer = tk.Label(outer, fg=MUTED, bg=BG, font=(FONT, 7), anchor="w", justify="left")
        self.footer.pack(fill="x", pady=(8, 0))
        tk.Frame(outer, bg=BG, width=int(262 * self.scale), height=0).pack()  # fixed min width
        self.root.update_idletasks()

    def _bind_drag(self, widget):
        widget.bind("<ButtonPress-1>", self._drag_start, add="+")
        widget.bind("<B1-Motion>", self._drag_move, add="+")
        widget.bind("<ButtonRelease-1>", lambda e: self.save_state(), add="+")
        for child in widget.winfo_children():
            self._bind_drag(child)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---------- behavior ----------
    def set_period(self, key):
        self.period = key
        self.save_state()
        self.render()

    def tick(self):
        self.cfg = load_config()
        try:
            self.reader.refresh()
        except Exception as ex:  # keep the widget alive on odd files
            if sys.stderr:  # None in a windowed .exe
                print(ex, file=sys.stderr)
        self.render()
        self.root.after(int(self.cfg["refresh_seconds"] * 1000), self.tick)

    def render(self):
        for key, lbl in self.tab_labels.items():
            active = key == self.period
            lbl.configure(bg=TOKEN_C if active else CARD, fg=BG if active else MUTED)

        t = self.reader.totals(self.period, self.cfg)
        eq = self.cfg["equivalents"]
        self.tok_value.configure(text=f"{fmt_tokens(t['total'])} tokens")
        self.tok_sub.configure(
            text=f"{fmt_tokens(t['output'])} out · {fmt_tokens(t['input'] + t['cache_write'])} in · "
                 f"{fmt_tokens(t['cache_read'])} cached · {t['responses']:,} replies\n"
                 f"Code {fmt_tokens(t['code'])} · Cowork {fmt_tokens(t['cowork'])}"
        )

        v, u = fmt_energy(t["wh"])
        self._set("power", v, u, f"≈ {fmt_num(t['wh'] / eq['wh_per_phone_charge'])} phone charges")
        v, u = fmt_carbon(t["g_co2"])
        self._set("carbon", v, u, f"≈ {fmt_num(t['g_co2'] / eq['g_co2_per_mile_driven'])} miles driven in a car")
        v, u = fmt_water(t["liters"])
        self._set("water", v, u, f"≈ {fmt_num(t['liters'] / eq['liters_per_water_bottle'])} bottles of water (16.9 oz)")

        if not self.reader.records:
            self.footer.configure(text="No Claude Code or Cowork usage found yet.\nIt will appear here after your first session.")
            return
        self.footer.configure(
            text=f"Claude Code + Cowork · updated {datetime.now():%I:%M %p}".replace(" 0", " ")
                 + "\nEstimates, not measurements · right-click for options"
        )

    def _set(self, key, value, unit, eq_text):
        val, unit_lbl, eq = self.rows[key]
        val.configure(text=value)
        unit_lbl.configure(text=unit)
        eq.configure(text=eq_text)

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Refresh now", command=self.tick_once)
        topmost = bool(self.root.attributes("-topmost"))
        m.add_command(label=("✓ " if topmost else "    ") + "Always on top", command=self.toggle_topmost)
        m.add_command(label=("✓ " if STARTUP_LNK.exists() else "    ") + "Start with Windows", command=self.toggle_startup)
        m.add_separator()
        m.add_command(label="Edit conversion factors…", command=lambda: os.startfile(CONFIG_PATH))
        m.add_command(label="How the estimates work", command=lambda: os.startfile(README_PATH))
        m.add_command(label="Add desktop shortcut", command=lambda: make_shortcut("Token Footprint.lnk", desktop=True))
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def tick_once(self):
        self.cfg = load_config()
        self.reader.refresh()
        self.render()

    def toggle_topmost(self):
        self.root.attributes("-topmost", not self.root.attributes("-topmost"))
        self.save_state()

    def toggle_startup(self):
        if STARTUP_LNK.exists():
            STARTUP_LNK.unlink()
        else:
            make_shortcut(STARTUP_LNK)

    def save_state(self):
        self.state.update(
            x=self.root.winfo_x(), y=self.root.winfo_y(),
            period=self.period, topmost=bool(self.root.attributes("-topmost")),
        )
        try:
            STATE_PATH.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError:
            pass

    def quit(self):
        self.save_state()
        self.root.destroy()


def already_running():
    """Hold a named mutex so a second launch (e.g. from the shortcut) just exits."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        already_running.handle = k32.CreateMutexW(None, False, "Local\\TokenFootprintWidget")
        return k32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


if __name__ == "__main__":
    if not already_running():
        Widget().root.mainloop()
