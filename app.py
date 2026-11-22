import re, threading, time  
from datetime import date, timedelta  
from pathlib import Path  
import numpy as np  
import pandas as pd  
import tkinter as tk  
from tkinter import ttk, filedialog, messagebox  
import ttkbootstrap as tb  
from ttkbootstrap.constants import *  
from ttkbootstrap import Button  
import matplotlib  
matplotlib.use("TkAgg")  
from matplotlib.figure import Figure  
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk  
import matplotlib.pyplot as plt  
import art  
try:  
    from PIL import Image, ImageDraw, ImageTk, ImageFilter  
    PIL_AVAILABLE = True  
except ImportError:  
    PIL_AVAILABLE = False  
  
from art import generate_art  
from colors import COLORS, get_bp_color  
from config import DATA_DIR, SETTINGS_PATH, load_settings, save_settings, TOKEN_DIR
from database import (init_db, db_load_garmin_data, db_get_bp, db_save_bp,  
                      db_get_last_date, db_set_meta, db_get_meta)  
from fit_reader import FIT_AVAILABLE, FitFile, read_fit_file  
from garmin_api import (GARMIN_AVAILABLE, garmin_login, fetch_full_history,  
                        fetch_incremental, save_to_db)  
from sleep_score import compute_sleep_quality_score 
from stats import compute_stats  
try:  
    import odf  
    ODS_AVAILABLE = True  
except ImportError:  
    ODS_AVAILABLE = False
from art import generate_art, PIL_AVAILABLE
  
# ═══════════════════════════════════════════════════════════════════════════════
#  GUI APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class GarminViewerApp:
    def __init__(self, root: tk.Tk):  
        self.root = root  
        self.garmin_client = None  
        self.garmin_data: dict = {}  
        self.bp_data: pd.DataFrame | None = None  
        self.progress_win = None  
        self.settings = load_settings()  
        init_db()  
        self._build_ui()  
        saved_theme = self.settings.get("theme", "darkly")  
        try:  
            self.root.set_theme(saved_theme)  
        except Exception:  
            pass  
        self._load_existing_data()  
        self._redraw_data_chart()  
        if self.settings.get("connect_on_start"):  
            self.root.after(500, self._on_connect_update)

    def _load_existing_data(self):  
        def _load_bg():  
            self.garmin_data = db_load_garmin_data()  
            n_days = len(self.garmin_data.get("daily_stats", []))  
            first = db_get_meta("first_download") or "?"  
            last = db_get_meta("last_update") or "?"  
            self.status_var.set(f"Loaded: {first} → {last} ({n_days} days)")  
            self._update_data_info()  
            self._redraw_data_chart()  
            df = db_get_bp()  
            if df is not None:  
                self.bp_data = df  
                n = len(df)  
                dmin = df["datetime"].min().strftime('%Y-%m-%d')  
                dmax = df["datetime"].max().strftime('%Y-%m-%d')  
                if hasattr(self, 'bp_tab_info_var'):  
                    self.bp_tab_info_var.set(f"Loaded {n} readings. Range: {dmin} → {dmax}")  
                self._populate_bp_table()  
                if self.data_chart_var.get() == "blood_pressure":  
                    self._redraw_data_chart()  
        self.root.after(100, _load_bg)  

    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tab_garmin = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_garmin, text="  Garmin  ")
        self._build_garmin_tab()
        self.tab_data = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_data, text="  Data Monitor  ")
        self._build_data_tab()
        self.tab_bp = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_bp, text="  Blood Pressure  ")
        self._build_bp_tab()
        self.tab_art = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_art, text="  Art  ")
        self._build_art_tab()
        self.tab_overview = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_overview, text="  Overview  ")
        self._build_overview_tab()
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    def _show_progress(self, title: str):
        self.progress_win = tk.Toplevel(self.root)
        self.progress_win.title(title)
        self.progress_win.geometry("550x180")
        self.progress_win.resizable(False, False)
        self.progress_win.transient(self.root)
        self.progress_win.grab_set()
        self.progress_label = tk.Label(self.progress_win, text="Starting…",
                                       font=("TkDefaultFont", 12), wraplength=500)
        self.progress_label.pack(pady=(20, 10), padx=20)
        self.progress_bar = ttk.Progressbar(self.progress_win, mode="indeterminate", length=450)
        self.progress_bar.pack(pady=10, padx=20)
        self.progress_bar.start(10)
        self.progress_detail = tk.Label(self.progress_win, text="",
                                        font=("TkDefaultFont", 9), wraplength=500, fg="gray")
        self.progress_detail.pack(pady=(5, 15), padx=20)
        self.progress_win.update()

    def _update_progress(self, msg: str):
        def _do():
            if self.progress_win and self.progress_win.winfo_exists():
                short = msg.split("→")[-1].strip() if "→" in msg else msg
                self.progress_label.config(text=short)
                self.progress_detail.config(text=msg)
                self.progress_win.update_idletasks()
        self.root.after(0, _do)

    def _close_progress(self):
        def _do():
            if self.progress_win and self.progress_win.winfo_exists():
                self.progress_win.destroy()
            self.progress_win = None
        self.root.after(0, _do)

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self.status_var.set(msg))
        self._update_progress(msg)

    def _prompt_mfa(self) -> str:
        result = [""]
        def _do():
            win = tk.Toplevel(self.root)
            win.title("MFA Code Required")
            win.geometry("300x130")
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text="Enter MFA code:").pack(pady=5)
            entry = tk.Entry(win, width=20)
            entry.pack(pady=5)
            entry.focus_set()
            def _ok():
                result[0] = entry.get().strip()
                win.destroy()
            Button(win, text="OK", command=_ok).pack(pady=5)
        self.root.after(0, _do)
        while not result[0] and self.root.winfo_exists():
            self.root.update()
            time.sleep(0.1)
        return result[0]

    def _apply_theme(self):
        theme = self.theme_var.get()
        try:
            self.root.style.theme_use(theme)
            self.settings["theme"] = theme
            save_settings(self.settings)
        except Exception as e:
            messagebox.showerror("Theme Error", f"Could not apply theme:\n{e}")
        self._redraw_data_chart()

    def _toggle_dark(self):
        current = self.theme_var.get()
        dark_themes = {"darkly", "superhero", "solar", "cyborg"}
        new_theme = "flatly" if current in dark_themes else "darkly"
        try:
            self.root.style.theme_use(new_theme)
            self.theme_var.set(new_theme)
            self.settings["theme"] = new_theme
            save_settings(self.settings)
        except Exception:
            pass
        self._redraw_data_chart()

    def _get_chart_colors(self) -> dict:
        dark_themes = {"darkly", "superhero", "solar", "cyborg"}
        current = self.theme_var.get() if hasattr(self, 'theme_var') else "darkly"
        if current in dark_themes:
            return {"fig_bg": "#3a3a3a", "ax_bg": "#3a3a3a", "text": "#e0e0e0",
                    "grid": "#666666", "spine": "#666666", "legend_bg": "#4a4a4a"}
        return {"fig_bg": "#ffffff", "ax_bg": "#ffffff", "text": "#222222",
                "grid": "#cccccc", "spine": "#cccccc", "legend_bg": "#ffffff"}

    def _style_figure(self, fig, ax, twin_ax=None):
        c = self._get_chart_colors()
        fig.set_facecolor(c["fig_bg"])
        ax.set_facecolor(c["ax_bg"])
        ax.tick_params(colors=c["text"], which="both", labelsize=11)
        ax.xaxis.label.set_color(c["text"])
        ax.yaxis.label.set_color(c["text"])
        ax.title.set_color(c["text"])
        for spine in ax.spines.values():
            spine.set_color(c["spine"])
        ax.grid(True, alpha=0.3, color=c["grid"])
        if twin_ax is not None:
            twin_ax.set_facecolor(c["ax_bg"])
            twin_ax.tick_params(colors=c["text"], which="both", labelsize=11)
            twin_ax.yaxis.label.set_color(c["text"])
            for spine in twin_ax.spines.values():
                spine.set_color(c["spine"])
        sb = getattr(self, 'xscrollbar_data', None)
        if sb is not None: 
            sb.config(troughcolor=c["fig_bg"], background=c["spine"], activebackground=c["text"])   

    # ─── Garmin Tab ─────────────────────────────────────────────────────────

    def _build_garmin_tab(self):  
        frame = ttk.Frame(self.tab_garmin)  
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)  
  
        # ─── Theme ──────────────────────────────────────────────────────  
        theme_frame = ttk.LabelFrame(frame, text="Theme")  
        theme_frame.pack(fill=tk.X, padx=5, pady=10)  
        ttk.Label(theme_frame, text="Theme:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)  
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "darkly"))  
        themes = ["darkly", "superhero", "solar", "cyborg",  
                  "cosmo", "flatly", "journal", "litera",  
                  "lumen", "minty", "pulse", "sandstone", "united", "yeti"]  
        self.theme_combo = ttk.Combobox(theme_frame, textvariable=self.theme_var,  
                                        values=themes, width=18, state="readonly")  
        self.theme_combo.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)  
        Button(theme_frame, text="Apply", bootstyle="primary",  
               command=self._apply_theme).grid(row=0, column=2, padx=5)  
        Button(theme_frame, text="Dark/Bright Toggle", bootstyle="secondary",  
               command=self._toggle_dark).grid(row=0, column=3, padx=5)  
  
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)  
  
        # ─── Garmin Connect Login ───────────────────────────────────────  
        login_frame = ttk.LabelFrame(frame, text="Garmin Connect Login")  
        login_frame.pack(fill=tk.X, padx=5, pady=10)  
        ttk.Label(login_frame, text="Email:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)  
        self.email_var = tk.StringVar(value=self.settings.get("email", ""))  
        ttk.Entry(login_frame, textvariable=self.email_var, width=40).grid(  
            row=0, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)  
        ttk.Label(login_frame, text="Password:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)  
        self.password_var = tk.StringVar(value=self.settings.get("password", ""))  
        ttk.Entry(login_frame, textvariable=self.password_var, width=40,  
                  show="*").grid(row=1, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)  
        ttk.Label(login_frame, text="API Token:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)  
        self.api_token_var = tk.StringVar(value=self.settings.get("api_token", str(TOKEN_DIR)))
        ttk.Entry(login_frame, textvariable=self.api_token_var, width=40).grid(  
            row=2, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)  
        self.connect_on_start_var = tk.BooleanVar(  
            value=self.settings.get("connect_on_start", False))  
        ttk.Checkbutton(login_frame, text="  Connect on Start",  
                        variable=self.connect_on_start_var).grid(  
            row=3, column=1, padx=5, pady=5, sticky=tk.W)  
        self.connect_status_var = tk.StringVar(value="⚪ Not Connected")  
        ttk.Label(login_frame, textvariable=self.connect_status_var,  
                  foreground="gray").grid(  
            row=4, column=1, padx=5, pady=(0, 5), sticky=tk.W)
  
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)  
  
        # ─── Garmin Local ───────────────────────────────────────────────  
        local_frame = ttk.LabelFrame(frame, text="Garmin Local")  
        local_frame.pack(fill=tk.X, padx=5, pady=10)  
        ttk.Label(local_frame, text=" Path: ").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)  
        self.local_fit_path_var = tk.StringVar(value=self.settings.get("local_fit_path", ""))  
        ttk.Entry(local_frame, textvariable=self.local_fit_path_var, width=40).grid(  
            row=0, column=1, padx=5, pady=5, sticky=tk.EW)  
        Button(local_frame, text=" Browse… ",  
               command=self._browse_local_fit_path).grid(row=0, column=2, padx=5)  
        Button(local_frame, text="⬇ Import FIT Files", bootstyle="primary",  
               command=self._on_local_fit_import).grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)  
        Button(local_frame, text=" Save to DB ", bootstyle="success",  
               command=self._on_local_save_db).grid(row=1, column=1, padx=5, pady=5, sticky=tk.E)  
        self.local_fit_info_var = tk.StringVar(value="")  
        ttk.Label(local_frame, textvariable=self.local_fit_info_var).grid(  
            row=1, column=2, padx=5, pady=5, sticky=tk.W)  
        local_frame.columnconfigure(1, weight=1)  
  
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)  
  
        # ─── Sleep Time Range ───────────────────────────────────────────  
        sleep_frame = ttk.LabelFrame(frame, text="Sleep Time Range")  
        sleep_frame.pack(fill=tk.X, padx=5, pady=10)  
        ttk.Label(sleep_frame, text="Start:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)  
        self.sleep_start_var = tk.StringVar(value=self.settings.get("sleep_start", "22:00"))  
        ttk.Entry(sleep_frame, textvariable=self.sleep_start_var, width=8).grid(  
            row=0, column=1, padx=5, pady=5, sticky=tk.W)  
        ttk.Label(sleep_frame, text="End:").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)  
        self.sleep_end_var = tk.StringVar(value=self.settings.get("sleep_end", "07:00"))  
        ttk.Entry(sleep_frame, textvariable=self.sleep_end_var, width=8).grid(  
            row=0, column=3, padx=5, pady=5, sticky=tk.W)  
        ttk.Label(sleep_frame, text="(e.g. 22:00 → 07:00)",  
                  foreground="gray").grid(row=1, column=0, columnspan=4, padx=5, pady=2, sticky=tk.W)  
        ttk.Label(sleep_frame, text="Age:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)  
        self.age_var = tk.StringVar(value=str(self.settings.get("age", 30)))  
        ttk.Entry(sleep_frame, textvariable=self.age_var, width=5).grid(  
            row=2, column=1, padx=5, pady=5, sticky=tk.W)  
        ttk.Label(sleep_frame, text="(used for sleep quality scoring)",  
                  foreground="gray").grid(row=2, column=2, columnspan=2, padx=5, pady=2, sticky=tk.W)  
  
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)  
  
        # ─── Blood Pressure ─────────────────────────────────────────────  
        bp_frame = ttk.LabelFrame(frame, text="Blood Pressure")  
        bp_frame.pack(fill=tk.X, padx=5, pady=10)  
        self.bp_2x2_var = tk.BooleanVar(  
            value=self.settings.get("bp_2x2_measure", False))  
        ttk.Checkbutton(bp_frame, text="  2x2 Measure (Morning 2x, Evening 2x)",  
                        variable=self.bp_2x2_var).grid(  
            row=0, column=0, columnspan=3, padx=5, pady=5, sticky=tk.W)  
        ttk.Label(bp_frame, text="Time between the 2 measures (min):").grid(  
            row=1, column=0, padx=5, pady=5, sticky=tk.W)  
        self.bp_gap_var = tk.StringVar(  
            value=str(self.settings.get("bp_measure_gap_min", 5)))  
        ttk.Entry(bp_frame, textvariable=self.bp_gap_var, width=5).grid(  
            row=1, column=1, padx=5, pady=5, sticky=tk.W)
  
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)  
  
        # ─── Save Settings ──────────────────────────────────────────────  
        Button(frame, text="Save Settings", bootstyle="success",  
               command=self._save_settings).pack(anchor=tk.W, padx=5, pady=10)

    def _on_local_save_db(self):
        if not self.garmin_data.get("daily_stats") and not self.garmin_data.get("activities"):
            messagebox.showinfo("Info", "No data to save. Import first.")
            return
        self.status_var.set("Saving to database…")
        self.root.update()

        def work():
            try:
                save_to_db(self.garmin_data)
                db_set_meta("first_download", db_get_meta("first_download") or date.today().isoformat())
                db_set_meta("last_update", date.today().isoformat())
                self.garmin_data = db_load_garmin_data()
                n_days = len(self.garmin_data.get("daily_stats", []))
                n_acts = len(self.garmin_data.get("activities", []))
                dates = [d["date"] for d in self.garmin_data.get("daily_stats", []) if d.get("date")]
                d_min = min(dates) if dates else "?"
                d_max = max(dates) if dates else "?"
                msg = f"✅ Data saved to database  ({n_days} days: {d_min} → {d_max}, {n_acts} activities)"
            except Exception as e:
                msg = f"❌ Save failed: {str(e)}"
            self.root.after(0, lambda: self.local_fit_info_var.set(msg))
            self.root.after(0, lambda: self.status_var.set(msg))
            self.root.after(0, self._update_data_info)
            self.root.after(0, self._redraw_data_chart)

        threading.Thread(target=work, daemon=True).start()

    def _save_settings(self):  
        self.settings = {  
            "email": self.email_var.get().strip(),  
            "password": self.password_var.get().strip(),  
            "local_fit_path": self.local_fit_path_var.get().strip(),  
            "api_token": self.api_token_var.get().strip(),  
            "connect_on_start": self.connect_on_start_var.get(),   # NEW  
            "sleep_start": self.sleep_start_var.get().strip(),  
            "sleep_end": self.sleep_end_var.get().strip(),  
            "age": int(self.age_var.get().strip() or 30),  
            "theme": self.theme_var.get().strip(),              
            "bp_2x2_measure": self.bp_2x2_var.get(),  
            "bp_measure_gap_min": int(self.bp_gap_var.get().strip() or 5),
            
        }  
        save_settings(self.settings)  
        self.status_var.set(f"Settings saved to {SETTINGS_PATH}")

    def _browse_local_fit_path(self):
        path = filedialog.askdirectory(title="Select Garmin Export Drive/Folder")
        if path:
            self.local_fit_path_var.set(path)

    def _on_local_fit_import(self):
        if not FIT_AVAILABLE:
            messagebox.showerror("Error", "fitparse not installed.")
            return
        path = self.local_fit_path_var.get().strip()
        if not path:
            messagebox.showwarning("Warning", "Enter a path first.")
            return
        if not Path(path).expanduser().exists():
            messagebox.showerror("Error", f"Path does not exist:\n{path}")
            return

        self._show_progress("Garmin Local – Import")
        self._set_status("🔍 Scanning local drive…")

        def work():
            try:
                from collections import defaultdict
                root = Path(path).expanduser()
                data = db_load_garmin_data()

                monitor_dir = root / "Monitor"
                n_daily = n_hr = n_stress = n_resp = 0
                if monitor_dir.exists():
                    for ff in sorted(monitor_dir.glob("*.fit")):
                        try:
                            fit = FitFile(str(ff))
                            daily_msgs, hr_msgs, stress_msgs, resp_msgs = [], [], [], []

                            for msg in fit.get_messages("daily_activity_summary"):
                                d_str, steps, dist_m, cal, act_cal = None, None, None, None, None
                                for field in msg:
                                    if field.name in ("local_timestamp", "timestamp"):
                                        try:
                                            d_str = pd.Timestamp(field.value).strftime("%Y-%m-%d")
                                        except Exception:
                                            pass
                                    elif field.name == "total_steps":
                                        steps = field.value
                                    elif field.name == "distance":
                                        dist_m = field.value
                                    elif field.name in ("total_calories", "kilocalories"):
                                        cal = field.value
                                    elif field.name in ("active_calories", "active_kilocalories"):
                                        act_cal = field.value
                                if d_str and steps is not None:
                                    daily_msgs.append({"date": d_str, "steps": steps,
                                        "distance_km": (dist_m or 0) / 1000,
                                        "calories": cal or 0, "active_calories": act_cal or 0})

                            for msg in fit.get_messages("hr"):
                                d_str, hr_val = None, None
                                for field in msg:
                                    if field.name in ("local_timestamp", "timestamp"):
                                        try:
                                            d_str = pd.Timestamp(field.value).strftime("%Y-%m-%d")
                                        except Exception:
                                            pass
                                    elif field.name == "hr":
                                        hr_val = field.value
                                if d_str and hr_val:
                                    hr_msgs.append({"date": d_str, "hr": hr_val})

                            for msg in fit.get_messages("stress"):
                                d_str, stress_val = None, None
                                for field in msg:
                                    if field.name in ("local_timestamp", "timestamp"):
                                        try:
                                            d_str = pd.Timestamp(field.value).strftime("%Y-%m-%d")
                                        except Exception:
                                            pass
                                    elif field.name == "stress":
                                        stress_val = field.value
                                if d_str and stress_val is not None:
                                    stress_msgs.append({"date": d_str, "stress": stress_val})

                            for msg in fit.get_messages("respiration_rate"):
                                d_str, resp_val = None, None
                                for field in msg:
                                    if field.name in ("local_timestamp", "timestamp"):
                                        try:
                                            d_str = pd.Timestamp(field.value).strftime("%Y-%m-%d")
                                        except Exception:
                                            pass
                                    elif field.name in ("res_rate", "respiration"):
                                        resp_val = field.value
                                if d_str and resp_val is not None:
                                    resp_msgs.append({"date": d_str, "resp": resp_val})

                            if daily_msgs:
                                existing = {e["date"]: e for e in data.get("daily_stats", [])}
                                for dm in daily_msgs:
                                    if dm["date"] not in existing or existing[dm["date"]]["steps"] == 0:
                                        existing[dm["date"]] = dm
                                data["daily_stats"] = sorted(existing.values(), key=lambda x: x["date"])
                                n_daily += len(daily_msgs)

                            if hr_msgs:
                                by_day = defaultdict(list)
                                for hm in hr_msgs:
                                    by_day[hm["date"]].append(hm["hr"])
                                existing = {e["date"]: e for e in data.get("heart_rate", [])}
                                for d, vals in by_day.items():
                                    if d not in existing:
                                        existing[d] = {"date": d, "resting_hr": int(min(vals)),
                                                       "max_hr": int(max(vals)), "min_hr": int(min(vals))}
                                data["heart_rate"] = sorted(existing.values(), key=lambda x: x["date"])
                                n_hr += len(by_day)

                            if stress_msgs:
                                by_day = defaultdict(list)
                                for sm in stress_msgs:
                                    by_day[sm["date"]].append(sm["stress"])
                                existing = {e["date"]: e for e in data.get("stress", [])}
                                for d, vals in by_day.items():
                                    if d not in existing:
                                        existing[d] = {"date": d, "avg_stress": int(sum(vals) / len(vals)),
                                                                                                              "max_stress": int(max(vals)), "min_stress": int(min(vals)),
                                                       "low_minutes": 0, "medium_minutes": 0, "high_minutes": 0}
                                data["stress"] = sorted(existing.values(), key=lambda x: x["date"])
                                n_stress += len(by_day)

                            if resp_msgs:
                                by_day = defaultdict(list)
                                for rm in resp_msgs:
                                    by_day[rm["date"]].append(rm["resp"])
                                existing = {e["date"]: e for e in data.get("respiration", [])}
                                for d, vals in by_day.items():
                                    if d not in existing:
                                        existing[d] = {"date": d,
                                                       "avg_respiration": round(sum(vals) / len(vals), 1),
                                                       "max_respiration": max(vals),
                                                       "min_respiration": min(vals)}
                                data["respiration"] = sorted(existing.values(), key=lambda x: x["date"])
                                n_resp += len(by_day)

                        except Exception:
                            continue

                self._set_status(f"  → Monitor: {n_daily} days, {n_hr} HR, {n_stress} stress, {n_resp} resp")

                sleep_dir = root / "Sleep"
                n_sleep = 0
                if sleep_dir.exists():
                    for ff in sorted(sleep_dir.glob("*.fit")):
                        try:
                            fit = FitFile(str(ff))
                            sleep_records = []
                            for msg in fit.get_messages("sleep_record"):
                                rec = {}
                                for field in msg:
                                    rec[field.name] = field.value
                                sleep_records.append(rec)
                            if not sleep_records:
                                for msg in fit.get_messages("record"):
                                    rec = {}
                                    for field in msg:
                                        if field.name in ("timestamp", "sleep_stage",
                                                          "sleep_start_time", "sleep_end_time"):
                                            rec[field.name] = field.value
                                    if rec:
                                        sleep_records.append(rec)
                            if not sleep_records:
                                continue

                            d_str = None
                            if re.match(r'\d{4}-\d{2}-\d{2}', ff.stem):
                                d_str = ff.stem[:10]
                            if not d_str:
                                ts = sleep_records[0].get("timestamp") or sleep_records[0].get("sleep_start_time")
                                if ts:
                                    try:
                                        d_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
                                    except Exception:
                                        pass
                            if not d_str:
                                continue

                            stage_minutes = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
                            levels = []
                            prev_ts = None
                            for rec in sleep_records:
                                ts = rec.get("timestamp")
                                stage = rec.get("sleep_stage", 0)
                                if ts is None:
                                    continue
                                try:
                                    t = pd.Timestamp(ts)
                                except Exception:
                                    continue
                                if prev_ts is not None:
                                    dur_min = (t - prev_ts).total_seconds() / 60
                                    if 0 < dur_min < 120:
                                        stage_minutes[int(stage)] = stage_minutes.get(int(stage), 0) + dur_min
                                levels.append({"start": t.isoformat(), "end": "", "level": int(stage)})
                                prev_ts = t

                            for i in range(len(levels) - 1):
                                levels[i]["end"] = levels[i + 1]["start"]
                            if levels:
                                levels[-1]["end"] = levels[-1]["start"]

                            total_min = sum(stage_minutes.values())
                            if total_min < 5:
                                continue

                            existing = {e["date"]: e for e in data.get("sleep", [])}
                            if d_str not in existing:
                                existing[d_str] = {
                                    "date": d_str, "sleep_score": None,
                                    "sleep_time_min": round(total_min, 1),
                                    "deep_sleep_min": round(stage_minutes.get(2, 0), 1),
                                    "light_sleep_min": round(stage_minutes.get(1, 0), 1),
                                    "rem_sleep_min": round(stage_minutes.get(3, 0), 1),
                                    "awake_min": round(stage_minutes.get(0, 0), 1),
                                    "sleep_levels": levels,
                                }
                                n_sleep += 1
                            data["sleep"] = sorted(existing.values(), key=lambda x: x["date"])
                        except Exception:
                            continue

                self._set_status(f"  → Sleep: {n_sleep} nights")

                activity_dir = root / "Activity"
                if not activity_dir.exists():
                    activity_dir = root / "Sports"
                n_acts = 0
                if activity_dir.exists():
                    existing_acts = {a.get("activityId"): a for a in data.get("activities", [])}
                    for ff in sorted(activity_dir.glob("*.fit")):
                        if not re.match(r'\d{4}-\d{2}-\d{2}', ff.name):
                            continue
                        try:
                            fit_data = read_fit_file(str(ff))
                            sess = fit_data.get("session", {})
                            act_id = f"local_{ff.stem}"
                            if act_id in existing_acts:
                                continue
                            start_time = ""
                            records = fit_data.get("records", [])
                            if records and records[0].get("timestamp"):
                                try:
                                    start_time = pd.Timestamp(records[0]["timestamp"]).strftime("%Y-%m-%dT%H:%M:%S")
                                except Exception:
                                    pass
                            sport = sess.get("sport", "")
                            sport_map = {0: "running", 1: "cycling", 4: "walking",
                                         6: "swimming", 8: "fitness", 14: "hiking",
                                         17: "fitness", 18: "fitness", 19: "fitness"}
                            act_type = sport_map.get(sport, "running") if isinstance(sport, int) else "running"
                            existing_acts[act_id] = {
                                "activityId": act_id, "activityName": ff.stem,
                                "activityType": {"typeKey": act_type},
                                "startTimeLocal": start_time,
                                "distance": sess.get("total_distance", 0) or 0,
                                "duration": sess.get("total_time", 0) or 0,
                                "averageHeartRate": sess.get("avg_heart_rate"),
                                "maxHeartRate": sess.get("max_heart_rate"),
                                "calories": None, "source": "local_fit",
                            }
                            n_acts += 1
                        except Exception:
                            continue
                    data["activities"] = list(existing_acts.values())

                self._set_status(f"  → Activity: {n_acts} imported")

                dates = [d["date"] for d in data.get("daily_stats", []) if d.get("date")]
                d_min = min(dates) if dates else "?"
                d_max = max(dates) if dates else "?"
                n_days = len(data.get("daily_stats", []))
                n_sl = len(data.get("sleep", []))
                n_act = len(data.get("activities", []))

                self.garmin_data = data
                msg = (f"✅ Import complete: {n_days} days ({d_min} → {d_max}), "
                       f"{n_sl} sleep, {n_act} activities")
                self._set_status(msg)
                self._close_progress()
                self.root.after(0, self._update_data_info)
                self.root.after(0, self._redraw_data_chart)
                self.root.after(0, lambda: self.local_fit_info_var.set(
                    f"Imported: {n_days} days ({d_min} → {d_max})"))
                self.root.after(0, lambda: self.status_var.set(
                    f"Import done. {n_days} days loaded. Click 'Save to DB' to persist."))

            except Exception as e:
                err_msg = str(e)
                self._set_status(f"❌ {err_msg}")
                self._close_progress()
                self.root.after(0, lambda: messagebox.showerror("Import Error", err_msg))

        threading.Thread(target=work, daemon=True).start()    
        
     # ─── Data Monitor Tab ───────────────────────────────────────────────────

    def _build_data_tab(self):
        frame = ttk.Frame(self.tab_data)
        frame.pack(fill=tk.BOTH, expand=True)
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="⬇ Connect Update", bootstyle="primary",
               command=self._on_connect_update).pack(side=tk.LEFT, padx=5)
        Button(top, text=" Reload from DB", bootstyle="info",
               command=self._on_reload_from_db).pack(side=tk.LEFT, padx=5)
        Button(top, text="🔍+", width=3, bootstyle="secondary",
               command=self._zoom_in).pack(side=tk.LEFT, padx=2)
        Button(top, text="🔍−", width=3, bootstyle="secondary",
               command=self._zoom_out).pack(side=tk.LEFT, padx=2)
        Button(top, text="🔍 Reset", width=6, bootstyle="secondary",
               command=self._zoom_reset).pack(side=tk.LEFT, padx=2)
        self.data_info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.data_info_var).pack(side=tk.LEFT, padx=15)

        chart_frame = ttk.LabelFrame(frame, text="Metric")
        chart_frame.pack(fill=tk.X, padx=5, pady=5)
        self.data_chart_var = tk.StringVar(value="steps")
        charts = [
            ("steps", "Steps & Distance"),
            ("calories", "Calories"),
            ("heart_rate", "Heart Rate"),
            ("stress", "Stress"),
            ("sleep", "Sleep Summary"),
            ("sleep_tracing", "Sleep Tracing"),
            ("respiration", "Respiration"),
            ("body_battery", "Body Battery"),
            ("activities", "Activities"),
            ("blood_pressure", "Blood Pressure"),
        ]
        for i, (key, label) in enumerate(charts):
            ttk.Radiobutton(chart_frame, text=label, value=key,
                            variable=self.data_chart_var,
                            command=self._redraw_data_chart).grid(
                row=i // 5, column=i % 5, padx=10, pady=3, sticky=tk.W)

        self.stats_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.stats_var, anchor=tk.W,
                  font=("TkDefaultFont", 10)).pack(fill=tk.X, padx=10, pady=2)

        self.fig_data = Figure(figsize=(13, 6), dpi=100)
        self._data_twin_ax = None
        self.ax_data = self.fig_data.add_subplot(111)
        self.canvas_data = FigureCanvasTkAgg(self.fig_data, master=frame)
        self.canvas_data.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        c = self._get_chart_colors()
        self.xscrollbar_data = tk.Scrollbar(
            frame, orient=tk.HORIZONTAL, command=self._on_data_xscroll,
            troughcolor=c["fig_bg"], background=c["spine"], activebackground=c["text"])
        self.xscrollbar_data.pack(fill=tk.X, padx=5, pady=(0, 2))
        self._data_x_full_range = (0, 1)
        self._data_scrolling = False
        self.toolbar_data = NavigationToolbar2Tk(self.canvas_data, frame)
        self.toolbar_data.update()
        self.fig_data.canvas.mpl_connect('scroll_event', self._on_data_scroll)
        self._update_data_info()

    def _update_data_info(self):
        n_days = len(self.garmin_data.get("daily_stats", []))
        dates = [d["date"] for d in self.garmin_data.get("daily_stats", []) if d.get("date")]
        d_min = min(dates) if dates else None
        d_max = max(dates) if dates else None
        if d_min and d_max:
            self.data_info_var.set(f"Data: {d_min} → {d_max}  |  {n_days} days")
        else:
            self.data_info_var.set("No data yet – click Connect Update or Import")

    def _update_data_xscrollbar(self):
        cur_lo, cur_hi = self.ax_data.get_xlim()
        full_lo, full_hi = self._data_x_full_range
        total = full_hi - full_lo
        if total <= 0:
            return
        frac_lo = max(0.0, (cur_lo - full_lo) / total)
        frac_hi = min(1.0, (cur_hi - full_lo) / total)
        self._data_scrolling = True
        self.xscrollbar_data.set(frac_lo, frac_hi)
        self._data_scrolling = False

    def _zoom_in(self):
        x_lo, x_hi = self.ax_data.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) / 4
        new_lo, new_hi = center - half, center + half
        if self.data_chart_var.get() == "blood_pressure":
            self._redraw_data_chart(xlim=(new_lo, new_hi))
            self._update_data_xscrollbar()
        else:
            self.ax_data.set_xlim(new_lo, new_hi)
            self.fig_data.canvas.draw_idle()
            self._update_data_xscrollbar()

    def _zoom_out(self):
        x_lo, x_hi = self.ax_data.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) * 0.75
        new_lo, new_hi = center - half, center + half
        if self.data_chart_var.get() == "blood_pressure":
            self._redraw_data_chart(xlim=(new_lo, new_hi))
            self._update_data_xscrollbar()
        else:
            self.ax_data.set_xlim(new_lo, new_hi)
            self.fig_data.canvas.draw_idle()
            self._update_data_xscrollbar()

    def _zoom_reset(self):
        lo, hi = self._data_x_full_range
        if hi > lo:
            if self.data_chart_var.get() == "blood_pressure":
                self._redraw_data_chart(xlim=(lo, hi))
            else:
                self.ax_data.set_xlim(lo, hi)
                self.fig_data.canvas.draw_idle()
        self._update_data_xscrollbar()

    def _on_data_scroll(self, event):
        if event.inaxes != self.ax_data:
            return
        factor = 0.8 if event.button == 'up' else (1.25 if event.button == 'down' else None)
        if factor is None:
            return
        x_lo, x_hi = self.ax_data.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) / 2 * factor
        new_lo, new_hi = center - half, center + half
        if self.data_chart_var.get() == "blood_pressure":
            self._redraw_data_chart(xlim=(new_lo, new_hi))
            self._update_data_xscrollbar()
        else:
            self.ax_data.set_xlim(new_lo, new_hi)
            self.fig_data.canvas.draw_idle()
            self._update_data_xscrollbar()

    def _on_data_xscroll(self, *args):
        if self._data_scrolling:
            return
        if len(args) >= 2 and args[0] == "moveto":
            frac = float(args[1])
            full_lo, full_hi = self._data_x_full_range
            total = full_hi - full_lo
            if total <= 0:
                return
            cur_lo, cur_hi = self.ax_data.get_xlim()
            visible = min(cur_hi - cur_lo, total)
            new_lo = full_lo + frac * total
            new_hi = new_lo + visible
            if new_hi > full_hi:
                new_hi = full_hi
                new_lo = new_hi - visible
            if new_lo < full_lo:
                new_lo = full_lo
                new_hi = new_lo + visible
            if self.data_chart_var.get() == "blood_pressure":
                self._redraw_data_chart(xlim=(new_lo, new_hi))
                self._update_data_xscrollbar()
            else:
                self.ax_data.set_xlim(new_lo, new_hi)
                self.fig_data.canvas.draw_idle()
                self._update_data_xscrollbar()   
                
    # ─── Download / Update ──────────────────────────────────────────────────

    def _get_credentials(self) -> tuple:
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        api_token = self.api_token_var.get().strip() or str(TOKEN_DIR)  
        return email, password, api_token

    def _on_connect_update(self):  
        self.connect_status_var.set("⏳ Connecting…")  
        if not GARMIN_AVAILABLE:  
            self.connect_status_var.set("❌ Not Connected")  
            messagebox.showerror("Error",  
                                 "garminconnect not installed.\n"  
                                 "Run: pip install garminconnect==0.3.20 curl_cffi")  
            return  
        email, password, token_path = self._get_credentials()  
        if not email or not password:  
            self.connect_status_var.set("❌ Not Connected")  
            messagebox.showwarning("Warning", "Enter email and password in Garmin tab.")  
            return  
  
        last_date = db_get_last_date()  
        is_full = last_date is None  
  
        if is_full:  
            self._show_progress("Garmin Connect – Full Download")  
            self._set_status("No data in DB. Starting full download…")  
        else:  
            if last_date >= date.today():  
                self.status_var.set("✅ Already up to date (DB current).")  
                self.connect_status_var.set("⚪ Not Connected — Database already up to date!")  
                return  
            self._show_progress("Garmin Connect – Incremental Update")  
            self._set_status(f"DB has data up to {last_date.isoformat()}. Fetching new days…")  
  
        def work():  
            try:  
                client, err = garmin_login(email, password, token_path,  
                                           status_cb=self._set_status,  
                                           mfa_callback=self._prompt_mfa)  
                if not client:  
                    self._set_status("❌ Login failed.")  
                    self._close_progress()  
                    self.root.after(0, lambda: self.connect_status_var.set(  
                        "❌ Connection not established. Check your internet connection"))  
                    self.root.after(0, lambda: messagebox.showerror(  
                        "Login Failed", err or "Could not authenticate."))  
                    return  
  
                self.root.after(0, lambda: self.connect_status_var.set("✅ Connected"))  
  
                if is_full:  
                    self._set_status("📥 Full download (all history)…")  
                    new_data = fetch_full_history(client, status_cb=self._set_status)  
                else:  
                    self._set_status(f"📥 Fetching {last_date.isoformat()} → today…")  
                    new_data = fetch_incremental(client, last_date, status_cb=self._set_status)  
  
                n_new_days = len(new_data.get("daily_stats", []))  
                n_new_acts = len(new_data.get("activities", []))  
                self._set_status(f"  → Fetched {n_new_days} days, {n_new_acts} activities. Saving…")  
  
                save_to_db(new_data)  
                db_set_meta("first_download", db_get_meta("first_download") or date.today().isoformat())  
                db_set_meta("last_update", date.today().isoformat())  
  
                self.garmin_data = db_load_garmin_data()  
                dates = [d["date"] for d in self.garmin_data.get("daily_stats", []) if d.get("date")]  
                d_min = min(dates) if dates else "?"  
                d_max = max(dates) if dates else "?"  
                n_total = len(self.garmin_data.get("daily_stats", []))  
                msg = f"✅ {n_total} days in DB ({d_min} → {d_max})"  
                self._set_status(msg)  
                self._close_progress()  
                self.root.after(0, self._update_data_info)  
                self.root.after(0, self._redraw_data_chart)  
                self.root.after(0, lambda: self.status_var.set(msg))  
            except Exception as e:  
                err_msg = str(e)  
                self._set_status(f"❌ Error: {err_msg}")  
                self._close_progress()  
                self.root.after(0, lambda: self.connect_status_var.set(  
                    "❌ Connection not established. Check your internet connection"))  
                self.root.after(0, lambda: messagebox.showerror("Update Error", err_msg))  
        threading.Thread(target=work, daemon=True).start()


    def _on_reload_from_db(self):
        self.status_var.set("Reloading from database…")
        self.root.update()

        def work():
            self.garmin_data = db_load_garmin_data()
            self.bp_data = db_get_bp()
            if self.bp_data is not None and not self.bp_data.empty:
                self._populate_bp_table()
            dates = [d["date"] for d in self.garmin_data.get("daily_stats", []) if d.get("date")]
            d_min = min(dates) if dates else "?"
            d_max = max(dates) if dates else "?"
            n_days = len(self.garmin_data.get("daily_stats", []))
            msg = f"✅ Reloaded: {n_days} days ({d_min} → {d_max})"
            self.root.after(0, lambda: self.status_var.set(msg))
            self.root.after(0, self._update_data_info)
            self.root.after(0, self._redraw_data_chart)
            self.root.after(0, self._populate_bp_table)

        threading.Thread(target=work, daemon=True).start()   

    # ─── Chart Rendering ────────────────────────────────────────────────────

    def _safe_legend(self, ax, ax2=None):
        handles, labels = ax.get_legend_handles_labels()
        if ax2 is not None:
            h2, l2 = ax2.get_legend_handles_labels()
            handles += h2
            labels += l2
        if handles:
            c = self._get_chart_colors()
            ax.legend(handles, labels, loc="upper left", fontsize=8,
                      facecolor=c["legend_bg"], edgecolor=c["spine"], labelcolor=c["text"])

    def _redraw_data_chart(self, xlim=None):
        if self._data_twin_ax is not None:
            self.fig_data.delaxes(self._data_twin_ax)
            self._data_twin_ax = None
        self.ax_data.clear()
        choice = self.data_chart_var.get()
        stats_text = ""
        twin_ax = None

        if choice == "steps" and self.garmin_data.get("daily_stats"):
            df = pd.DataFrame(self.garmin_data["daily_stats"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_data.plot(df["date"], df["steps"], color=COLORS["steps"], linewidth=1.5, label="Steps")
            self.ax_data.set_ylabel("Steps", color=COLORS["steps"])
            twin_ax = self._data_twin_ax = self.ax_data.twinx()
            twin_ax.plot(df["date"], df["distance_km"], color=COLORS["distance"], linewidth=1.5, label="Distance (km)")
            twin_ax.set_ylabel("km", color=COLORS["distance"])
            self.ax_data.set_title("Daily Steps & Distance")
            stats_text = f"Steps: {compute_stats(df['steps'])}   |   Distance: {compute_stats(df['distance_km'])} km"

        elif choice == "calories" and self.garmin_data.get("daily_stats"):
            df = pd.DataFrame(self.garmin_data["daily_stats"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_data.fill_between(df["date"], df["calories"], alpha=0.3, color=COLORS["calories"])
            self.ax_data.plot(df["date"], df["calories"], color=COLORS["calories"], linewidth=1.2, label="Total kcal")
            self.ax_data.plot(df["date"], df["active_calories"], color=COLORS["active_cal"], linewidth=1.2,
                              linestyle="--", label="Active kcal")
            self.ax_data.set_ylabel("kcal")
            self.ax_data.set_title("Calories")
            stats_text = f"Total: {compute_stats(df['calories'])} kcal   |   Active: {compute_stats(df['active_calories'])} kcal"

        elif choice == "heart_rate" and self.garmin_data.get("heart_rate"):
            df = pd.DataFrame(self.garmin_data["heart_rate"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_data.plot(df["date"], df["resting_hr"], color=COLORS["resting_hr"], linewidth=1.5, label="Resting")
            self.ax_data.plot(df["date"], df["max_hr"], color=COLORS["max_hr"], linewidth=1.0, label="Max")
            self.ax_data.plot(df["date"], df["min_hr"], color=COLORS["min_hr"], linewidth=1.0, label="Min")
            self.ax_data.set_ylabel("BPM")
            self.ax_data.set_title("Heart Rate")
            stats_text = f"Resting: {compute_stats(df['resting_hr'])} bpm   |   Max: {compute_stats(df['max_hr'])} bpm"

        elif choice == "stress" and self.garmin_data.get("stress"):
            df = pd.DataFrame(self.garmin_data["stress"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_data.plot(df["date"], df["avg_stress"], color=COLORS["stress"], linewidth=1.5, label="Avg Stress")
            self.ax_data.plot(df["date"], df["max_stress"], color=COLORS["stress_high"], linewidth=0.8,
                              linestyle="--", label="Max")
            if "min_stress" in df.columns and df["min_stress"].notna().any():
                self.ax_data.plot(df["date"], df["min_stress"], color=COLORS["stress_low"], linewidth=0.8,
                                  linestyle="--", label="Min")
            twin_ax = self.ax_data.twinx()
            if "low_minutes" in df.columns:
                twin_ax.fill_between(df["date"], 0, df["low_minutes"].fillna(0),
                                     alpha=0.3, color=COLORS["stress_low"], label="Low min")
                twin_ax.fill_between(df["date"], df["low_minutes"].fillna(0),
                                     df["low_minutes"].fillna(0) + df["medium_minutes"].fillna(0),
                                     alpha=0.3, color=COLORS["stress_med"], label="Med min")
                twin_ax.fill_between(df["date"],
                                     df["low_minutes"].fillna(0) + df["medium_minutes"].fillna(0),
                                     df["low_minutes"].fillna(0) + df["medium_minutes"].fillna(0)
                                     + df["high_minutes"].fillna(0),
                                     alpha=0.3, color=COLORS["stress_high"], label="High min")
            twin_ax.set_ylabel("Minutes")
            self.ax_data.set_ylabel("Stress Level")
            self.ax_data.set_title("Stress")
            stats_text = f"Avg: {compute_stats(df['avg_stress'])}   |   Max: {compute_stats(df['max_stress'])}"

        elif choice == "sleep" and self.garmin_data.get("sleep"):
            df = pd.DataFrame(self.garmin_data["sleep"])
            df["date"] = pd.to_datetime(df["date"])
            age = self.settings.get("age", 30)

            df["quality_score"] = [
                compute_sleep_quality_score(d.deep_sleep_min, d.light_sleep_min,
                                            d.rem_sleep_min, d.awake_min,
                                            d.sleep_time_min, age)
                for d in df.itertuples()
            ]

            bottom = np.zeros(len(df))
            self.ax_data.bar(df["date"], df["deep_sleep_min"].fillna(0), color=COLORS["sleep_deep"],
                             label="Deep", bottom=bottom, width=0.8)
            bottom += df["deep_sleep_min"].fillna(0).values
            self.ax_data.bar(df["date"], df["light_sleep_min"].fillna(0), color=COLORS["sleep_light"],
                             label="Light", bottom=bottom, width=0.8)
            bottom += df["light_sleep_min"].fillna(0).values
            self.ax_data.bar(df["date"], df["rem_sleep_min"].fillna(0), color=COLORS["sleep_rem"],
                             label="REM", bottom=bottom, width=0.8)

            c = self._get_chart_colors()
            for i, row in df.iterrows():
                score = row["quality_score"]
                if score is not None:
                    total_h = (row["deep_sleep_min"] or 0) + (row["light_sleep_min"] or 0) + (row["rem_sleep_min"] or 0)
                    self.ax_data.text(row["date"], total_h + 5, f"{score:.0f}",
                                      ha="center", va="bottom", fontsize=7,
                                      color=c["text"], fontweight="bold")

            self.ax_data.set_ylabel("Minutes")
            self.ax_data.set_title(f"Sleep Summary  (age {age})")
            stats_text = (f"Total: {compute_stats(df['sleep_time_min'])} min   |   "
                          f"Deep: {compute_stats(df['deep_sleep_min'])}   |   "
                          f"REM: {compute_stats(df['rem_sleep_min'])}   |   "
                          f"Quality: {compute_stats(df['quality_score'])}")

        elif choice == "sleep_tracing" and self.garmin_data.get("sleep"):
            sleep_entries = [e for e in self.garmin_data.get("sleep", []) if e.get("sleep_levels")]
            if not sleep_entries:
                self.ax_data.text(0.5, 0.5, "No sleep tracing data available.",
                                  ha="center", va="center", fontsize=11)
            else:
                sleep_entries = sorted(sleep_entries, key=lambda x: x["date"])
                sleep_entries = sleep_entries[-14:]
                n_nights = len(sleep_entries)
                stage_map = {2: (10, "#2980B9"), 1: (12, "#1ABC9C"), 3: (14, "#9B59B6"), 0: (16, "#F1C40F")}
                sleep_start = self.settings.get("sleep_start", "22:00")
                sleep_end = self.settings.get("sleep_end", "07:00")
                sh, sm = map(int, sleep_start.split(":"))
                eh, em = map(int, sleep_end.split(":"))
                if eh < sh or (eh == sh and em <= sm):
                    window_hours = (24 - sh - sm / 60) + (eh + em / 60)
                else:
                    window_hours = (eh - sh) + (em - sm) / 60
                import random
                random.seed(42)
                bright_palette = ["#FF6B6B", "#4ECDC4", "#FFD93D", "#6C5CE7",
                                  "#A8E6CF", "#FF8A5C", "#45B7D1", "#F9CA24"]
                night_colors = [random.choice(bright_palette) for _ in range(n_nights)]

                def to_unit_x(ts, night_idx):
                    night_date = pd.Timestamp(sleep_entries[night_idx]["date"])
                    win_start = night_date.replace(hour=sh, minute=sm, second=0)
                    delta_h = (ts - win_start).total_seconds() / 3600
                    return night_idx + delta_h / window_hours

                for idx, entry in enumerate(sleep_entries):
                    for lv in entry["sleep_levels"]:
                        start_str, end_str = lv.get("start", ""), lv.get("end", "")
                        level = lv.get("level", 0)
                        if not start_str or not end_str:
                            continue
                        try:
                            start_t = pd.Timestamp(start_str).replace(tzinfo=None)
                            end_t = pd.Timestamp(end_str).replace(tzinfo=None)
                        except Exception:
                            continue
                        if end_t <= start_t:
                            continue
                        x0 = to_unit_x(start_t, idx)
                        x1 = to_unit_x(end_t, idx)
                        if x1 <= x0:
                            continue
                        y_val, color = stage_map.get(level, (12, "#999999"))
                        self.ax_data.fill_between([x0, x1], 0, y_val, color=color, alpha=0.85, edgecolor="none")

                for i in range(1, n_nights):
                    self.ax_data.axvline(i, color=night_colors[i], linewidth=2.0, alpha=0.8)

                for idx in range(n_nights):
                    d = pd.Timestamp(sleep_entries[idx]["date"])
                    self.ax_data.text(idx + 0.5, -3.0, d.strftime("%d.%m.%y"),
                                      ha="center", va="top", fontsize=8,
                                      color=night_colors[idx], fontweight="bold")

                self.ax_data.set_ylim(0, 18)
                self.ax_data.set_yticks([10, 12, 14, 16])
                self.ax_data.set_yticklabels(["Deep", "Light", "REM", "Awake"], fontsize=9)
                self.ax_data.set_xlim(-0.02, n_nights + 0.02)
                self.ax_data.set_title("Sleep Tracing")
                all_deep = [e.get("deep_sleep_min", 0) or 0 for e in sleep_entries]
                all_light = [e.get("light_sleep_min", 0) or 0 for e in sleep_entries]
                all_rem = [e.get("rem_sleep_min", 0) or 0 for e in sleep_entries]
                all_awake = [e.get("awake_min", 0) or 0 for e in sleep_entries]
                stats_text = (f"Total: {(sum(all_deep)+sum(all_light)+sum(all_rem)+sum(all_awake))/60:.1f} h  |  "
                              f"Deep: {sum(all_deep)/60:.1f} h  |  Light: {sum(all_light)/60:.1f} h  |  "
                              f"REM: {sum(all_rem)/60:.1f} h  |  Awake: {sum(all_awake)/60:.1f} h")

        elif choice == "respiration" and self.garmin_data.get("respiration"):
            df = pd.DataFrame(self.garmin_data["respiration"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_data.plot(df["date"], df["avg_respiration"], color=COLORS["respiration"], linewidth=1.5, label="Avg")
            if df["max_respiration"].notna().any():
                self.ax_data.plot(df["date"], df["max_respiration"], color=COLORS["respiration"],
                                  linewidth=0.8, linestyle="--", alpha=0.6, label="Max")
            if df["min_respiration"].notna().any():
                self.ax_data.plot(df["date"], df["min_respiration"], color=COLORS["respiration"],
                                  linewidth=0.8, linestyle=":", alpha=0.6, label="Min")
            self.ax_data.set_ylabel("Breaths/min")
            self.ax_data.set_title("Respiration Rate")
            stats_text = f"Respiration: {compute_stats(df['avg_respiration'])} breaths/min"

        elif choice == "body_battery":
            bbv = self.garmin_data.get("body_battery_values", [])
            if bbv:
                df = pd.DataFrame(bbv)
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.sort_values("timestamp")
                self.ax_data.fill_between(df["timestamp"], df["value"], alpha=0.25, color=COLORS["body_battery"])
                self.ax_data.plot(df["timestamp"], df["value"], color=COLORS["body_battery"],
                                  linewidth=1.2, label="Body Battery")
                self.ax_data.set_ylabel("Points")
                self.ax_data.set_ylim(0, 100)
                self.ax_data.set_title("Body Battery (intraday curve)")
                stats_text = f"Body Battery: {compute_stats(df['value'])}"
            else:
                bb = self.garmin_data.get("body_battery", [])
                if bb:
                    dates, values = [], []
                    for entry in bb:
                        d = entry.get("date")
                        v = entry.get("value")
                        if d and v is not None:
                            try:
                                dates.append(pd.Timestamp(str(d)))
                                values.append(float(v))
                            except (ValueError, TypeError):
                                continue
                    if dates:
                        self.ax_data.plot(dates, values, color=COLORS["body_battery"],
                                          linewidth=1.5, marker="o", markersize=4, label="Body Battery (daily)")
                        self.ax_data.set_ylabel("Points")
                        self.ax_data.set_ylim(0, 100)
                        self.ax_data.set_title("Body Battery (daily only — no intraday data)")
                        stats_text = f"Body Battery: {compute_stats(pd.Series(values))}"
                    else:
                        self.ax_data.text(0.5, 0.5, "No valid body battery entries",
                                          ha="center", va="center", fontsize=11)
                else:
                    self.ax_data.text(0.5, 0.5, "No body battery data",
                                      ha="center", va="center", fontsize=11)

        elif choice == "activities" and self.garmin_data.get("activities"):
            acts = self.garmin_data["activities"]
            types = [a.get("activityType", {}).get("typeKey", "unknown")
                     if isinstance(a.get("activityType"), dict) else a.get("activityType", "unknown")
                     for a in acts]
            counts = pd.Series(types).value_counts()
            self.ax_data.barh(counts.index, counts.values, color=COLORS["steps"])
            self.ax_data.set_xlabel("Count")
            self.ax_data.set_title("Activities by Type")
            stats_text = f"Total activities: {len(acts)}"

        elif choice == "blood_pressure":
            if self.bp_data is None or self.bp_data.empty:
                self.ax_data.text(0.5, 0.5, "No blood pressure data.\nLoad data in the Blood Pressure tab.",
                                  ha="center", va="center", fontsize=11)
            else:
                df = self.bp_data.copy().sort_values("datetime").reset_index(drop=True)
                n = len(df)
                x = np.arange(n)
                width = 0.28

                sys_colors = [get_bp_color(s, d) for s, d in zip(df["sys"], df["dia"])]
                dia_colors = [get_bp_color(s, d) for s, d in zip(df["sys"], df["dia"])]
                pulse_colors = ["#2ECC71"] * n

                self.ax_data.bar(x - width, df["sys"], width=width, color=sys_colors, label="Systolic")
                self.ax_data.bar(x, df["dia"], width=width, color=dia_colors, label="Diastolic")

                if xlim is not None:
                    x_lo, x_hi = xlim
                else:
                    x_lo, x_hi = (0, n - 1)
                visible_bars = max(1, int(x_hi - x_lo) + 1)
                px_per_group = 1100.0 / visible_bars
                val_fontsize = max(5, min(14, px_per_group / 5))
                cat_fontsize = max(4, min(12, px_per_group / 6))

                c = self._get_chart_colors()
                label_color = c["text"]

                for i in range(n):
                    if x[i] < x_lo - 1 or x[i] > x_hi + 1:
                        continue

                    s_val = df["sys"].iloc[i]
                    self.ax_data.text(x[i] - width, s_val + 1, f"{s_val:.0f}",
                                      ha="center", va="bottom", fontsize=val_fontsize, color=label_color)
                    self.ax_data.text(x[i] - width, 2, "Sys",
                                      ha="center", va="bottom", fontsize=cat_fontsize,
                                      color=label_color, fontstyle="italic")

                    d_val = df["dia"].iloc[i]
                    self.ax_data.text(x[i], d_val + 1, f"{d_val:.0f}",
                                      ha="center", va="bottom", fontsize=val_fontsize, color=label_color)
                    self.ax_data.text(x[i], 2, "Dia",
                                      ha="center", va="bottom", fontsize=cat_fontsize,
                                      color=label_color, fontstyle="italic")

                y_max = max(df["sys"].max(), df["dia"].max())
                self.ax_data.set_ylim(0, y_max * 1.15)

                tick_positions = list(range(n))
                tick_labels = []
                prev_date = None
                for i in range(n):
                    d = df["datum"].iloc[i]
                    t = df["time"].iloc[i].strftime("%H:%M")
                    if d != prev_date:
                        tick_labels.append(f"{d.strftime('%d.%m')}\n{t}")
                        prev_date = d
                    else:
                        tick_labels.append(t)
                self.ax_data.set_xticks(tick_positions)
                self.ax_data.set_xticklabels(tick_labels, rotation=90, fontsize=6)
                parts = []
                if "sys" in df.columns:
                    parts.append(f"Sys: {compute_stats(df['sys'])} mmHg")
                if "dia" in df.columns:
                    parts.append(f"Dia: {compute_stats(df['dia'])} mmHg")
                stats_text = "   |   ".join(parts)

        else:
            self.ax_data.text(0.5, 0.5, "No data – click Full Download or Import",
                              ha="center", va="center", fontsize=12)

        self._safe_legend(self.ax_data, twin_ax)
        self.ax_data.tick_params(axis="x", rotation=30, labelsize=11)
        self._style_figure(self.fig_data, self.ax_data, twin_ax)
        self.fig_data.tight_layout()

        if xlim is not None:
            self.ax_data.set_xlim(*xlim)

        self.canvas_data.draw()
        self.stats_var.set(stats_text)
        x_lo, x_hi = self.ax_data.get_xlim()
        if x_hi > x_lo:
            if xlim is None:
                self._data_x_full_range = (x_lo, x_hi)
        self._data_scrolling = True
        self.xscrollbar_data.set(0.0, 1.0)
        self._data_scrolling = False                                                           

    # ─── Blood Pressure Tab ─────────────────────────────────────────────────

    def _build_bp_tab(self):
        frame = ttk.Frame(self.tab_bp)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="➕ Add Row", bootstyle="success",
               command=self._on_bp_add_row).pack(side=tk.LEFT, padx=5)
        Button(top, text="🗑 Delete Row", bootstyle="danger",
               command=self._on_bp_delete_row).pack(side=tk.LEFT, padx=5)
        Button(top, text="Save to DB", bootstyle="primary",
               command=self._on_bp_save_all).pack(side=tk.LEFT, padx=5)
        Button(top, text="Reload", bootstyle="info",
               command=self._on_bp_reload).pack(side=tk.LEFT, padx=5)
        self.bp_tab_info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.bp_tab_info_var).pack(side=tk.LEFT, padx=15)

        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("datum", "time", "sys", "dia")  
        self.bp_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=20)  
        self.bp_tree.heading("datum", text="datum")  
        self.bp_tree.heading("time", text="time")  
        self.bp_tree.heading("sys", text="sys")  
        self.bp_tree.heading("dia", text="dia")  
        self.bp_tree.column("datum", width=80, anchor=tk.CENTER)  
        self.bp_tree.column("time", width=80, anchor=tk.CENTER)  
        self.bp_tree.column("sys", width=80, anchor=tk.CENTER)  
        self.bp_tree.column("dia", width=80, anchor=tk.CENTER)

        ysb = tk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.bp_tree.yview)
        xsb = tk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.bp_tree.xview)
        self.bp_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.bp_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)

        self.bp_tree.bind("<Double-1>", self._on_bp_cell_edit)
        ttk.Label(frame, text="Double-click a cell to edit.",
                  foreground="gray").pack(anchor=tk.W, padx=10, pady=(0, 5))

    def _on_bp_cell_edit(self, event):
        region = self.bp_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col_id = self.bp_tree.identify_column(event.x)
        row_id = self.bp_tree.identify_row(event.y)
        if not row_id:
            return
        col_idx = int(col_id.replace("#", "")) - 1
        bbox = self.bp_tree.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        current_val = self.bp_tree.item(row_id, "values")[col_idx]
        entry = tk.Entry(self.bp_tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, str(current_val))
        entry.select_range(0, tk.END)
        entry.focus_set()

        def _commit(_event=None):
            if not entry.winfo_exists():
                return
            new_val = entry.get().strip()
            values = list(self.bp_tree.item(row_id, "values"))
            values[col_idx] = new_val
            self.bp_tree.item(row_id, values=values)
            entry.destroy()

        def _cancel(_event=None):
            if entry.winfo_exists():
                entry.destroy()

        entry.bind("<Return>", _commit)
        entry.bind("<FocusOut>", _commit)
        entry.bind("<Escape>", _cancel)

    def _on_bp_add_row(self):
        self.bp_tree.insert("", tk.END, values=(date.today().strftime("%d.%m.%y"), "12:00", "", "", ""))

    def _on_bp_delete_row(self):
        sel = self.bp_tree.selection()
        if sel:
            self.bp_tree.delete(*sel)

    def _on_bp_save_all(self):
        rows = self.bp_tree.get_children()
        if not rows:
            messagebox.showwarning("Warning", "No data to save.")
            return
        bp_rows = []
        for row in rows:
            vals = self.bp_tree.item(row, "values")
            bp_rows.append({
                "datum": vals[0], "time": vals[1],
                "sys": float(vals[2]) if vals[2] else None,
                "dia": float(vals[3]) if vals[3] else None,
            })
        def work():
            db_save_bp(bp_rows)
            self.bp_data = db_get_bp()
            n = len(self.bp_data) if self.bp_data is not None else 0
            self.root.after(0, lambda: self.bp_tab_info_var.set(f"Saved {n} readings."))
            self.root.after(0, lambda: self.status_var.set(f"BP saved: {n} readings."))
            if self.data_chart_var.get() == "blood_pressure":
                self.root.after(0, self._redraw_data_chart)
        threading.Thread(target=work, daemon=True).start()

    def _on_bp_reload(self):
        def work():
            self.bp_data = db_get_bp()
            n = len(self.bp_data) if self.bp_data is not None else 0
            self.root.after(0, self._populate_bp_table)
            self.root.after(0, lambda: self.bp_tab_info_var.set(
                f"Loaded {n} readings" if n else "No data."))
            if self.data_chart_var.get() == "blood_pressure":
                self.root.after(0, self._redraw_data_chart)
        threading.Thread(target=work, daemon=True).start()

    def _populate_bp_table(self):
        for item in self.bp_tree.get_children():
            self.bp_tree.delete(item)
        if self.bp_data is None or self.bp_data.empty:
            return
        for _, row in self.bp_data.iterrows():
            d = row["datum"].strftime("%d.%m.%y")
            t = row["time"].strftime("%H:%M")
            s = int(row["sys"]) if pd.notna(row["sys"]) else ""
            dia = int(row["dia"]) if pd.notna(row["dia"]) else ""
            self.bp_tree.insert("", tk.END, values=(d, t, s, dia))   
            
    # ─── Art Tab ────────────────────────────────────────────────────────────

    _ART_STYLES = {
        "daily_mosaic":  "Daily Mosaic",
        "sleep_heatmap": "Sleep Heatmap",
        "bp_wave":       "BP Waveform",
        "kandinsky":     "Kandinsky",
        "mondrian":      "Mondrian",
        "rothko":        "Rothko",
        "pollock":       "Pollock",
        "bauhaus":       "Bauhaus",
        "scribble":      "Scribble",
        "wild_mix":      "Wild Mix",
    }

    _ART_DATA_LABELS = {
        "steps": "Steps & Distance",
        "calories": "Calories",
        "heart_rate": "Heart Rate",
        "stress": "Stress",
        "sleep": "Sleep Summary",
        "sleep_tracing": "Sleep Tracing",
        "respiration": "Respiration",
        "body_battery": "Body Battery",
        "activities": "Activities",
        "blood_pressure": "Blood Pressure",
    }

    def _build_art_tab(self):
        frame = ttk.Frame(self.tab_art)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ctrl = ttk.LabelFrame(frame, text="Art Settings")
        ctrl.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(ctrl, text="Style:").grid(row=0, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_style_var = tk.StringVar(value="scribble")
        style_frame = ttk.Frame(ctrl)
        style_frame.grid(row=0, column=1, columnspan=5, padx=5, pady=2, sticky=tk.W)
        for i, (key, label) in enumerate(self._ART_STYLES.items()):
            ttk.Radiobutton(style_frame, text=label, value=key,
                            variable=self.art_style_var,
                            command=self._on_art_style_changed).grid(
                row=0, column=i, padx=6, pady=2, sticky=tk.W)

        ttk.Label(ctrl, text="Data:").grid(row=1, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_data_var = tk.StringVar(value="steps")
        self.art_data_combo = ttk.Combobox(ctrl, textvariable=self.art_data_var,
                                           values=list(self._ART_DATA_LABELS.keys()),
                                           width=20, state="readonly")
        self.art_data_combo.bind("<<ComboboxSelected>>", lambda e: self._on_art_data_changed())
        self.art_data_combo.grid(row=1, column=1, columnspan=2, padx=5, pady=4, sticky=tk.W)

        ttk.Label(ctrl, text="Period:").grid(row=2, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_period_var = tk.StringVar(value="weekly")
        ttk.Radiobutton(ctrl, text="Weekly", value="weekly",
                        variable=self.art_period_var,
                        command=self._on_art_period_changed).grid(
            row=2, column=1, padx=4, pady=2, sticky=tk.W)
        ttk.Radiobutton(ctrl, text="Monthly", value="monthly",
                        variable=self.art_period_var,
                        command=self._on_art_period_changed).grid(
            row=2, column=2, padx=4, pady=2, sticky=tk.W)

        ttk.Label(ctrl, text="Select:").grid(row=2, column=3, padx=5, pady=4, sticky=tk.W)
        self.art_period_select_var = tk.StringVar(value="")
        self.art_period_select_combo = ttk.Combobox(ctrl,
                                                    textvariable=self.art_period_select_var,
                                                    values=[], width=18, state="readonly")
        self.art_period_select_combo.grid(row=2, column=4, columnspan=2, padx=5, pady=4, sticky=tk.W)
        ttk.Label(ctrl, text="Palette:").grid(row=3, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_palette_var = tk.StringVar(value="thermal")
        ttk.Label(ctrl, text="Size:").grid(row=3, column=2, padx=5, pady=4, sticky=tk.W)
        self.art_size_var = tk.StringVar(value="3000")
        sizes = ["1500", "2000", "3000", "4000", "6000"]
        self.art_size_combo = ttk.Combobox(ctrl, textvariable=self.art_size_var,
                                           values=sizes, width=6, state="readonly")
        self.art_size_combo.grid(row=3, column=3, padx=5, pady=4, sticky=tk.W)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=5, pady=5)
        Button(btn_row, text="Generate", bootstyle="primary",
               command=self._on_generate_art).pack(side=tk.LEFT, padx=5)
        Button(btn_row, text="Save PNG", bootstyle="success",
               command=self._on_save_art).pack(side=tk.LEFT, padx=5)
        self.art_info_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self.art_info_var).pack(side=tk.LEFT, padx=15)

        preview_frame = ttk.Frame(frame)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.art_preview_label = tk.Label(preview_frame, text="Art will appear here",
                                          bg="#353535", fg="#666",
                                          font=("TkDefaultFont", 14))
        self.art_preview_label.pack(fill=tk.BOTH, expand=True)
        self._art_image = None
        self._art_photo = None
        self._on_art_period_changed()

    def _on_art_style_changed(self):
        pass

    def _on_art_data_changed(self):
        self._populate_art_periods()

    def _on_art_period_changed(self):
        self._populate_art_periods()

    def _populate_art_periods(self):
        period_mode = self.art_period_var.get()
        data_key = self.art_data_var.get()
        dates = self._get_dates_for_data_source(data_key)
        if not dates:
            self.art_period_select_combo.configure(values=[])
            self.art_period_select_var.set("")
            return
        if period_mode == "weekly":
            weeks = sorted(set(d.isocalendar()[0:2] for d in dates))
            labels = [f"{y}-W{w:02d}" for y, w in weeks]
        else:
            months = sorted(set((d.year, d.month) for d in dates))
            labels = [f"{y}-{m:02d}" for y, m in months]
        self.art_period_select_combo.configure(values=labels)
        if labels:
            self.art_period_select_var.set(labels[-1])

    def _get_dates_for_data_source(self, data_key: str) -> list:
        if data_key == "blood_pressure":
            if self.bp_data is not None and not self.bp_data.empty:
                return list(self.bp_data["datetime"].dropna())
            return []
        if data_key == "body_battery":
            bbv = self.garmin_data.get("body_battery_values", [])
            if bbv:
                dates = []
                for entry in bbv:
                    ts = entry.get("timestamp")
                    if ts:
                        try:
                            dates.append(pd.Timestamp(ts))
                        except Exception:
                            pass
                if dates:
                    return dates
            data = self.garmin_data.get("body_battery", [])
            if not data:
                return []
            dates = []
            for entry in data:
                d = entry.get("date")
                if d:
                    try:
                        dates.append(pd.Timestamp(d))
                    except Exception:
                        pass
            return dates
        if data_key == "sleep_tracing":
            data = self.garmin_data.get("sleep", [])
            dates = []
            for entry in data:
                if entry.get("sleep_levels"):
                    d = entry.get("date")
                    if d:
                        try:
                            dates.append(pd.Timestamp(d))
                        except Exception:
                            pass
            return dates
        if data_key == "activities":
            data = self.garmin_data.get("activities", [])
            dates = []
            for entry in data:
                d = entry.get("startTimeLocal") or entry.get("startTimeGMT")
                if d:
                    try:
                        dates.append(pd.Timestamp(d))
                    except Exception:
                        pass
            return dates
        data = self.garmin_data.get(data_key, [])
        if not data:
            return []
        dates = []
        for entry in data:
            d = entry.get("date")
            if d:
                try:
                    dates.append(pd.Timestamp(d))
                except Exception:
                    pass
        return dates

    def _get_period_date_range(self) -> tuple:
        period_mode = self.art_period_var.get()
        selected = self.art_period_select_var.get()
        if not selected:
            return None, None
        if period_mode == "weekly":
            try:
                year, week = selected.split("-W")
                year, week = int(year), int(week)
                start = date.fromisocalendar(year, week, 1)
                end = date.fromisocalendar(year, week, 7)
                return pd.Timestamp(start), pd.Timestamp(end)
            except Exception:
                return None, None
        else:
            try:
                year, month = map(int, selected.split("-"))
                start = pd.Timestamp(year=year, month=month, day=1)
                end = (start + pd.offsets.MonthEnd(0))
                return start, end
            except Exception:
                return None, None

    def _extract_art_data(self) -> dict:
        data_key = self.art_data_var.get()
        start, end = self._get_period_date_range()
        if start is None:
            return {}

        label = self._ART_DATA_LABELS.get(data_key, data_key)

        if data_key == "blood_pressure":
            if self.bp_data is None or self.bp_data.empty:
                return {}
            df = self.bp_data[(self.bp_data["datetime"] >= start) &
                              (self.bp_data["datetime"] <= end)].copy()
            if df.empty:
                return {}
            df["day"] = df["datetime"].dt.date
            daily = df.groupby("day").agg({"sys": "mean", "dia": "mean"}).reset_index()
            daily = daily.sort_values("day")
            raw_avg = ((daily["sys"] + daily["dia"]) / 2).tolist()
            raw_min = daily["dia"].tolist()
            raw_max = daily["sys"].tolist()
            color_values = raw_avg[:]
            n_points = len(daily)
            value_label = f"avg sys: {daily['sys'].mean():.0f}  |  avg dia: {daily['dia'].mean():.0f}"

        elif data_key == "body_battery":
            bbv = self.garmin_data.get("body_battery_values", [])
            if bbv:
                df = pd.DataFrame(bbv)
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
                if df.empty:
                    return {}
                df["day"] = df["timestamp"].dt.date
                daily = df.groupby("day")["value"].mean().reset_index().sort_values("day")
                raw_avg = daily["value"].tolist()
            else:
                data = self.garmin_data.get("body_battery", [])
                filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
                if not filtered:
                    return {}
                raw_avg = [e.get("value", 0) or 0 for e in filtered]
            raw_min = [max(0, v * 0.7) for v in raw_avg]
            raw_max = [min(100, v * 1.2) for v in raw_avg]
            color_values = raw_avg[:]
            n_points = len(raw_avg)
            value_label = f"avg: {np.mean(raw_avg):.0f}  |  {n_points} days"

        elif data_key == "sleep_tracing":
            sleep = [e for e in self.garmin_data.get("sleep", []) if e.get("sleep_levels")]
            filtered = []
            for e in sleep:
                d = e.get("date")
                if d and start <= pd.Timestamp(d) <= end:
                    filtered.append(e)
            if not filtered:
                return {}
            raw_avg = [(e.get("deep_sleep_min", 0) or 0) + (e.get("rem_sleep_min", 0) or 0) for e in filtered]
            raw_min = [e.get("deep_sleep_min", 0) or 0 for e in filtered]
            raw_max = [e.get("sleep_time_min", 0) or 0 for e in filtered]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"{n_points} nights  |  avg quality: {np.mean(raw_avg):.0f} min"

        elif data_key == "activities":
            acts = self.garmin_data.get("activities", [])
            filtered = []
            for a in acts:
                d = a.get("startTimeLocal") or a.get("startTimeGMT")
                if d and start <= pd.Timestamp(d) <= end:
                    filtered.append(a)
            if not filtered:
                return {}
            raw_avg = [a.get("duration", 0) or 0 for a in filtered]
            raw_min = [max(0, v * 0.5) for v in raw_avg]
            raw_max = [v * 1.5 for v in raw_avg]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"{n_points} activities"

        elif data_key == "steps":
            data = self.garmin_data.get("daily_stats", [])
            filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
            if not filtered:
                return {}
            raw_avg = [e.get("steps", 0) or 0 for e in filtered]
            raw_min = [e.get("distance_km", 0) or 0 for e in filtered]
            raw_max = raw_avg[:]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"avg steps: {np.mean(raw_avg):.0f}  |  {n_points} days"

        elif data_key == "calories":
            data = self.garmin_data.get("daily_stats", [])
            filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
            if not filtered:
                return {}
            raw_avg = [e.get("calories", 0) or 0 for e in filtered]
            raw_min = [e.get("active_calories", 0) or 0 for e in filtered]
            raw_max = raw_avg[:]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"avg: {np.mean(raw_avg):.0f} kcal  |  {n_points} days"

        elif data_key == "heart_rate":
            data = self.garmin_data.get("heart_rate", [])
            filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
            if not filtered:
                return {}
            raw_avg = [e.get("resting_hr", 0) or 0 for e in filtered]
            raw_min = [e.get("min_hr", 0) or 0 for e in filtered]
            raw_max = [e.get("max_hr", 0) or 0 for e in filtered]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"avg resting: {np.mean(raw_avg):.0f} bpm"

        elif data_key == "stress":
            data = self.garmin_data.get("stress", [])
            filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
            if not filtered:
                return {}
            raw_avg = [e.get("avg_stress", 0) or 0 for e in filtered]
            raw_min = [e.get("min_stress", 0) or 0 for e in filtered]
            raw_max = [e.get("max_stress", 0) or 0 for e in filtered]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"avg stress: {np.mean(raw_avg):.0f}"

        elif data_key == "sleep":
            data = self.garmin_data.get("sleep", [])
            filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
            if not filtered:
                return {}
            raw_avg = [e.get("sleep_time_min", 0) or 0 for e in filtered]
            raw_min = [e.get("deep_sleep_min", 0) or 0 for e in filtered]
            raw_max = [e.get("awake_min", 0) or 0 for e in filtered]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"avg sleep: {np.mean(raw_avg)/60:.1f} h  |  {n_points} nights"

        elif data_key == "respiration":
            data = self.garmin_data.get("respiration", [])
            filtered = [e for e in data if start <= pd.Timestamp(e.get("date", "")) <= end]
            if not filtered:
                return {}
            raw_avg = [e.get("avg_respiration", 0) or 0 for e in filtered]
            raw_min = [e.get("min_respiration", 0) or 0 for e in filtered]
            raw_max = [e.get("max_respiration", 0) or 0 for e in filtered]
            color_values = raw_avg[:]
            n_points = len(filtered)
            value_label = f"avg: {np.mean(raw_avg):.1f} br/min"

        else:
            return {}

        period_label = self.art_period_select_var.get()
        title = f"{label} – {period_label}"

        all_vals = raw_min + raw_avg + raw_max
        v_min, v_max = min(all_vals), max(all_vals)
        v_range = v_max - v_min or 1
        data_min = [(v - v_min) / v_range for v in raw_min]
        data_avg = [(v - v_min) / v_range for v in raw_avg]
        data_max = [(v - v_min) / v_range for v in raw_max]

        return {
            "data_min": data_min, "data_avg": data_avg, "data_max": data_max,
            "raw_min": raw_min, "raw_avg": raw_avg, "raw_max": raw_max,
            "color_values": color_values, "title": title,
            "value_label": value_label, "n_points": n_points,
        }

    _DATA_PALETTES = {
        "thermal": {"low": (30, 40, 80), "mid": (255, 100, 30), "high": (255, 30, 30), "bg": (13, 17, 23)},
        "ocean":   {"low": (10, 20, 60), "mid": (0, 150, 180), "high": (100, 230, 255), "bg": (5, 10, 30)},
        "neon":    {"low": (40, 10, 80), "mid": (200, 50, 255), "high": (50, 255, 150), "bg": (15, 5, 25)},
        "rainbow": {"low": (255, 0, 0), "mid": (255, 255, 0), "high": (0, 200, 0), "bg": (20, 20, 20)},
    }

    def _lerp_data_color(self, t: float, palette: str) -> tuple:
        stops = self._DATA_PALETTES.get(palette, self._DATA_PALETTES["thermal"])
        if t <= 0.5:
            c0, c1 = stops["low"], stops["mid"]
            lt = t * 2
        else:
            c0, c1 = stops["mid"], stops["high"]
            lt = (t - 0.5) * 2
        return tuple(int(c0[i] + (c1[i] - c0[i]) * lt) for i in range(3))

    def _generate_art_image(self) -> "Image.Image":  
        style = self.art_style_var.get()  
        size = int(self.art_size_var.get())  
        data = self._extract_art_data()  
        bg = art._art_bg(self.theme_var.get())  
        return art.generate_art(style, data, size, bg, bp_df=self.bp_data)
  
    def _on_generate_art(self):  
        if not PIL_AVAILABLE:  
            messagebox.showerror("Error", "Pillow not installed.\nRun: pip install Pillow")  
            return  
        style = self.art_style_var.get()  
        data_key = self.art_data_var.get()  
        if style in ("daily_mosaic", "scribble", "kandinsky",  
                     "mondrian", "rothko", "pollock", "bauhaus", "wild_mix"):  
            dates = self._get_dates_for_data_source(data_key)  
            if not dates:  
                label = self._ART_DATA_LABELS.get(data_key, data_key)  
                self.art_info_var.set(f"❌ No {label} data.")  
                return  
            if not self.art_period_select_var.get():  
                self.art_info_var.set("❌ Select a period from the dropdown.")  
                return  
        elif style == "sleep_heatmap":  
            if not self.garmin_data.get("sleep"):  
                self.art_info_var.set("❌ No sleep data.")  
                return  
        elif style == "bp_wave":  
            if self.bp_data is None or self.bp_data.empty:  
                self.art_info_var.set("❌ No blood pressure data.")  
                return  
  
        c = self._get_chart_colors()  
        self.art_preview_label.config(image="", text="Generating…",  
                                      bg=c["fig_bg"], fg=c["text"])  
        self.art_info_var.set("Generating…")  
        self.root.update()  
  
        def work():  
            try:  
                img = self._generate_art_image()  
                self._art_image = img  
  
                def _show():  
                    preview_size = 500  
                    ratio = preview_size / max(img.size[0], img.size[1])  
                    preview = img.resize((int(img.size[0] * ratio),  
                                          int(img.size[1] * ratio)),  
                                         Image.LANCZOS)  
                    self._art_photo = ImageTk.PhotoImage(preview)  
                    self.art_preview_label.config(image=self._art_photo, text="")  
                    label = self._ART_DATA_LABELS.get(data_key, data_key)  
                    self.art_info_var.set(  
                        f"✅ {img.size[0]}×{img.size[1]} px  |  {style}  |  "  
                        f"{label}  |  {self.art_period_select_var.get()}")  
                self.root.after(0, _show)  
  
            except Exception as e:  
                err_msg = str(e)  
                def _err():  
                    self.art_info_var.set(f"❌ {err_msg}")  
                    self.art_preview_label.config(  
                        image="", text=f"Error: {err_msg}",  
                        bg="#2a1a1a", fg="#e74c3c")  
                self.root.after(0, _err)  
  
        threading.Thread(target=work, daemon=True).start()  
  
    def _on_save_art(self):  
        if self._art_image is None:  
            messagebox.showinfo("Info", "Generate art first.")  
            return  
        path = filedialog.asksaveasfilename(  
            title="Save Art",  
            defaultextension=".png",  
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")],  
            initialfile=f"art_{self.art_style_var.get()}_"  
                        f"{self.art_data_var.get()}_"  
                        f"{self.art_period_select_var.get() or 'all'}"  
                        f"_{date.today().isoformat()}.png",  
            initialdir=str(DATA_DIR))  
        if not path:  
            return  
        self._art_image.save(path)  
        self.art_info_var.set(f"💾 Saved: {Path(path).name}")

        
    # ─── Overview Tab ───────────────────────────────────────────────────────

    _SECTION_COLORS = {
        "Daily Stats":    "#1a2a4a",
        "Heart Rate":     "#4a1a1a",
        "Stress":         "#2a1a4a",
        "Sleep":          "#0a1a3a",
        "Respiration":    "#0a3a3a",
        "Body Battery":   "#1a3a1a",
        "Blood Pressure": "#3a2a0a",
    }

    def _bp_cell_bg(self, val_str, is_dia=False, is_pulse=False) -> str:
        try:
            v = float(val_str)
        except (ValueError, TypeError):
            return "#3a2a0a"
        if is_pulse:
            return "#2E7D32"
        if is_dia:
            if v >= 110:
                return "#8B0000"
            elif v >= 100:
                return "#CC5500"
            elif v >= 90:
                return "#8B8B00"
            else:
                return "#2E7D32"
        else:
            if v >= 180:
                return "#8B0000"
            elif v >= 160:
                return "#CC5500"
            elif v >= 140:
                return "#8B8B00"
            else:
                return "#2E7D32"

    def _build_overview_tab(self):
        frame = ttk.Frame(self.tab_overview)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="📊 Generate", bootstyle="primary",
               command=self._on_overview_generate).pack(side=tk.LEFT, padx=5)
        Button(top, text="Export ODS", bootstyle="success",
               command=self._on_overview_export).pack(side=tk.LEFT, padx=5)
        self.overview_info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.overview_info_var).pack(side=tk.LEFT, padx=15)

        self.overview_notebook = ttk.Notebook(frame)
        self.overview_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._overview_trees = {}
        self._overview_headers = {}
        self._overview_export_data = []

    def _fmt_hm(self, minutes):
        if minutes is None or (isinstance(minutes, float) and pd.isna(minutes)):
            return ""
        m = int(round(minutes))
        return f"{m // 60}:{m % 60:02d}"

    def _make_section_tree(self, parent, section_name, headers):
        tag = f"sec_{section_name.replace(' ', '_')}"
        tag_m = f"secm_{section_name.replace(' ', '_')}"
        color = self._SECTION_COLORS.get(section_name, "#1a2a4a")

        tab = ttk.Frame(parent)
        self.overview_notebook.add(tab, text=f"  {section_name}  ")

        cols = ["Period"] + [f"V{i}" for i in range(1, 10)]
        tree = ttk.Treeview(tab, columns=cols, show="headings", height=25)
        ysb = tk.Scrollbar(tab, orient=tk.VERTICAL, command=tree.yview)
        xsb = tk.Scrollbar(tab, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)

        tree.heading("Period", text="Period")
        tree.column("Period", width=130, anchor=tk.W)
        for i, h in enumerate(headers, 1):
            tree.heading(f"V{i}", text=h)
            tree.column(f"V{i}", width=90, anchor=tk.CENTER)

        tree.tag_configure(tag, background="#1a2a4a", foreground="#ffffff")
        tree.tag_configure(tag_m, background="#1a2a4a", foreground="#ffffff",  
                           font=("TkDefaultFont", 10, "bold"))  
        tag_w = f"secw_{section_name.replace(' ', '_')}"  
        tree.tag_configure(tag_w, background="#F5F5DC", foreground="#333333")

        self._overview_trees[section_name] = tree
        self._overview_headers[section_name] = headers
        return tree

    def _on_overview_generate(self):
        if not self.garmin_data:
            self.overview_info_var.set("❌ No data. Download or import first.")
            return
        self.overview_info_var.set("Generating…")
        self.root.update()

        def work():
            for child in self.overview_notebook.winfo_children():
                child.destroy()
            self._overview_trees = {}
            self._overview_headers = {}
            self._overview_export_data = []

            dfs = {}
            for key in ("daily_stats", "heart_rate", "stress", "sleep", "respiration"):
                data = self.garmin_data.get(key, [])
                if data:
                    df = pd.DataFrame(data)
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.sort_values("date").reset_index(drop=True)
                    dfs[key] = df

            bb_data = self.garmin_data.get("body_battery", [])
            if bb_data:
                rows = []
                for e in bb_data:
                    d = e.get("date")
                    v = e.get("value")
                    if d and v is not None:
                        rows.append({"date": d, "value": v})
                if rows:
                    df = pd.DataFrame(rows)
                    df["date"] = pd.to_datetime(df["date"])
                    dfs["body_battery"] = df.sort_values("date").reset_index(drop=True)

            if self.bp_data is not None and not self.bp_data.empty:
                bp_copy = self.bp_data.copy()
                bp_copy["date"] = bp_copy["datetime"]
                dfs["bp"] = bp_copy.sort_values("date").reset_index(drop=True)

            if not dfs:
                self.root.after(0, lambda: self.overview_info_var.set("❌ No valid data."))
                return

            def week_label(d):
                iso = d.isocalendar()
                return f"{iso[0]}-W{iso[1]:02d}"

            def month_label(d):
                return f"{d.year}-{d.month:02d}"

            def compute_daily(sub):
                vals = []
                for col in ("steps", "distance_km", "calories"):
                    s = sub[col].dropna()
                    if s.empty:
                        vals += ["", "", ""]
                    else:
                        fmt = "{:.1f}" if col == "distance_km" else "{:.0f}"
                        vals += [fmt.format(s.min()), fmt.format(s.max()), fmt.format(s.mean())]
                return vals

            def compute_hr(sub):
                vals = []
                for col in ("resting_hr", "max_hr", "min_hr"):
                    s = sub[col].dropna()
                    if s.empty:
                        vals += ["", "", ""]
                    else:
                        vals += [f"{s.min():.0f}", f"{s.max():.0f}", f"{s.mean():.0f}"]
                return vals

            def compute_stress(sub):
                vals = []
                for col in ("avg_stress", "max_stress", "min_stress"):
                    s = sub[col].dropna()
                    if s.empty:
                        vals += ["", "", ""]
                    else:
                        vals += [f"{s.min():.0f}", f"{s.max():.0f}", f"{s.mean():.0f}"]
                return vals

            def compute_sleep(sub):
                vals = []
                for col in ("sleep_time_min", "deep_sleep_min", "light_sleep_min",
                            "rem_sleep_min", "awake_min"):
                    s = sub[col].dropna()
                    vals.append(self._fmt_hm(s.mean()) if not s.empty else "")
                age = self.settings.get("age", 30)
                scores = [
                    compute_sleep_quality_score(r.deep_sleep_min, r.light_sleep_min,
                                                r.rem_sleep_min, r.awake_min,
                                                r.sleep_time_min, age)
                    for r in sub.itertuples()
                ]
                scores = [s for s in scores if s is not None]
                vals.append(f"{np.mean(scores):.0f}" if scores else "")
                return vals

            def compute_resp(sub):
                vals = []
                for col in ("avg_respiration", "max_respiration", "min_respiration"):
                    s = sub[col].dropna()
                    if s.empty:
                        vals += ["", "", ""]
                    else:
                        vals += [f"{s.min():.1f}", f"{s.max():.1f}", f"{s.mean():.1f}"]
                return vals

            def compute_bb(sub):
                s = sub["value"].dropna()
                return [f"{s.mean():.0f}"] if not s.empty else [""]

            def compute_bp(sub):
                vals = []
                for col in ("sys", "dia"):
                    if col in sub.columns:
                        s = sub[col].dropna()
                        if s.empty:
                            vals += ["", ""]
                        else:
                            vals += [f"{s.min():.0f}", f"{s.max():.0f}", f"{s.mean():.0f}"]
                    else:
                        vals += ["", ""]
                return vals
            
            def compute_bp_day(sub_day):  
                """Return list of (label_suffix, vals) rows for one day."""  
                use_2x2 = self.settings.get("bp_2x2_measure", False)  
                gap = pd.Timedelta(minutes=int(self.settings.get("bp_measure_gap_min", 5)))  
  
                def avg_vals(s):  
                    return [f"{s['sys'].mean():.0f}", f"{s['sys'].max():.0f}",  
                            f"{s['sys'].min():.0f}",  
                            f"{s['dia'].mean():.0f}", f"{s['dia'].max():.0f}",  
                            f"{s['dia'].min():.0f}"]  
  
                def find_pair(half):  
                    """Two readings within `gap` of each other → their mean row."""  
                    half = half.sort_values("datetime")  
                    for i in range(len(half) - 1):  
                        if half["datetime"].iloc[i + 1] - half["datetime"].iloc[i] <= gap:  
                            return avg_vals(half.iloc[i:i + 2])  
                    return None  
  
                if not use_2x2:  
                    return [("", avg_vals(sub_day))]  
                morning = sub_day[sub_day["datetime"].dt.hour < 12]  
                evening = sub_day[sub_day["datetime"].dt.hour >= 12]  
                m_vals = find_pair(morning) if len(morning) >= 2 else None  
                e_vals = find_pair(evening) if len(evening) >= 2 else None  
                if m_vals is None and e_vals is None:  
                    return [("", avg_vals(sub_day))]          # fallback: day average  
                rows = []  
                if m_vals is not None:  
                    rows.append((" mrn", m_vals))  
                if e_vals is not None:  
                    rows.append((" eve", e_vals))  
                return rows

            sections = [
                ("Daily Stats", "daily_stats",
                 ["Steps Min", "Steps Max", "Steps Avg",
                  "Dist Min", "Dist Max", "Dist Avg",
                  "Cal Min", "Cal Max", "Cal Avg"],
                 compute_daily),
                ("Heart Rate", "heart_rate",
                 ["Rest Min", "Rest Max", "Rest Avg",
                  "MaxHR", "MaxHR Max", "MaxHR Avg",
                  "MinHR", "MinHR Min", "MinHR Avg"],
                 compute_hr),
                ("Stress", "stress",
                 ["Avg Min", "Avg Max", "Avg Avg",
                  "Max Min", "Max Max", "Max Avg",
                  "Min Min", "Min Max", "Min Avg"],
                 compute_stress),
                ("Sleep", "sleep",
                 ["Total", "Deep", "Light", "REM", "Awake", "Score"],
                 compute_sleep),
                ("Respiration", "respiration",
                 ["Avg Min", "Avg Max", "Avg Avg",
                  "Max Min", "Max Max", "Max Avg",
                  "Min Min", "Min Max", "Min Avg"],
                 compute_resp),
                ("Body Battery", "body_battery",
                 ["Avg"],
                 compute_bb),
                ("Blood Pressure", "bp",
                 ["Sys Min", "Sys Max", "Sys Avg",
                  "Dia Min", "Dia Max", "Dia Avg"],
                 compute_bp),
            ]

            for sec_name, df_key, headers, compute_fn in sections:
                if df_key not in dfs:
                    continue
                df = dfs[df_key].copy()
                df["week"] = df["date"].apply(week_label)
                df["month"] = df["date"].apply(month_label)

                if sec_name == "Blood Pressure":
                    tab = ttk.Frame(self.overview_notebook)
                    self.overview_notebook.add(tab, text="  Blood Pressure  ")

                    canvas = tk.Canvas(tab, highlightthickness=0)
                    ysb = tk.Scrollbar(tab, orient=tk.VERTICAL, command=canvas.yview)
                    inner = tk.Frame(canvas)
                    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
                    canvas.create_window((0, 0), window=inner, anchor="nw")
                    canvas.configure(yscrollcommand=ysb.set)
                    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                    ysb.pack(side=tk.RIGHT, fill=tk.Y)

                    all_headers = ["Period"] + headers
                    for col, text in enumerate(all_headers):
                        lbl = tk.Label(inner, text=text, bg="#3a2a0a", fg="#ffffff",
                                       font=("TkDefaultFont", 9, "bold"),
                                       relief=tk.RAISED, padx=10, pady=4)
                        lbl.grid(row=0, column=col, sticky="nsew")

                    row_idx = 1
                    months = sorted(df["month"].unique())
                    for mo in months:
                        sub = df[df["month"] == mo]
                        vals = compute_fn(sub)
                        lbl = tk.Label(inner, text=f"{mo}", bg="#1a2a4a", fg="#ffffff",  
                                       font=("TkDefaultFont", 9, "bold"),  
                                       relief=tk.RAISED, padx=10, pady=4)
                        lbl.grid(row=row_idx, column=0, sticky="nsew")
                        for i, val in enumerate(vals):
                            is_dia = (3 <= i < 6)
                            is_pulse = (i >= 6)
                            cell_bg = self._bp_cell_bg(val, is_dia=is_dia, is_pulse=is_pulse)
                            lbl = tk.Label(inner, text=val, bg=cell_bg, fg="#ffffff",
                                           font=("TkDefaultFont", 9),
                                           relief=tk.RAISED, padx=10, pady=4)
                            lbl.grid(row=row_idx, column=i + 1, sticky="nsew")
                        self._overview_export_data.append(
                            {"section": sec_name, "type": "monthly",
                             "period": mo, "values": vals, "headers": headers})
                        row_idx += 1

                    for col in range(len(all_headers)):
                        lbl = tk.Label(inner, text="", bg="#2a2a2a",
                                       relief=tk.RAISED, padx=10, pady=2)
                        lbl.grid(row=row_idx, column=col, sticky="nsew")
                    row_idx += 1

                    weeks = sorted(df["week"].unique())  
                    for wk in weeks:  
                        sub = df[df["week"] == wk]  
                        vals = compute_fn(sub)  
                        lbl = tk.Label(inner, text=wk, bg="#F5F5DC", fg="#333333",  
                                       font=("TkDefaultFont", 9),  
                                       relief=tk.RAISED, padx=10, pady=4) 
                        lbl.grid(row=row_idx, column=0, sticky="nsew")  
                        for i, val in enumerate(vals):  
                            is_dia = (3 <= i < 6)  
                            cell_bg = self._bp_cell_bg(val, is_dia=is_dia)  
                            lbl = tk.Label(inner, text=val, bg=cell_bg, fg="#ffffff",  
                                           font=("TkDefaultFont", 9),  
                                           relief=tk.RAISED, padx=10, pady=4)  
                            lbl.grid(row=row_idx, column=i + 1, sticky="nsew")  
                        self._overview_export_data.append(  
                            {"section": sec_name, "type": "weekly",  
                             "period": wk, "values": vals, "headers": headers})  
                        row_idx += 1  
  
                        # ── day rows under this week ──  
                        for day, sub_day in sub.groupby(sub["date"].dt.date):  
                            for suffix, dvals in compute_bp_day(sub_day):  
                                lbl = tk.Label(inner, text=f"{day}{suffix}",  
                                               bg="#419141", fg="#ffffff",  
                                               font=("TkDefaultFont", 9),  
                                               relief=tk.RAISED, padx=10, pady=4)  
                                lbl.grid(row=row_idx, column=0, sticky="nsew")  
                                for i, val in enumerate(dvals):  
                                    is_dia = (3 <= i < 6)  
                                    cell_bg = self._bp_cell_bg(val, is_dia=is_dia)  
                                    lbl = tk.Label(inner, text=val, bg=cell_bg, fg="#ffffff",  
                                                   font=("TkDefaultFont", 9),  
                                                   relief=tk.RAISED, padx=10, pady=4)  
                                    lbl.grid(row=row_idx, column=i + 1, sticky="nsew")  
                                self._overview_export_data.append(  
                                    {"section": sec_name, "type": "daily",  
                                     "period": f"{day}{suffix}", "values": dvals,  
                                     "headers": headers})  
                                row_idx += 1

                    self._overview_headers[sec_name] = headers

                else:
                    tree = self._make_section_tree(self.overview_notebook, sec_name, headers)
                    tag = f"sec_{sec_name.replace(' ', '_')}"

                    months = sorted(df["month"].unique())
                    for mo in months:
                        sub = df[df["month"] == mo]
                        vals = compute_fn(sub)
                        row_vals = [f"{mo}"] + vals + [""] * (9 - len(vals))
                        tree.insert("", tk.END, values=row_vals[:10], tags=(tag,))
                        self._overview_export_data.append(
                            {"section": sec_name, "type": "monthly",
                             "period": mo, "values": vals, "headers": headers})

                    tree.insert("", tk.END, values=[""] * 10, tags=())

                    tag_w = f"secw_{sec_name.replace(' ', '_')}"  
                    weeks = sorted(df["week"].unique())  
                    for wk in weeks:  
                        sub = df[df["week"] == wk]  
                        vals = compute_fn(sub)  
                        row_vals = [wk] + vals + [""] * (9 - len(vals))  
                        tree.insert("", tk.END, values=row_vals[:10], tags=(tag_w,))
                        self._overview_export_data.append(
                            {"section": sec_name, "type": "weekly",
                             "period": wk, "values": vals, "headers": headers})

                    tree.insert("", tk.END, values=[""] * 10, tags=())

            self.root.after(0, lambda: self.overview_info_var.set("✅ Done"))

        threading.Thread(target=work, daemon=True).start()

    def _on_overview_export(self):
        if not self._overview_export_data:
            messagebox.showinfo("Info", "Generate the overview first.")
            return
        if not ODS_AVAILABLE:
            messagebox.showerror("Error", "odfpy not installed.\nRun: pip install odfpy")
            return
        path = filedialog.asksaveasfilename(
            title="Export Overview to ODS",
            defaultextension=".ods",
            filetypes=[("ODS files", "*.ods")],
            initialfile=f"overview_{date.today().isoformat()}.ods",
            initialdir=str(DATA_DIR))
        if not path:
            return

        def work():
            try:
                from odf.opendocument import OpenDocumentSpreadsheet
                from odf.table import Table, TableRow, TableCell
                from odf.text import P
                from odf.style import Style, TextProperties, TableCellProperties

                doc = OpenDocumentSpreadsheet()

                styles = {}
                for sec_name, color in self._SECTION_COLORS.items():
                    key = sec_name.replace(" ", "_")
                    s = Style(name=f"cell_{key}", family="table-cell")
                    s.addElement(TableCellProperties(backgroundcolor=color))
                    doc.automaticstyles.addElement(s)
                    styles[key] = f"cell_{key}"
                    s_b = Style(name=f"cellb_{key}", family="table-cell")
                    s_b.addElement(TableCellProperties(backgroundcolor=color))
                    doc.automaticstyles.addElement(s_b)
                    styles[f"{key}_bold"] = f"cellb_{key}"
                    t_b = Style(name=f"text_{key}_bold", family="text")
                    t_b.addElement(TextProperties(fontweight="bold"))
                    doc.automaticstyles.addElement(t_b)

                bp_colors = {
                    "bp_green":  "#2E7D32",
                    "bp_yellow": "#8B8B00",
                    "bp_orange": "#CC5500",
                    "bp_red":    "#8B0000",
                }
                for name, color in bp_colors.items():
                    s = Style(name=f"cell_{name}", family="table-cell")
                    s.addElement(TableCellProperties(backgroundcolor=color))
                    doc.automaticstyles.addElement(s)
                    s_b = Style(name=f"cellb_{name}", family="table-cell")
                    s_b.addElement(TableCellProperties(backgroundcolor=color))
                    doc.automaticstyles.addElement(s_b)

                def _bp_style(val_str, col_idx, bold=False):
                    try:
                        v = float(val_str)
                    except (ValueError, TypeError):
                        return None
                    if col_idx >= 6:
                        name = "bp_green"
                    elif col_idx >= 3:
                        if v >= 110:
                            name = "bp_red"
                        elif v >= 100:
                            name = "bp_orange"
                        elif v >= 90:
                            name = "bp_yellow"
                        else:
                            name = "bp_green"
                    else:
                        if v >= 180:
                            name = "bp_red"
                        elif v >= 160:
                            name = "bp_orange"
                        elif v >= 140:
                            name = "bp_yellow"
                        else:
                            name = "bp_green"
                    return f"cellb_{name}" if bold else f"cell_{name}"

                sections_order = []
                for entry in self._overview_export_data:
                    if entry["section"] not in sections_order:
                        sections_order.append(entry["section"])

                for sec_name in sections_order:
                    entries = [e for e in self._overview_export_data if e["section"] == sec_name]
                    headers = entries[0]["headers"]
                    key = sec_name.replace(" ", "_")
                    style_normal = styles[key]
                    style_bold = styles[f"{key}_bold"]
                    text_bold = f"text_{key}_bold"
                    is_bp = (sec_name == "Blood Pressure")

                    table = Table(name=sec_name[:31])

                    row = TableRow()
                    hcell = TableCell(stylename=style_bold)
                    hcell.addElement(P(text="Period", stylename=text_bold))
                    row.addElement(hcell)
                    for h in headers:
                        cell = TableCell(stylename=style_bold)
                        cell.addElement(P(text=h, stylename=text_bold))
                        row.addElement(cell)
                    table.addElement(row)

                    monthly_entries = [e for e in entries if e["type"] == "monthly"]
                    weekly_entries = [e for e in entries if e["type"] == "weekly"]
                    daily_entries = [e for e in entries if e["type"] == "daily"]  

                    for entry in monthly_entries:
                        row = TableRow()
                        pcell = TableCell(stylename=style_bold)
                        pcell.addElement(P(text=f"{entry['period']}", stylename=text_bold))
                        row.addElement(pcell)
                        for i in range(len(headers)):
                            v = entry["values"][i] if i < len(entry["values"]) else ""
                            if is_bp:
                                cell_style = _bp_style(v, i, bold=True) or style_bold
                            else:
                                cell_style = style_bold
                            cell = TableCell(stylename=cell_style)
                            cell.addElement(P(text=v, stylename=text_bold))
                            row.addElement(cell)
                        table.addElement(row)

                    for _ in range(3):
                        table.addElement(TableRow())#419141

                    for entry in weekly_entries:
                        row = TableRow()
                        pcell = TableCell(stylename=style_normal)
                        pcell.addElement(P(text=entry["period"]))
                        row.addElement(pcell)
                        for i in range(len(headers)):
                            v = entry["values"][i] if i < len(entry["values"]) else ""
                            if is_bp:
                                cell_style = _bp_style(v, i, bold=False) or style_normal
                            else:
                                cell_style = style_normal
                            cell = TableCell(stylename=cell_style)
                            cell.addElement(P(text=v, stylename=text_bold))
                            row.addElement(cell)
                        table.addElement(row)
                        
                    for entry in daily_entries:  
                        row = TableRow()  
                        pcell = TableCell(stylename=style_normal)  
                        pcell.addElement(P(text=entry["period"]))  
                        row.addElement(pcell)  
                        for i in range(len(headers)):  
                            v = entry["values"][i] if i < len(entry["values"]) else ""  
                            cell_style = (_bp_style(v, i, bold=False) or style_normal) if is_bp else style_normal  
                            cell = TableCell(stylename=cell_style)  
                            cell.addElement(P(text=v))  
                            row.addElement(cell)  
                        table.addElement(row)

                    doc.spreadsheet.addElement(table)

                doc.save(path)
                self.root.after(0, lambda: self.overview_info_var.set(
                    f"✅ Exported to {Path(path).name}"))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("Export Error", err_msg))

        threading.Thread(target=work, daemon=True).start()   
