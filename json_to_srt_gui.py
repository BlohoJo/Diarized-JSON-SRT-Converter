#!/usr/bin/env python3
"""
json_to_srt_gui.py - Windows-friendly tkinter front-end for json_to_srt_core.

* Input / output file pickers (output defaults to <input>.srt)
* Every converter setting visible and editable, grouped in tabs
* Convert button with live log / summary
* Settings persisted to %LOCALAPPDATA%\\JSONtoSRT\\settings.json
  (~/.config/JSONtoSRT on Linux, ~/Library/Application Support/JSONtoSRT on macOS)
* "Reset to Defaults" with confirmation, named presets (save / load / delete)

Runs with stock Python (tkinter ships with the python.org Windows installer).
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Optional

# Allow running from a PyInstaller bundle or from the source folder.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json_to_srt_core as core  # noqa: E402

WINDOW_TITLE = f"JSON to SRT converter  v{core.__version__}"


# --------------------------------------------------------------------------------------
# persistence helpers
# --------------------------------------------------------------------------------------

def _safe_preset_name(name: str) -> str:
    name = re.sub(r"[^\w\- ]+", "_", name.strip())
    return name[:64]


class Store:
    """settings.json + presets/*.json under the config folder."""

    def __init__(self) -> None:
        self.dir = core.config_dir()
        self.presets = core.presets_dir()
        os.makedirs(self.presets, exist_ok=True)

    def load_settings(self) -> Dict[str, Any]:
        try:
            with open(core.settings_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_settings(self, data: Dict[str, Any]) -> None:
        os.makedirs(self.dir, exist_ok=True)
        with open(core.settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def preset_names(self):
        try:
            return sorted(os.path.splitext(n)[0] for n in os.listdir(self.presets) if n.endswith(".json"))
        except OSError:
            return []

    def preset_path(self, name: str) -> str:
        return os.path.join(self.presets, _safe_preset_name(name) + ".json")

    def save_preset(self, name: str, settings: Dict[str, Any]) -> None:
        with open(self.preset_path(name), "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)

    def load_preset(self, name: str) -> Dict[str, Any]:
        with open(self.preset_path(name), "r", encoding="utf-8") as f:
            return json.load(f)

    def delete_preset(self, name: str) -> None:
        try:
            os.remove(self.preset_path(name))
        except OSError:
            pass


# --------------------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(820, 640)
        self.store = Store()
        self.vars: Dict[str, tk.Variable] = {}
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None

        self._style()
        self._build()
        self._load_persisted()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log)

    # --- look & feel -----------------------------------------------------------------
    def _style(self) -> None:
        style = ttk.Style(self)
        if sys.platform.startswith("win") and "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names() and not sys.platform.startswith("win"):
            style.theme_use("clam")
        try:
            self.tk.call("tk", "scaling", 1.0 if not sys.platform.startswith("win") else self.tk.call("tk", "scaling"))
        except tk.TclError:
            pass

    # --- layout ------------------------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 6, "pady": 3}
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # files
        files = ttk.LabelFrame(root, text="Files", padding=6)
        files.pack(fill="x")
        self.in_var = tk.StringVar()
        self.out_var = tk.StringVar()
        ttk.Label(files, text="Input JSON:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(files, textvariable=self.in_var, width=80).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(files, text="Browse...", command=self._pick_input).grid(row=0, column=2, **pad)
        ttk.Label(files, text="Output SRT:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(files, textvariable=self.out_var, width=80).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(files, text="Browse...", command=self._pick_output).grid(row=1, column=2, **pad)
        files.columnconfigure(1, weight=1)
        self.in_var.trace_add("write", lambda *_: self._default_output())

        # presets row
        presets = ttk.LabelFrame(root, text="Presets", padding=6)
        presets.pack(fill="x", pady=(6, 0))
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(presets, textvariable=self.preset_var, width=32, state="normal")
        self.preset_box.grid(row=0, column=0, **pad)
        ttk.Button(presets, text="Load", command=self._load_preset).grid(row=0, column=1, **pad)
        ttk.Button(presets, text="Save as...", command=self._save_preset).grid(row=0, column=2, **pad)
        ttk.Button(presets, text="Delete", command=self._delete_preset).grid(row=0, column=3, **pad)
        ttk.Button(presets, text="Reset to Defaults", command=self._reset_defaults).grid(row=0, column=4, padx=(24, 6))
        ttk.Button(presets, text="Open settings folder", command=self._open_folder).grid(row=0, column=5, **pad)
        self._refresh_presets()

        # settings tabs
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=False, pady=(6, 0))
        tabs = {g: ttk.Frame(nb, padding=8) for g in core.SETTINGS_GROUPS}
        for g, fr in tabs.items():
            nb.add(fr, text=g)
        rows = {g: 0 for g in tabs}
        for key, typ, default, label, group, help_ in core.SETTINGS_SPEC:
            fr = tabs[group]
            r = rows[group]
            # two columns of settings per tab
            col = 0 if r % 2 == 0 else 3
            rr = r // 2
            ttk.Label(fr, text=label + ":").grid(row=rr, column=col, sticky="w", **pad)
            widget = self._make_widget(fr, key, typ, default)
            widget.grid(row=rr, column=col + 1, sticky="w", **pad)
            self._tooltip(widget, help_)
            rows[group] += 1
            fr.columnconfigure(2, minsize=30)

        # actions + log
        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(8, 0))
        self.convert_btn = ttk.Button(actions, text="Convert", command=self._convert)
        self.convert_btn.pack(side="left")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(actions, textvariable=self.status_var).pack(side="left", padx=12)
        ttk.Button(actions, text="Clear log", command=lambda: self.log.delete("1.0", "end")).pack(side="right")

        logf = ttk.LabelFrame(root, text="Log", padding=4)
        logf.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(logf, height=12, wrap="word", font=("Consolas", 9) if sys.platform.startswith("win") else None)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _make_widget(self, parent, key, typ, default):
        if typ == "bool":
            var = tk.BooleanVar(value=default)
            w = ttk.Checkbutton(parent, variable=var)
        elif isinstance(typ, tuple) and typ[0] == "choice":
            var = tk.StringVar(value=default)
            w = ttk.Combobox(parent, textvariable=var, values=typ[1], state="readonly", width=22)
        elif typ == "str":
            var = tk.StringVar(value=default)
            w = ttk.Entry(parent, textvariable=var, width=34)
        else:  # int / float
            var = tk.StringVar(value=str(default))
            w = ttk.Spinbox(parent, textvariable=var, width=10, from_=0, to=1_000_000,
                            increment=1 if typ == "int" else 0.5)
        self.vars[key] = var
        return w

    # simple tooltip --------------------------------------------------------------------
    def _tooltip(self, widget, text: str) -> None:
        tip = {"win": None}

        def show(_e):
            if tip["win"] or not text:
                return
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, justify="left", background="#ffffe0", relief="solid",
                     borderwidth=1, wraplength=380, padx=6, pady=3).pack()
            tip["win"] = tw

        def hide(_e):
            if tip["win"]:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # --- settings <-> widgets --------------------------------------------------------
    def current_settings(self) -> Dict[str, Any]:
        raw = {k: v.get() for k, v in self.vars.items()}
        settings = core.default_settings()
        errors = []
        for key, typ, default, label, *_ in core.SETTINGS_SPEC:
            try:
                settings[key] = core.coerce_setting(key, raw[key])
            except (ValueError, TypeError):
                errors.append(f"{label}: invalid value '{raw[key]}'")
        if errors:
            raise ValueError("\n".join(errors))
        return settings

    def apply_settings(self, settings: Dict[str, Any]) -> None:
        settings = core.normalize_settings(settings)
        for key, typ, *_ in core.SETTINGS_SPEC:
            v = settings[key]
            self.vars[key].set(v if typ == "bool" else str(v))

    def _load_persisted(self) -> None:
        data = self.store.load_settings()
        if data:
            self.apply_settings(data.get("settings", data))
            self.in_var.set(data.get("last_input", ""))
            if data.get("last_output"):
                self.out_var.set(data["last_output"])
            self._log(f"Loaded settings from {core.settings_path()}")
        else:
            self._log("Using default settings (Subtitle Edit profile).")

    def _persist(self) -> None:
        try:
            settings = self.current_settings()
        except ValueError:
            settings = core.default_settings()
        self.store.save_settings({"settings": settings, "last_input": self.in_var.get(),
                                  "last_output": self.out_var.get()})

    # --- file pickers ----------------------------------------------------------------
    def _pick_input(self) -> None:
        p = filedialog.askopenfilename(title="Choose transcription JSON",
                                       filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if p:
            self.in_var.set(p)

    def _pick_output(self) -> None:
        init = self.out_var.get() or self._default_output_path()
        p = filedialog.asksaveasfilename(title="Save SRT as", defaultextension=".srt",
                                         initialfile=os.path.basename(init) if init else "",
                                         initialdir=os.path.dirname(init) if init else "",
                                         filetypes=[("SubRip subtitles", "*.srt"), ("All files", "*.*")])
        if p:
            self.out_var.set(p)

    def _default_output_path(self) -> str:
        i = self.in_var.get().strip()
        return os.path.splitext(i)[0] + ".srt" if i else ""

    def _default_output(self) -> None:
        self.out_var.set(self._default_output_path())

    # --- presets --------------------------------------------------------------------------
    def _refresh_presets(self) -> None:
        self.preset_box["values"] = self.store.preset_names()

    def _save_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            name = self._ask_string("Save preset", "Preset name:")
        if not name:
            return
        if name in self.store.preset_names() and not messagebox.askyesno(
                "Overwrite preset", f"Preset '{name}' exists. Overwrite?"):
            return
        try:
            self.store.save_preset(name, self.current_settings())
        except ValueError as e:
            messagebox.showerror("Invalid settings", str(e))
            return
        self.preset_var.set(name)
        self._refresh_presets()
        self._log(f"Saved preset '{name}'.")

    def _load_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name or name not in self.store.preset_names():
            messagebox.showinfo("Load preset", "Choose a preset from the list first.")
            return
        self.apply_settings(self.store.load_preset(name))
        self._log(f"Loaded preset '{name}'.")

    def _delete_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name or name not in self.store.preset_names():
            messagebox.showinfo("Delete preset", "Choose a preset from the list first.")
            return
        if messagebox.askyesno("Delete preset", f"Delete preset '{name}'? This cannot be undone."):
            self.store.delete_preset(name)
            self.preset_var.set("")
            self._refresh_presets()
            self._log(f"Deleted preset '{name}'.")

    def _reset_defaults(self) -> None:
        if messagebox.askyesno(
                "Reset to Defaults",
                "This will replace ALL current settings with the built-in defaults\n"
                "(Subtitle Edit profile: 43 chars, 25 CPS, 1000-8000 ms, 24 ms gap, 2 lines, ...).\n\n"
                "Saved presets are not affected. Continue?", icon="warning", default="no"):
            self.apply_settings(core.default_settings())
            self._log("Settings reset to defaults.")

    def _ask_string(self, title: str, prompt: str) -> Optional[str]:
        from tkinter import simpledialog
        return simpledialog.askstring(title, prompt, parent=self)

    def _open_folder(self) -> None:
        folder = self.store.dir
        os.makedirs(folder, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}" >/dev/null 2>&1 &')
        except OSError as e:
            messagebox.showerror("Open folder", str(e))
        self._log(f"Settings folder: {folder}")

    # --- conversion ----------------------------------------------------------------------
    def _convert(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        inp = self.in_var.get().strip()
        out = self.out_var.get().strip() or self._default_output_path()
        if not inp or not os.path.isfile(inp):
            messagebox.showerror("Input missing", "Please choose an existing input JSON file.")
            return
        try:
            settings = self.current_settings()
        except ValueError as e:
            messagebox.showerror("Invalid settings", str(e))
            return
        if os.path.exists(out) and not messagebox.askyesno("Overwrite?", f"{out}\nalready exists. Overwrite?"):
            return
        self._persist()
        self.convert_btn.state(["disabled"])
        self.status_var.set("Converting...")
        self._log(f"--- Converting {inp}")

        def work():
            try:
                _, report = core.convert_file(inp, out, settings, log=self.log_queue.put)
                self.log_queue.put(report.summary())
                self.log_queue.put(f"DONE -> {out}")
                self.log_queue.put("__STATUS__Done: " + os.path.basename(out))
            except Exception as e:  # surface anything to the log instead of crashing the GUI
                self.log_queue.put(f"ERROR: {e}")
                self.log_queue.put("__STATUS__Failed - see log")

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _drain_log(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg.startswith("__STATUS__"):
                    self.status_var.set(msg[len("__STATUS__"):])
                    self.convert_btn.state(["!disabled"])
                else:
                    self._log(msg)
        except queue.Empty:
            pass
        self.after(100, self._drain_log)

    def _log(self, msg: str) -> None:
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def _on_close(self) -> None:
        self._persist()
        self.destroy()


def main() -> None:
    app = App()
    # Optional: pass an input file on the command line / drag onto the .exe
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.in_var.set(sys.argv[1])
    app.mainloop()


if __name__ == "__main__":
    main()
