#!/usr/bin/env python3
"""
Garmin Data Viewer – GUI app to fetch & visualize Garmin Connect data,
local .FIT files, and blood pressure data from ODS.

Dependencies:
    pip install "garminconnect==0.3.20" curl_cffi fitparse matplotlib pandas numpy odfpy ttkbootstrap
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap import Button
from datetime import date, timedelta
from pathlib import Path
import json
import threading
import time
import zipfile

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import pandas as pd
import numpy as np

# ─── Garmin Connect API ────────────────────────────────────────────────────────
try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False

# ─── FIT file reader ──────────────────────────────────────────────────────────
try:
    from fitparse import FitFile
    FIT_AVAILABLE = True
except ImportError:
    FIT_AVAILABLE = False

# ─── ODS support ──────────────────────────────────────────────────────────────
try:
    import odf
    ODS_AVAILABLE = True
except ImportError:
    ODS_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
#  PATHS
# ═══════════════════════════════════════════════════════════════════════════════

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "garmin_history.json"
BP_FILE = DATA_DIR / "blood_pressure.ods"   


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {"email": "", "password": "", "export_path": "",
            "token_path": "~/.garminconnect", "bp_file": "",
            "sleep_start": "22:00", "sleep_end": "07:00",
            "theme": "darkly"}


def save_settings(s: dict):
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return {"first_download": None, "last_update": None, "data": {}}


def save_history(history: dict):
    DATA_DIR.mkdir(exist_ok=True)
    serializable = json.loads(json.dumps(history, default=str))
    HISTORY_FILE.write_text(json.dumps(serializable, indent=2))


def get_last_stored_date(history: dict) -> date | None:
    data = history.get("data", {})
    for key in ("daily_stats", "heart_rate", "stress", "sleep", "respiration"):
        entries = data.get(key, [])
        if entries:
            last = max(e["date"] for e in entries if e.get("date"))
            return date.fromisoformat(last)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  COLOR PALETTE
# ═══════════════════════════════════════════════════════════════════════════════

COLORS = {
    "steps":        "#4A90D9",
    "distance":     "#F5A623",
    "calories":     "#E74C3C",
    "active_cal":   "#D35400",
    "resting_hr":   "#2ECC71",
    "max_hr":       "#E74C3C",
    "min_hr":       "#85C1E9",
    "stress":       "#9B59B6",
    "stress_low":   "#2ECC71",
    "stress_med":   "#F1C40F",
    "stress_high":  "#E74C3C",
    "sleep_deep":   "#1B2A4A",
    "sleep_light":  "#5DADE2",
    "sleep_rem":    "#F4D03F",
    "sleep_awake":  "#E74C3C",
    "body_battery": "#27AE60",
    "respiration":  "#1ABC9C",
    "speed":        "#2980B9",
    "elevation":    "#27AE60",
    "cadence":      "#F39C12",
    "power":        "#8E44AD",
    "bp_sys":       "#E74C3C",
    "bp_dia":       "#2980B9",
    "bp_pulse":     "#2ECC71",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  BLOOD PRESSURE (ODS)
# ═══════════════════════════════════════════════════════════════════════════════

def load_bp_data() -> pd.DataFrame | None:
    if not ODS_AVAILABLE:
        return None
    if not BP_FILE.exists():
        return None
    try:
        df = pd.read_excel(str(BP_FILE), engine="odf")
        df.columns = [c.strip().lower() for c in df.columns]
        col_map = {}
        for c in df.columns:
            if "datum" in c or "date" in c:
                col_map[c] = "datum"
            elif "time" in c or "zeit" in c:
                col_map[c] = "time"
            elif "sys" in c:
                col_map[c] = "sys"
            elif "dia" in c:
                col_map[c] = "dia"
            elif "pulse" in c or "herz" in c or "hr" in c:
                col_map[c] = "pulse"
        df = df.rename(columns=col_map)
        try:
            df["datum"] = pd.to_datetime(df["datum"], format="%d.%m.%y", errors="coerce")
        except (ValueError, TypeError):
            df["datum"] = pd.to_datetime(df["datum"], errors="coerce", dayfirst=True)
        df["time"] = pd.to_datetime(df["time"].astype(str).str.strip(),
                                    format="mixed", errors="coerce")
        df["datetime"] = pd.to_datetime(
            df["datum"].dt.strftime("%Y-%m-%d") + " " + df["time"].dt.strftime("%H:%M:%S"),
            errors="coerce"
        )
        for col in ("sys", "dia", "pulse"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["datetime"]).reset_index(drop=True)
        df = df.drop_duplicates().reset_index(drop=True)
        return df if not df.empty else None
    except Exception:
        return None   
        
def save_bp_data(text: str) -> pd.DataFrame | None:
    """Parse pasted tab-separated text and save to ODS. Returns the DataFrame or None."""
    if not ODS_AVAILABLE:
        return None
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return None
    # Parse: header line + data lines, tab-separated
    header = [h.strip().lower() for h in lines[0].split("\t")]
    rows = []
    for line in lines[1:]:
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < len(header):
            parts += [""] * (len(header) - len(parts))
        rows.append(parts[:len(header)])
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=header)
    # Normalize column names
    col_map = {}
    for c in df.columns:
        if "datum" in c or "date" in c:
            col_map[c] = "datum"
        elif "time" in c or "zeit" in c:
            col_map[c] = "time"
        elif "sys" in c:
            col_map[c] = "sys"
        elif "dia" in c:
            col_map[c] = "dia"
        elif "pulse" in c or "herz" in c or "hr" in c:
            col_map[c] = "pulse"
    df = df.rename(columns=col_map)
    # Ensure required columns exist
    for col in ("datum", "time", "sys", "dia", "pulse"):
        if col not in df.columns:
            df[col] = None
    df = df[["datum", "time", "sys", "dia", "pulse"]].reset_index(drop=True)
    # Save to ODS
    DATA_DIR.mkdir(exist_ok=True)
    df.to_excel(str(BP_FILE), engine="odf", index=False)
    return df   

def export_all_to_ods(filepath: str, garmin_data: dict, bp_df: pd.DataFrame | None):
    if not ODS_AVAILABLE:
        raise RuntimeError("odfpy not installed. Run: pip install odfpy")
    with pd.ExcelWriter(filepath, engine="odf") as writer:
        if garmin_data.get("daily_stats"):
            pd.DataFrame(garmin_data["daily_stats"]).to_excel(
                writer, sheet_name="Daily Stats", index=False)
        if garmin_data.get("heart_rate"):
            pd.DataFrame(garmin_data["heart_rate"]).to_excel(
                writer, sheet_name="Heart Rate", index=False)
        if garmin_data.get("stress"):
            pd.DataFrame(garmin_data["stress"]).to_excel(
                writer, sheet_name="Stress", index=False)
        if garmin_data.get("sleep"):
            df = pd.DataFrame(garmin_data["sleep"])
            if "sleep_levels" in df.columns:
                df = df.drop(columns=["sleep_levels"])
            df.to_excel(writer, sheet_name="Sleep", index=False)
        if garmin_data.get("respiration"):
            pd.DataFrame(garmin_data["respiration"]).to_excel(
                writer, sheet_name="Respiration", index=False)
        if garmin_data.get("activities"):
            act_rows = []
            for a in garmin_data["activities"]:
                act_rows.append({
                    "activityId": a.get("activityId"),
                    "name": a.get("activityName"),
                    "type": a.get("activityType", {}).get("typeKey"),
                    "start": a.get("startTimeLocal"),
                    "distance_km": (a.get("distance", 0) or 0) / 1000,
                    "duration_min": (a.get("duration", 0) or 0) / 60,
                    "avg_hr": a.get("averageHeartRate"),
                    "calories": a.get("calories"),
                })
            if act_rows:
                pd.DataFrame(act_rows).to_excel(writer, sheet_name="Activities", index=False)
        if bp_df is not None and not bp_df.empty:
            bp_out = bp_df[["datum", "time", "sys", "dia", "pulse"]].copy()
            bp_out["datum"] = bp_out["datum"].dt.strftime("%Y-%m-%d")
            bp_out["time"] = bp_out["time"].dt.strftime("%H:%M:%S")
            bp_out.to_excel(writer, sheet_name="Blood Pressure", index=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA FETCHERS
# ═══════════════════════════════════════════════════════════════════════════════

def garmin_login(email: str, password: str,
                 token_path: str = "~/.garminconnect",
                 status_cb=None,
                 mfa_callback=None) -> tuple:
    token_path = str(Path(token_path).expanduser())

    def _msg(s):
        if status_cb:
            status_cb(s)

    def _get_mfa():
        if mfa_callback:
            return mfa_callback().strip()
        return input("MFA code: ").strip()

    def _attempt():
        _msg("  → Attempting cached token login…")
        try:
            g = Garmin()
            g.login(token_path)
            return g, None
        except Exception:
            pass
        _msg("  → Cached tokens failed, trying fresh login…")
        try:
            g = Garmin(email, password, prompt_mfa=_get_mfa)
            g.login(token_path)
            return g, None
        except GarminConnectTooManyRequestsError:
            return None, "rate_limited"
        except Exception as e:
            return None, str(e)

    client, err = _attempt()
    if client:
        return client, None
    if err == "rate_limited":
        _msg("  → Rate limited (429). Waiting 15s before retry…")
        time.sleep(15)
        client, err = _attempt()
        if client:
            return client, None
        return None, ("Garmin rate limit (429). Wait ~5 min.\n"
                      "Upgrade: pip install --upgrade garminconnect")
    return None, (err or "Unknown error")


def _safe_call(fn, *args, **kwargs):
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except GarminConnectTooManyRequestsError:
            time.sleep(5 * (attempt + 1))
        except Exception:
            return None
    return None


def find_oldest_activity_date(client: Garmin, status_cb=None) -> date | None:
    def _msg(s):
        if status_cb:
            status_cb(s)
    all_acts = []
    start = 0
    page_size = 100
    page = 0
    while True:
        batch = _safe_call(client.get_activities, start, page_size)
        if not batch:
            break
        all_acts.extend(batch)
        page += 1
        _msg(f"  → Activities page {page}: {len(batch)} items (total {len(all_acts)})")
        if len(batch) < page_size:
            break
        start += page_size
        time.sleep(0.5)
    if not all_acts:
        return None
    oldest = min(all_acts, key=lambda a: a.get("startTimeLocal", "9999"))
    ts = oldest.get("startTimeLocal", "")
    try:
        return date.fromisoformat(ts[:10])
    except Exception:
        return None


def fetch_daily_data(client: Garmin, start: date, end: date, status_cb=None) -> dict:
    def _msg(s):
        if status_cb:
            status_cb(s)
    daily_stats, hr_data, stress_data, sleep_data, resp_data = [], [], [], [], []
    total_days = (end - start).days + 1
    d = start
    i = 0
    while d <= end:
        i += 1
        ds = d.isoformat()
        if i % 10 == 0 or i == 1 or i == total_days:
            _msg(f"  → Daily data: day {i}/{total_days} ({ds})")
        s = _safe_call(client.get_user_summary, ds)
        if s:
            daily_stats.append({
                "date": ds,
                "steps": s.get("totalSteps", 0) or 0,
                "distance_km": (s.get("totalDistanceMeters", 0) or 0) / 1000,
                "calories": s.get("totalKilocalories", 0) or 0,
                "active_calories": s.get("activeKilocalories", 0) or 0,
            })
        hr = _safe_call(client.get_heart_rates, ds)
        if hr:
            hr_data.append({
                "date": ds,
                "resting_hr": hr.get("restingHeartRate"),
                "max_hr": hr.get("maxHeartRate"),
                "min_hr": hr.get("minHeartRate"),
            })
        st = _safe_call(client.get_stress_data, ds)
        if st:
            stress_data.append({
                "date": ds,
                "avg_stress": st.get("avgStressLevel") or st.get("averageStress"),
                "max_stress": st.get("maxStressLevel") or st.get("maxStress"),
                "min_stress": st.get("minStressLevel") or st.get("minStress"),
                "low_minutes": (st.get("lowStressDuration", 0) or 0) / 60,
                "medium_minutes": (st.get("mediumStressDuration", 0) or 0) / 60,
                "high_minutes": (st.get("highStressDuration", 0) or 0) / 60,
            })
        sl = _safe_call(client.get_sleep_data, ds)
        if sl:
            dto = sl.get("dailySleepDTO", {})
            sleep_time = dto.get("sleepTimeSeconds", 0) or 0
            if sleep_time > 0:
                score = None
                scores = dto.get("sleepScores", {})
                if isinstance(scores, dict):
                    overall = scores.get("overall", {})
                    score = overall.get("value") if isinstance(overall, dict) else overall
                sleep_entry = {
                    "date": ds,
                    "sleep_score": score,
                    "sleep_time_min": sleep_time / 60,
                    "deep_sleep_min": (dto.get("deepSleepSeconds", 0) or 0) / 60,
                    "light_sleep_min": (dto.get("lightSleepSeconds", 0) or 0) / 60,
                    "rem_sleep_min": (dto.get("remSleepSeconds", 0) or 0) / 60,
                    "awake_min": (dto.get("awakeSleepSeconds", 0) or 0) / 60,
                }
                levels = sl.get("sleepLevels", [])
                if levels:
                    sleep_entry["sleep_levels"] = [
                        {"start": lv.get("startGMT"), "end": lv.get("endGMT"),
                         "level": int(lv.get("activityLevel", 0))}
                        for lv in levels
                    ]
                sleep_data.append(sleep_entry)
        resp = _safe_call(client.get_respiration_data, ds)
        if resp:
            resp_data.append({
                "date": ds,
                "avg_respiration": (resp.get("averageRespirationRate")
                                    or resp.get("avgRespirationRate")
                                    or resp.get("respirationRate")),
                "max_respiration": (resp.get("maxRespirationRate")
                                    or resp.get("maxRespiration")),
                "min_respiration": (resp.get("minRespirationRate")
                                    or resp.get("minRespiration")),
            })
        d += timedelta(days=1)
        time.sleep(0.3)
    return {
        "daily_stats": daily_stats,
        "heart_rate": hr_data,
        "stress": stress_data,
        "sleep": sleep_data,
        "respiration": resp_data,
    }


def fetch_full_history(client: Garmin, status_cb=None) -> dict:
    def _msg(s):
        if status_cb:
            status_cb(s)
    _msg("🔍 Finding oldest activity date…")
    oldest = find_oldest_activity_date(client, status_cb)
    if oldest is None:
        _msg("  → No activities found. Using 365 days back.")
        oldest = date.today() - timedelta(days=365)
    end = date.today()
    _msg(f"  → Oldest data: {oldest.isoformat()}  |  Today: {end.isoformat()}")
    _msg(f"  → Total days to fetch: {(end - oldest).days + 1}")
    _msg("📋 Fetching all activities…")
    all_acts = []
    start = 0
    page_size = 100
    while True:
        batch = _safe_call(client.get_activities, start, page_size)
        if not batch:
            break
        all_acts.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
        time.sleep(0.5)
    _msg(f"  → {len(all_acts)} activities fetched.")
    _msg("📊 Fetching daily health data…")
    daily = fetch_daily_data(client, oldest, end, status_cb)
    _msg("🔋 Fetching body battery…")
    bb = _safe_call(client.get_body_battery, oldest.isoformat(), end.isoformat())
    if isinstance(bb, list):
        daily["body_battery"] = bb
    elif isinstance(bb, dict):
        daily["body_battery"] = [bb]
    else:
        daily["body_battery"] = []
    _msg(f"  → {len(daily['body_battery'])} body battery entries.")
    _msg("⚖️ Fetching body composition…")
    body = _safe_call(client.get_body_composition, end.isoformat())
    daily["body"] = body if isinstance(body, list) else ([body] if body else [])
    daily["activities"] = all_acts
    return daily


def fetch_incremental(client: Garmin, last_date: date, status_cb=None) -> dict:
    def _msg(s):
        if status_cb:
            status_cb(s)
    end = date.today()
    start = last_date + timedelta(days=1)
    _msg(f"  → Updating from {start.isoformat()} to {end.isoformat()} "
         f"({(end - start).days + 1} new days)")
    _msg("  → Fetching new activities…")
    acts = _safe_call(client.get_activities_by_date, start.isoformat(), end.isoformat())
    new_acts = acts if acts else []
    _msg(f"  → {len(new_acts)} new activities.")
    daily = fetch_daily_data(client, start, end, status_cb)
    _msg("  → Fetching new body battery…")
    bb = _safe_call(client.get_body_battery, start.isoformat(), end.isoformat())
    daily["body_battery"] = bb if isinstance(bb, list) else []
    daily["activities"] = new_acts
    return daily


def merge_data(existing: dict, new: dict) -> dict:
    merged = dict(existing)
    for key in ("daily_stats", "heart_rate", "stress", "sleep", "respiration"):
        old = {e["date"]: e for e in existing.get(key, [])}
        for e in new.get(key, []):
            old[e["date"]] = e
        merged[key] = sorted(old.values(), key=lambda x: x["date"])
    old_acts = {a.get("activityId"): a for a in existing.get("activities", [])}
    for a in new.get("activities", []):
        old_acts[a.get("activityId")] = a
    merged["activities"] = list(old_acts.values())
    old_bb = {e.get("date"): e for e in existing.get("body_battery", []) if isinstance(e, dict)}
    for e in new.get("body_battery", []):
        if isinstance(e, dict) and e.get("date"):
            old_bb[e["date"]] = e
    merged["body_battery"] = sorted(old_bb.values(), key=lambda x: x.get("date", ""))
    if new.get("body"):
        merged["body"] = new["body"]
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
#  FIT FILE READER
# ═══════════════════════════════════════════════════════════════════════════════

def read_fit_file(filepath) -> dict:
    fit = FitFile(filepath)
    data = {"file": Path(filepath).name if isinstance(filepath, str) else "unknown",
            "records": [], "session": {}, "laps": []}
    for msg in fit.get_messages("session"):
        for field in msg:
            if field.name in ("total_distance", "total_time", "avg_heart_rate",
                              "max_heart_rate", "avg_speed", "max_speed",
                              "total_ascent", "total_descent", "sport"):
                data["session"][field.name] = field.value
        break
    for msg in fit.get_messages("lap"):
        lap = {}
        for field in msg:
            if field.name in ("total_distance", "avg_heart_rate", "max_heart_rate",
                              "total_time", "avg_speed", "start_time"):
                lap[field.name] = field.value
        data["laps"].append(lap)
    for msg in fit.get_messages("record"):
        rec = {}
        for field in msg:
            if field.name in ("timestamp", "position_lat", "position_long",
                              "heart_rate", "altitude", "distance",
                              "speed", "cadence", "power", "temperature"):
                rec[field.name] = field.value
        data["records"].append(rec)
    data["records_df"] = pd.DataFrame(data["records"]) if data["records"] else pd.DataFrame()
    return data


def scan_export_folder(root_path: str, status_cb=None) -> dict:
    root = Path(root_path).expanduser()
    fit_files, json_files, total_bytes = [], {}, 0
    if not root.exists():
        return {"fit_files": [], "json_files": {}, "total_size_mb": 0}
    for filepath in root.rglob("*"):
        if not filepath.is_file():
            continue
        total_bytes += filepath.stat().st_size
        suffix = filepath.suffix.lower()
        if suffix == ".fit":
            fit_files.append(filepath)
        elif suffix == ".json":
            name = filepath.stem.lower()
            if "summarizedactivities" in name:
                json_files["activities"] = filepath
            elif "sleep" in name:
                json_files["sleep"] = filepath
            elif "body" in name and "composition" in name:
                json_files["body"] = filepath
            elif "monitoring" in name or "daily" in name:
                json_files["monitoring"] = filepath
            elif "userprofile" in name:
                json_files["profile"] = filepath
        elif suffix == ".zip":
            try:
                with zipfile.ZipFile(filepath, "r") as zf:
                    for info in zf.infolist():
                        if info.filename.lower().endswith(".fit"):
                            fit_files.append((filepath, info.filename))
                        total_bytes += info.file_size
            except Exception:
                pass
    return {"fit_files": fit_files, "json_files": json_files,
            "total_size_mb": total_bytes / (1024 * 1024)}


def read_fit_from_zip(zip_path: Path, inner_name: str) -> dict:
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(inner_name) as f:
            data = read_fit_file(f)
            data["file"] = f"{zip_path.name}/{inner_name}"
            return data


# ═══════════════════════════════════════════════════════════════════════════════
#  STATS HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def compute_stats(series: pd.Series) -> str:
    s = series.dropna()
    if s.empty:
        return "No data"
    return f"Avg: {s.mean():.1f}  |  Min: {s.min():.1f}  |  Max: {s.max():.1f}"


# ═══════════════════════════════════════════════════════════════════════════════
#  GUI APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class GarminViewerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.garmin_client = None
        self.garmin_data: dict = {}
        self.fit_data: dict = {}
        self.export_scan: dict = {}
        self.bp_data: pd.DataFrame | None = None
        self.progress_win = None
        self.settings = load_settings()
        self._build_ui()
        saved_theme = self.settings.get("theme", "darkly")
        try:
            self.root.set_theme(saved_theme)
        except Exception:
            pass
        self._load_existing_data()
        self._redraw_garmin_chart()
        self._redraw_fit_chart()

    def _load_existing_data(self):
        history = load_history()
        if history.get("data"):
            self.garmin_data = history["data"]
            first = history.get("first_download", "?")
            last = history.get("last_update", "?")
            n_days = len(self.garmin_data.get("daily_stats", []))
            self.status_var.set(
                f"Loaded saved data: {first} → {last}  ({n_days} days)")

        def _load_bp_bg():
            df = load_bp_data()
            if df is not None:
                self.bp_data = df
                n = len(df)
                dmin = df["datetime"].min().strftime('%Y-%m-%d')
                dmax = df["datetime"].max().strftime('%Y-%m-%d')
                def _update_ui():
                    if hasattr(self, 'bp_tab_info_var'):
                        self.bp_tab_info_var.set(f"Auto-loaded {n} readings.  Range: {dmin} → {dmax}")
                    self.status_var.set(f"BP data loaded: {n} readings.")
                    self._populate_bp_table()
                    if self.garmin_chart_var.get() == "blood_pressure":
                        self._redraw_garmin_chart()
                self.root.after(200, _update_ui)
        threading.Thread(target=_load_bp_bg, daemon=True).start()   

        export_path = self.settings.get("export_path", "")
        if export_path and Path(export_path).expanduser().exists():
            def _scan_bg():
                result = scan_export_folder(export_path)
                self.export_scan = result
                n_fit = len(result["fit_files"])
                n_json = len(result["json_files"])
                info = (f"Auto-scanned: {n_fit} FIT file(s), "
                        f"{n_json} JSON file(s), "
                        f"total {result['total_size_mb']:.1f} MB.")
                def _update_ui():
                    if hasattr(self, 'export_info_var'):
                        self.export_info_var.set(info)
                        self.status_var.set(f"Export folder scanned: {n_fit} FIT files.")
                self.root.after(200, _update_ui)
            threading.Thread(target=_scan_bg, daemon=True).start()

    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_settings, text="  Settings  ")
        self._build_settings_tab()
        self.tab_garmin = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_garmin, text="  Garmin Connect  ")
        self._build_garmin_tab()
        self.tab_fit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_fit, text="  FIT Files  ")
        self._build_fit_tab()
        self.tab_bp = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_bp, text="  Blood Pressure  ")
        self._build_bp_tab()
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)   

    # ─── Progress Dialog ────────────────────────────────────────────────────

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
        self.progress_bar = ttk.Progressbar(self.progress_win, mode="indeterminate",
                                            length=450)
        self.progress_bar.pack(pady=10, padx=20)
        self.progress_bar.start(10)
        self.progress_detail = tk.Label(self.progress_win, text="",
                                        font=("TkDefaultFont", 9), wraplength=500,
                                        fg="gray")
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

    # ─── MFA GUI Prompt ─────────────────────────────────────────────────────

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

    # ─── Theme ──────────────────────────────────────────────────────────────

    def _apply_theme(self):
        theme = self.theme_var.get()
        try:
            self.root.style.theme_use(theme)
            self.settings["theme"] = theme
            save_settings(self.settings)
        except Exception as e:
            messagebox.showerror("Theme Error", f"Could not apply theme:\n{e}")
        self._redraw_garmin_chart()
        self._redraw_fit_chart()
        
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
        self._redraw_garmin_chart()
        self._redraw_fit_chart()   
        
        
    def _get_chart_colors(self) -> dict:
        dark_themes = {"darkly", "superhero", "solar", "cyborg"}
        current = self.theme_var.get() if hasattr(self, 'theme_var') else "darkly"
        if current in dark_themes:
            return {
                "fig_bg":    "#3a3a3a",
                "ax_bg":     "#3a3a3a",
                "text":      "#e0e0e0",
                "grid":      "#666666",
                "spine":     "#666666",
                "legend_bg": "#4a4a4a",
            }
        return {
            "fig_bg":    "#ffffff",
            "ax_bg":     "#ffffff",
            "text":      "#222222",
            "grid":      "#cccccc",
            "spine":     "#cccccc",
            "legend_bg": "#ffffff",
        }

    def _style_figure(self, fig, ax, twin_ax=None):
        c = self._get_chart_colors()
        fig.set_facecolor(c["fig_bg"])
        ax.set_facecolor(c["ax_bg"])
        ax.tick_params(colors=c["text"], which="both")
        ax.xaxis.label.set_color(c["text"])
        ax.yaxis.label.set_color(c["text"])
        ax.title.set_color(c["text"])
        for spine in ax.spines.values():
            spine.set_color(c["spine"])
        ax.grid(True, alpha=0.3, color=c["grid"])
        if twin_ax is not None:
            twin_ax.set_facecolor(c["ax_bg"])
            twin_ax.tick_params(colors=c["text"], which="both")
            twin_ax.yaxis.label.set_color(c["text"])
            for spine in twin_ax.spines.values():
                spine.set_color(c["spine"])
        # Restyle scrollbars
        for sb in (getattr(self, 'xscrollbar_garmin', None),
                   getattr(self, 'xscrollbar_fit', None)):
            if sb is not None:
                sb.config(troughcolor=c["fig_bg"], background=c["spine"],
                          activebackground=c["text"])   
        


    # ─── Settings Tab ───────────────────────────────────────────────────────

    def _build_settings_tab(self):
        frame = ttk.Frame(self.tab_settings)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Theme
        theme_frame = ttk.LabelFrame(frame, text="Theme")
        theme_frame.pack(fill=tk.X, padx=5, pady=10)
        ttk.Label(theme_frame, text="Theme:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "darkly"))
        themes = [
            "darkly", "superhero", "solar", "cyborg",
            "cosmo", "flatly", "journal", "litera",
            "lumen", "minty", "pulse", "sandstone",
            "united", "yeti",
        ]   
        self.theme_combo = ttk.Combobox(theme_frame, textvariable=self.theme_var,
                                        values=themes, width=18, state="readonly")
        self.theme_combo.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        Button(theme_frame, text="Apply", bootstyle="primary",
       command=self._apply_theme).grid(row=0, column=2, padx=5)
        Button(theme_frame, text="🌙/☀ Toggle", bootstyle="secondary",
               command=self._toggle_dark).grid(row=0, column=3, padx=5)



        # Login
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
        ttk.Label(login_frame, text="Token path:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        self.token_path_var = tk.StringVar(value=self.settings.get("token_path", "~/.garminconnect"))
        ttk.Entry(login_frame, textvariable=self.token_path_var, width=40).grid(
            row=2, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)
        login_frame.columnconfigure(1, weight=1)

        # Export Folder
        export_frame = ttk.LabelFrame(frame, text="Data Folder")
        export_frame.pack(fill=tk.X, padx=5, pady=10)
        ttk.Label(export_frame, text="Path:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.export_path_var = tk.StringVar(value=self.settings.get("export_path", ""))
        ttk.Entry(export_frame, textvariable=self.export_path_var, width=50).grid(
            row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        Button(export_frame, text="Browse…",
               command=self._browse_export_path).grid(row=0, column=2, padx=5)
        Button(export_frame, text="Scan Folder", bootstyle="info",
               command=self._on_scan_export).grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        Button(export_frame, text="Show Folder Structure",
               command=self._on_show_structure).grid(row=1, column=2, padx=5, pady=5, sticky=tk.W)

       

        # Sleep Time Range
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
        ttk.Label(sleep_frame, text="(e.g. 22:00 → 07:00, or 20:00 → 09:00 for ±2h context)",
                  foreground="gray").grid(row=1, column=0, columnspan=4, padx=5, pady=2, sticky=tk.W)

        # Save
        Button(frame, text="Save Settings", bootstyle="success",
               command=self._save_settings).pack(anchor=tk.W, padx=5, pady=10)   
        export_frame.columnconfigure(1, weight=1)
        
    def _browse_export_path(self):
        path = filedialog.askdirectory(title="Select Garmin Export Folder")
        if path:
            self.export_path_var.set(path)

    def _browse_bp_file(self):
        path = filedialog.askopenfilename(
            title="Select Blood Pressure ODS file",
            filetypes=[("ODS files", "*.ods"), ("All files", "*.*")])
        if path:
            self.bp_file_var.set(path)

    def _on_load_bp(self):
        path = self.bp_file_var.get().strip()
        if not path:
            messagebox.showwarning("Warning", "Enter the ODS file path first.")
            return
        if not ODS_AVAILABLE:
            messagebox.showerror("Error", "odfpy not installed.\nRun: pip install odfpy")
            return
        self.status_var.set("Loading blood pressure data…")
        self.root.update()
        def work():
            df = load_bp_data(path)
            if df is None:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", f"Could not read ODS file or no valid rows:\n{path}"))
                self.root.after(0, lambda: self.bp_info_var.set("Failed to load."))
                return
            self.bp_data = df
            n = len(df)
            dmin = df["datetime"].min().strftime('%Y-%m-%d')
            dmax = df["datetime"].max().strftime('%Y-%m-%d')
            info = f"Loaded {n} readings.  Range: {dmin} → {dmax}"
            self.root.after(0, lambda: self.bp_info_var.set(info))
            self.root.after(0, lambda: self.status_var.set(f"BP data loaded: {n} readings."))
            if self.garmin_chart_var.get() == "blood_pressure":
                self.root.after(0, self._redraw_garmin_chart)
        threading.Thread(target=work, daemon=True).start()

    def _save_settings(self):
        self.settings = {
            "email": self.email_var.get().strip(),
            "password": self.password_var.get().strip(),
            "export_path": self.export_path_var.get().strip(),
            "token_path": self.token_path_var.get().strip(),
            "sleep_start": self.sleep_start_var.get().strip(),
            "sleep_end": self.sleep_end_var.get().strip(),
            "theme": self.theme_var.get().strip(),
        }   
        save_settings(self.settings)
        self.status_var.set(f"Settings saved to {SETTINGS_PATH}")

    def _on_scan_export(self):
        path = self.export_path_var.get().strip()
        if not path:
            messagebox.showwarning("Warning", "Enter an export folder path first.")
            return
        if not Path(path).expanduser().exists():
            messagebox.showerror("Error", f"Path does not exist:\n{path}")
            return
        self.status_var.set("Scanning export folder…")
        self.root.update()
        def work():
            result = scan_export_folder(path, status_cb=lambda msg: self._set_status(msg))
            self.export_scan = result
            n_fit = len(result["fit_files"])
            n_json = len(result["json_files"])
            info = (f"Found {n_fit} FIT file(s), {n_json} JSON file(s), "
                    f"total {result['total_size_mb']:.1f} MB.")
            self.root.after(0, lambda: self.export_info_var.set(info))
            self._set_status(f"Scan complete: {n_fit} FIT files.")
        threading.Thread(target=work, daemon=True).start()

    def _on_show_structure(self):
        path = self.export_path_var.get().strip()
        if not path:
            messagebox.showwarning("Warning", "Enter a path first.")
            return
        if not Path(path).expanduser().exists():
            messagebox.showerror("Error", f"Path does not exist:\n{path}")
            return
        def work():
            root = Path(path).expanduser()
            lines = []
            fit_count = zip_count = json_count = total_files = 0
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    total_files += 1
                    s = p.suffix.lower()
                    if s == ".fit": fit_count += 1
                    elif s == ".zip": zip_count += 1
                    elif s == ".json": json_count += 1
            zip_fit_count = 0
            for p in root.rglob("*.zip"):
                try:
                    with zipfile.ZipFile(p, "r") as zf:
                        zip_fit_count += sum(
                            1 for i in zf.infolist() if i.filename.lower().endswith(".fit"))
                except Exception:
                    pass
            lines.append(f"Root: {root}")
            lines.append(f"Total files: {total_files}")
            lines.append(f".fit (direct): {fit_count}  |  .fit (in ZIPs): {zip_fit_count}")
            lines.append(f".zip: {zip_count}  |  .json: {json_count}")
            lines.append("")
            lines.append("── Top-level ──")
            for p in sorted(root.iterdir()):
                lines.append(f"  [{'DIR' if p.is_dir() else '   '}] {p.name}")
            lines.append("")
            lines.append("── First 20 .fit ──")
            c = 0
            for p in root.rglob("*.fit"):
                if c >= 20: lines.append("  ..."); break
                lines.append(f"  {p.relative_to(root)}"); c += 1
            if c == 0: lines.append("  (none)")
            result = "\n".join(lines)
            self.root.after(0, lambda r=result: messagebox.showinfo(
                "Folder Structure", r[:5000]))
        threading.Thread(target=work, daemon=True).start()

    # ─── Garmin Connect Tab ─────────────────────────────────────────────────

    def _build_garmin_tab(self):
        frame = ttk.Frame(self.tab_garmin)
        frame.pack(fill=tk.BOTH, expand=True)
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="⬇ Full Download", bootstyle="primary",
               command=self._on_full_download).pack(side=tk.LEFT, padx=5)
        Button(top, text="🔄 Update", bootstyle="info",
               command=self._on_update).pack(side=tk.LEFT, padx=5)
        Button(top, text="💾 Export ODS", bootstyle="success",
               command=self._on_export_ods).pack(side=tk.LEFT, padx=5)
        Button(top, text="🔍+", width=3, bootstyle="secondary",
               command=self._zoom_in).pack(side=tk.LEFT, padx=2)
        Button(top, text="🔍−", width=3, bootstyle="secondary",
               command=self._zoom_out).pack(side=tk.LEFT, padx=2)
        Button(top, text="⊡ Reset", width=5, bootstyle="outline",
               command=self._zoom_reset).pack(side=tk.LEFT, padx=2)
        self.garmin_info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.garmin_info_var).pack(side=tk.LEFT, padx=15)

        chart_frame = ttk.LabelFrame(frame, text="Metric")
        chart_frame.pack(fill=tk.X, padx=5, pady=5)
        self.garmin_chart_var = tk.StringVar(value="steps")
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
                            variable=self.garmin_chart_var,
                            command=self._redraw_garmin_chart).grid(
                row=i // 5, column=i % 5, padx=10, pady=3, sticky=tk.W)

        self.stats_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.stats_var, anchor=tk.W,
                  font=("TkDefaultFont", 10)).pack(fill=tk.X, padx=10, pady=2)

        self.fig_garmin = Figure(figsize=(13, 6), dpi=100)
        self._garmin_twin_ax = None
        self.ax_garmin = self.fig_garmin.add_subplot(111)
        self.canvas_garmin = FigureCanvasTkAgg(self.fig_garmin, master=frame)
        self.canvas_garmin.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        c = self._get_chart_colors()
        self.xscrollbar_garmin = tk.Scrollbar(
            frame, orient=tk.HORIZONTAL, command=self._on_garmin_xscroll,
            troughcolor=c["fig_bg"], background=c["spine"],
            activebackground=c["text"])      
        self.xscrollbar_garmin.pack(fill=tk.X, padx=5, pady=(0, 2))
        self._garmin_x_full_range = (0, 1)
        self._garmin_scrolling = False
        self.toolbar_garmin = NavigationToolbar2Tk(self.canvas_garmin, frame)   
        self.toolbar_garmin.update()
        self.fig_garmin.canvas.mpl_connect('scroll_event', self._on_garmin_scroll)
        self._update_garmin_info()

    # ─── X-Scrollbar Sync ───────────────────────────────────────────────────

    def _update_garmin_xscrollbar(self):
        cur_lo, cur_hi = self.ax_garmin.get_xlim()
        full_lo, full_hi = self._garmin_x_full_range
        total = full_hi - full_lo
        if total <= 0:
            return
        frac_lo = max(0.0, (cur_lo - full_lo) / total)
        frac_hi = min(1.0, (cur_hi - full_lo) / total)
        self._garmin_scrolling = True
        self.xscrollbar_garmin.set(frac_lo, frac_hi)
        self._garmin_scrolling = False    

    def _zoom_in(self):
        x_lo, x_hi = self.ax_garmin.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) / 4
        self.ax_garmin.set_xlim(center - half, center + half)
        self.fig_garmin.canvas.draw_idle()
        self._update_garmin_xscrollbar()

    def _zoom_out(self):
        x_lo, x_hi = self.ax_garmin.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) * 0.75
        self.ax_garmin.set_xlim(center - half, center + half)
        self.fig_garmin.canvas.draw_idle()
        self._update_garmin_xscrollbar()

    def _zoom_reset(self):
        lo, hi = self._garmin_x_full_range
        if hi > lo:
            self.ax_garmin.set_xlim(lo, hi)
            self.fig_garmin.canvas.draw_idle()
        self._update_garmin_xscrollbar()

    def _update_garmin_info(self):
        history = load_history()
        first = history.get("first_download")
        last = history.get("last_update")
        n_days = len(self.garmin_data.get("daily_stats", []))
        if first and last:
            self.garmin_info_var.set(f"Data: {first} → {last}  |  {n_days} days")
        else:
            self.garmin_info_var.set("No data yet – click Full Download or Update")

    def _on_garmin_scroll(self, event):
        if event.inaxes != self.ax_garmin:
            return
        factor = 0.8 if event.button == 'up' else (1.25 if event.button == 'down' else None)
        if factor is None:
            return
        x_lo, x_hi = self.ax_garmin.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) / 2 * factor
        self.ax_garmin.set_xlim(center - half, center + half)
        self.fig_garmin.canvas.draw_idle()
        self._update_garmin_xscrollbar()

    def _on_garmin_xscroll(self, *args):
        if self._garmin_scrolling:
            return
        if len(args) >= 2 and args[0] == "moveto":
            frac = float(args[1])
            full_lo, full_hi = self._garmin_x_full_range
            total = full_hi - full_lo
            if total <= 0:
                return
            cur_lo, cur_hi = self.ax_garmin.get_xlim()
            visible = min(cur_hi - cur_lo, total)
            new_lo = full_lo + frac * total
            new_hi = new_lo + visible
            if new_hi > full_hi:
                new_hi = full_hi
                new_lo = new_hi - visible
            if new_lo < full_lo:
                new_lo = full_lo
                new_hi = new_lo + visible
            self.ax_garmin.set_xlim(new_lo, new_hi)
            self.fig_garmin.canvas.draw_idle()   

    # ─── Export ODS ─────────────────────────────────────────────────────────

    def _on_export_ods(self):
        if not ODS_AVAILABLE:
            messagebox.showerror("Error", "odfpy not installed.\nRun: pip install odfpy")
            return
        if not self.garmin_data:
            messagebox.showinfo("Info", "No data to export yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Export all data to ODS",
            defaultextension=".ods",
            filetypes=[("ODS files", "*.ods")],
            initialfile=f"garmin_export_{date.today().isoformat()}.ods",
            initialdir=str(DATA_DIR))
        if not path:
            return
        self.status_var.set("Exporting to ODS…")
        self.root.update()
        def work():
            try:
                export_all_to_ods(path, self.garmin_data, self.bp_data)
                self.root.after(0, lambda: self.status_var.set(f"✅ Exported to {Path(path).name}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Export Error", str(e)))
                self.root.after(0, lambda: self.status_var.set("Export failed."))
        threading.Thread(target=work, daemon=True).start()

    # ─── Download / Update ──────────────────────────────────────────────────

    def _get_credentials(self) -> tuple:
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        token_path = self.token_path_var.get().strip() or "~/.garminconnect"
        return email, password, token_path

    def _on_full_download(self):
        if not GARMIN_AVAILABLE:
            messagebox.showerror("Error",
                                 "garminconnect not installed.\n"
                                 "Run: pip install garminconnect==0.3.20 curl_cffi")
            return
        email, password, token_path = self._get_credentials()
        if not email or not password:
            messagebox.showwarning("Warning", "Enter email and password in Settings.")
            return
        self._show_progress("Garmin – Full Download")
        self._set_status("🔗 Connecting to Garmin Connect…")
        def work():
            try:
                client, err = garmin_login(email, password, token_path,
                                           status_cb=self._set_status,
                                           mfa_callback=self._prompt_mfa)
                if not client:
                    self._set_status("❌ Login failed.")
                    self._close_progress()
                    self.root.after(0, lambda: messagebox.showerror(
                        "Login Failed", err or "Could not authenticate."))
                    return
                self._set_status("📥 Starting full download…")
                new_data = fetch_full_history(client, status_cb=self._set_status)
                history = load_history()
                existing = history.get("data", {})
                if existing:
                    self._set_status("🔀 Merging with existing data…")
                    merged = merge_data(existing, new_data)
                else:
                    merged = new_data
                self._set_status("💾 Saving to disk…")
                history_out = {
                    "first_download": history.get("first_download") or date.today().isoformat(),
                    "last_update": date.today().isoformat(),
                    "data": merged,
                }
                save_history(history_out)
                self.garmin_data = merged
                n_days = len(merged.get("daily_stats", []))
                n_acts = len(merged.get("activities", []))
                n_sleep = len(merged.get("sleep", []))
                self._set_status(
                    f"✅ Done! {n_acts} activities, {n_days} days, {n_sleep} sleep nights.")
                self._close_progress()
                self.root.after(0, self._update_garmin_info)
                self.root.after(0, self._redraw_garmin_chart)
            except Exception as e:
                self._set_status(f"❌ Error: {e}")
                self._close_progress()
                self.root.after(0, lambda: messagebox.showerror("Download Error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _on_update(self):
        if not GARMIN_AVAILABLE:
            messagebox.showerror("Error",
                                 "garminconnect not installed.\n"
                                 "Run: pip install garminconnect==0.3.20 curl_cffi")
            return
        email, password, token_path = self._get_credentials()
        if not email or not password:
            messagebox.showwarning("Warning", "Enter email and password in Settings.")
            return
        history = load_history()
        if not history.get("data"):
            messagebox.showinfo("Info", "No saved data yet.\nUse 'Full Download' first.")
            return
        last_date = get_last_stored_date(history)
        if last_date is None:
            messagebox.showinfo("Info", "Could not determine last stored date.")
            return
        if last_date >= date.today():
            self.status_var.set("✅ Already up to date.")
            return
        self._show_progress("Garmin – Update")
        self._set_status(f"🔗 Connecting… (last data: {last_date.isoformat()})")
        def work():
            try:
                client, err = garmin_login(email, password, token_path,
                                           status_cb=self._set_status,
                                           mfa_callback=self._prompt_mfa)
                if not client:
                    self._set_status("❌ Login failed.")
                    self._close_progress()
                    self.root.after(0, lambda: messagebox.showerror(
                        "Login Failed", err or "Could not authenticate."))
                    return
                self._set_status(f"📥 Fetching new data from {last_date.isoformat()}…")
                new_data = fetch_incremental(client, last_date, status_cb=self._set_status)
                self._set_status("🔀 Merging new data…")
                merged = merge_data(history["data"], new_data)
                self._set_status("💾 Saving…")
                history_out = {
                    "first_download": history.get("first_download"),
                    "last_update": date.today().isoformat(),
                    "data": merged,
                }
                save_history(history_out)
                self.garmin_data = merged
                n_days = len(merged.get("daily_stats", []))
                self._set_status(
                    f"✅ Updated! {n_days} days total. "
                    f"Range: {history_out['first_download']} → {history_out['last_update']}")
                self._close_progress()
                self.root.after(0, self._update_garmin_info)
                self.root.after(0, self._redraw_garmin_chart)
            except Exception as e:
                self._set_status(f"❌ Error: {e}")
                self._close_progress()
                self.root.after(0, lambda: messagebox.showerror("Update Error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    # ─── Chart rendering ────────────────────────────────────────────────────

    def _safe_legend(self, ax, ax2=None):
        handles, labels = ax.get_legend_handles_labels()
        if ax2 is not None:
            h2, l2 = ax2.get_legend_handles_labels()
            handles += h2
            labels += l2
        if handles:
            c = self._get_chart_colors()
            ax.legend(handles, labels, loc="upper left", fontsize=8,
                      facecolor=c["legend_bg"], edgecolor=c["spine"],
                      labelcolor=c["text"])

    def _redraw_garmin_chart(self):
        if self._garmin_twin_ax is not None:
            self.fig_garmin.delaxes(self._garmin_twin_ax)
            self._garmin_twin_ax = None
        self.ax_garmin.clear()   
        choice = self.garmin_chart_var.get()
        stats_text = ""
        twin_ax = None

        if choice == "steps" and self.garmin_data.get("daily_stats"):
            df = pd.DataFrame(self.garmin_data["daily_stats"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_garmin.plot(df["date"], df["steps"],
                                color=COLORS["steps"], linewidth=1.5, label="Steps")
            self.ax_garmin.set_ylabel("Steps", color=COLORS["steps"])
            twin_ax = self._garmin_twin_ax = self.ax_garmin.twinx()   
            twin_ax.plot(df["date"], df["distance_km"],
                         color=COLORS["distance"], linewidth=1.5, label="Distance (km)")
            twin_ax.set_ylabel("km", color=COLORS["distance"])
            self.ax_garmin.set_title("Daily Steps & Distance")
            stats_text = (f"Steps: {compute_stats(df['steps'])}   |   "
                          f"Distance: {compute_stats(df['distance_km'])} km")

        elif choice == "calories" and self.garmin_data.get("daily_stats"):
            df = pd.DataFrame(self.garmin_data["daily_stats"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_garmin.fill_between(df["date"], df["calories"],
                                        alpha=0.3, color=COLORS["calories"])
            self.ax_garmin.plot(df["date"], df["calories"],
                                color=COLORS["calories"], linewidth=1.2, label="Total kcal")
            self.ax_garmin.plot(df["date"], df["active_calories"],
                                color=COLORS["active_cal"], linewidth=1.2,
                                linestyle="--", label="Active kcal")
            self.ax_garmin.set_ylabel("kcal")
            self.ax_garmin.set_title("Calories")
            stats_text = (f"Total: {compute_stats(df['calories'])} kcal   |   "
                          f"Active: {compute_stats(df['active_calories'])} kcal")

        elif choice == "heart_rate" and self.garmin_data.get("heart_rate"):
            df = pd.DataFrame(self.garmin_data["heart_rate"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_garmin.plot(df["date"], df["resting_hr"],
                                color=COLORS["resting_hr"], linewidth=1.5, label="Resting")
            self.ax_garmin.plot(df["date"], df["max_hr"],
                                color=COLORS["max_hr"], linewidth=1.0, label="Max")
            self.ax_garmin.plot(df["date"], df["min_hr"],
                                color=COLORS["min_hr"], linewidth=1.0, label="Min")
            self.ax_garmin.set_ylabel("BPM")
            self.ax_garmin.set_title("Heart Rate")
            stats_text = (f"Resting: {compute_stats(df['resting_hr'])} bpm   |   "
                          f"Max: {compute_stats(df['max_hr'])} bpm")

        elif choice == "stress" and self.garmin_data.get("stress"):
            df = pd.DataFrame(self.garmin_data["stress"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_garmin.plot(df["date"], df["avg_stress"],
                                color=COLORS["stress"], linewidth=1.5, label="Avg Stress")
            self.ax_garmin.plot(df["date"], df["max_stress"],
                                color=COLORS["stress_high"], linewidth=0.8,
                                linestyle="--", label="Max")
            if "min_stress" in df.columns and df["min_stress"].notna().any():
                self.ax_garmin.plot(df["date"], df["min_stress"],
                                    color=COLORS["stress_low"], linewidth=0.8,
                                    linestyle="--", label="Min")
            twin_ax = self.ax_garmin.twinx()
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
            self.ax_garmin.set_ylabel("Stress Level")
            self.ax_garmin.set_title("Stress")
            stats_text = (f"Avg: {compute_stats(df['avg_stress'])}   |   "
                          f"Max: {compute_stats(df['max_stress'])}")

        elif choice == "sleep" and self.garmin_data.get("sleep"):
            df = pd.DataFrame(self.garmin_data["sleep"])
            df["date"] = pd.to_datetime(df["date"])
            bottom = np.zeros(len(df))
            self.ax_garmin.bar(df["date"], df["deep_sleep_min"].fillna(0),
                               color=COLORS["sleep_deep"], label="Deep",
                               bottom=bottom, width=0.8)
            bottom += df["deep_sleep_min"].fillna(0).values
            self.ax_garmin.bar(df["date"], df["light_sleep_min"].fillna(0),
                               color=COLORS["sleep_light"], label="Light",
                               bottom=bottom, width=0.8)
            bottom += df["light_sleep_min"].fillna(0).values
            self.ax_garmin.bar(df["date"], df["rem_sleep_min"].fillna(0),
                               color=COLORS["sleep_rem"], label="REM",
                               bottom=bottom, width=0.8)
            self.ax_garmin.set_ylabel("Minutes")
            self.ax_garmin.set_title("Sleep Summary")
            stats_text = (f"Total: {compute_stats(df['sleep_time_min'])} min   |   "
                          f"Deep: {compute_stats(df['deep_sleep_min'])}   |   "
                          f"REM: {compute_stats(df['rem_sleep_min'])}   |   "
                          f"Score: {compute_stats(df['sleep_score'])}")

        elif choice == "sleep_tracing" and self.garmin_data.get("sleep"):
            sleep_entries = [e for e in self.garmin_data.get("sleep", []) if e.get("sleep_levels")]
            if not sleep_entries:
                self.ax_garmin.text(0.5, 0.5,
                                    "No sleep tracing data available.\n"
                                    "Stage timelines are only available for\n"
                                    "recent nights (last ~7 days).",
                                    ha="center", va="center", fontsize=11)
            else:
                sleep_entries = sorted(sleep_entries, key=lambda x: x["date"])[-14:]
                n_nights = len(sleep_entries)
                stage_map = {
                    2: (10, "#2980B9", "Deep"),
                    1: (12, "#1ABC9C", "Light"),
                    3: (14, "#9B59B6", "REM"),
                    0: (16, "#F1C40F", "Awake"),
                }
                sleep_start = self.settings.get("sleep_start", "22:00")
                sleep_end   = self.settings.get("sleep_end", "07:00")
                sh, sm = map(int, sleep_start.split(":"))
                eh, em = map(int, sleep_end.split(":"))
                if eh < sh or (eh == sh and em <= sm):
                    window_hours = (24 - sh - sm / 60) + (eh + em / 60)
                else:
                    window_hours = (eh - sh) + (em - sm) / 60
                import random
                random.seed(42)
                bright_palette = ["#FF6B6B", "#4ECDC4", "#FFD93D", "#6C5CE7",
                                  "#A8E6CF", "#FF8A5C", "#45B7D1", "#F9CA24",
                                  "#6AB04C", "#EB4D4B", "#7ED6DF", "#E84393",
                                  "#2ECC71", "#F39C12"]
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
                            end_t   = pd.Timestamp(end_str).replace(tzinfo=None)
                        except Exception:
                            continue
                        if end_t <= start_t:
                            continue
                        x0 = to_unit_x(start_t, idx)
                        x1 = to_unit_x(end_t, idx)
                        if x1 <= x0:
                            continue
                        y_val, color, _ = stage_map.get(level, (12, "#999999", "?"))
                        self.ax_garmin.fill_between(
                            [x0, x1], 0, y_val,
                            color=color, alpha=0.85, edgecolor="none")

                for i in range(1, n_nights):
                    self.ax_garmin.axvline(i, color=night_colors[i],
                                           linewidth=2.0, alpha=0.8)

                tick_positions = []
                tick_labels = []
                for idx in range(n_nights):
                    night_date = pd.Timestamp(sleep_entries[idx]["date"])
                    win_start = night_date.replace(hour=sh, minute=sm, second=0)
                    for offset_h in range(0, int(window_hours) + 1, 2):
                        tick_time = win_start + pd.Timedelta(hours=offset_h)
                        pos = idx + offset_h / window_hours
                        if pos <= n_nights:
                            tick_positions.append(pos)
                            tick_labels.append(tick_time.strftime("%H:%M"))
                self.ax_garmin.set_xticks(tick_positions)
                self.ax_garmin.set_xticklabels(tick_labels, fontsize=7, rotation=45)

                def _fmt_hm(minutes):
                    m_total = int(round(minutes))
                    h = m_total // 60
                    m = m_total % 60
                    return f"{h:02d}:{m:02d}"

                for idx in range(n_nights):
                    entry = sleep_entries[idx]
                    d = pd.Timestamp(entry["date"])
                    date_label = d.strftime("%d.%m.%y")
                    self.ax_garmin.text(
                        idx + 0.5, -3.0, date_label,
                        ha="center", va="top", fontsize=8,
                        color=night_colors[idx], fontweight="bold")
                    state_minutes = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
                    for lv in entry["sleep_levels"]:
                        s, e = lv.get("start", ""), lv.get("end", "")
                        if not s or not e:
                            continue
                        try:
                            st = pd.Timestamp(s).replace(tzinfo=None)
                            en = pd.Timestamp(e).replace(tzinfo=None)
                        except Exception:
                            continue
                        dur_min = (en - st).total_seconds() / 60
                        lvl = lv.get("level", 0)
                        if lvl in state_minutes:
                            state_minutes[lvl] += dur_min
                    total_min = sum(state_minutes.values())
                    self.ax_garmin.text(
                        idx + 0.5, -4.0, f"Σ {_fmt_hm(total_min)}",
                        ha="center", va="top", fontsize=7,
                        color=night_colors[idx])
                    states = [
                        ("D", state_minutes[2], stage_map[2][1]),
                        ("L", state_minutes[1], stage_map[1][1]),
                        ("R", state_minutes[3], stage_map[3][1]),
                        ("A", state_minutes[0], stage_map[0][1]),
                    ]
                    offsets = [-0.32, -0.11, 0.11, 0.32]
                    for i, (letter, minutes, color) in enumerate(states):
                        self.ax_garmin.text(
                            idx + 0.5 + offsets[i], -5.0,
                            f"{letter} {_fmt_hm(minutes)}",
                            ha="center", va="top", fontsize=7,
                            color=color, fontweight="bold")

                self.ax_garmin.set_ylim(0, 18)
                self.ax_garmin.set_yticks([10, 12, 14, 16])
                self.ax_garmin.set_yticklabels(["Deep", "Light", "REM", "Awake"], fontsize=9)
                self.ax_garmin.set_xlim(-0.02, n_nights + 0.02)
                self.ax_garmin.set_xlabel("")
                self.ax_garmin.set_title("Sleep Tracing")
                all_deep  = [e.get("deep_sleep_min", 0) for e in sleep_entries]
                all_light = [e.get("light_sleep_min", 0) for e in sleep_entries]
                all_rem   = [e.get("rem_sleep_min", 0) for e in sleep_entries]
                all_awake = [e.get("awake_min", 0) for e in sleep_entries]
                stats_text = (f"Total: {(sum(all_deep)+sum(all_light)+sum(all_rem)+sum(all_awake))/60:.1f} h  |  "
                              f"Deep: {sum(all_deep)/60:.1f} h  |  "
                              f"Light: {sum(all_light)/60:.1f} h  |  "
                              f"REM: {sum(all_rem)/60:.1f} h  |  "
                              f"Awake: {sum(all_awake)/60:.1f} h")

        elif choice == "respiration" and self.garmin_data.get("respiration"):
            df = pd.DataFrame(self.garmin_data["respiration"])
            df["date"] = pd.to_datetime(df["date"])
            self.ax_garmin.plot(df["date"], df["avg_respiration"],
                                color=COLORS["respiration"], linewidth=1.5, label="Avg")
            if df["max_respiration"].notna().any():
                self.ax_garmin.plot(df["date"], df["max_respiration"],
                                    color=COLORS["respiration"], linewidth=0.8,
                                    linestyle="--", alpha=0.6, label="Max")
            if df["min_respiration"].notna().any():
                self.ax_garmin.plot(df["date"], df["min_respiration"],
                                    color=COLORS["respiration"], linewidth=0.8,
                                    linestyle=":", alpha=0.6, label="Min")
            self.ax_garmin.set_ylabel("Breaths/min")
            self.ax_garmin.set_title("Respiration Rate")
            stats_text = f"Respiration: {compute_stats(df['avg_respiration'])} breaths/min"

        elif choice == "body_battery" and self.garmin_data.get("body_battery"):
            bb = self.garmin_data["body_battery"]
            if bb:
                dates, values = [], []
                for entry in bb:
                    d = entry.get("date") or entry.get("calendarDate")
                    v = (entry.get("charged") or entry.get("bodyBattery")
                         or entry.get("value"))
                    if d and v is not None:
                        try:
                            dates.append(pd.Timestamp(d))
                            values.append(v)
                        except Exception:
                            pass
                if dates:
                    self.ax_garmin.fill_between(dates, values, alpha=0.3,
                                                color=COLORS["body_battery"])
                    self.ax_garmin.plot(dates, values,
                                        color=COLORS["body_battery"],
                                        linewidth=1.5, label="Body Battery")
                    self.ax_garmin.set_ylabel("Points")
                    self.ax_garmin.set_title("Body Battery")
                    stats_text = f"Body Battery: {compute_stats(pd.Series(values))}"
                else:
                    self.ax_garmin.text(0.5, 0.5, "No valid body battery entries",
                                        ha="center", va="center", fontsize=11)

        elif choice == "activities" and self.garmin_data.get("activities"):
            acts = self.garmin_data["activities"]
            types = [a.get("activityType", {}).get("typeKey", "unknown") for a in acts]
            counts = pd.Series(types).value_counts()
            self.ax_garmin.barh(counts.index, counts.values, color=COLORS["steps"])
            self.ax_garmin.set_xlabel("Count")
            self.ax_garmin.set_title("Activities by Type")
            stats_text = f"Total activities: {len(acts)}"

        elif choice == "blood_pressure":
            if self.bp_data is None or self.bp_data.empty:
                self.ax_garmin.text(0.5, 0.5,
                                    "No blood pressure data.\n"
                                    "Set the ODS file path in Settings\n"
                                    "and click 'Load BP Data'.",
                                    ha="center", va="center", fontsize=11)
            else:
                df = self.bp_data.copy()
                df = df.sort_values("datetime").reset_index(drop=True)
                n = len(df)
                x = np.arange(n)
                width = 0.3

                self.ax_garmin.bar(x - width, df["sys"], width=width,
                                   color=COLORS["bp_sys"], label="Systolic")
                self.ax_garmin.bar(x, df["dia"], width=width,
                                   color=COLORS["bp_dia"], label="Diastolic")
                if "pulse" in df.columns and df["pulse"].notna().any():
                    self.ax_garmin.bar(x + width, df["pulse"], width=width,
                                       color=COLORS["bp_pulse"], label="Pulse")

                # X-axis: time for every measurement, date only when it changes
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
                self.ax_garmin.set_xticks(tick_positions)
                self.ax_garmin.set_xticklabels(tick_labels, rotation=90, fontsize=6)   

                parts = []
                if "sys" in df.columns:
                    parts.append(f"Sys: {compute_stats(df['sys'])} mmHg")
                if "dia" in df.columns:
                    parts.append(f"Dia: {compute_stats(df['dia'])} mmHg")
                if "pulse" in df.columns:
                    parts.append(f"Pulse: {compute_stats(df['pulse'])} bpm")
                stats_text = "   |   ".join(parts)      

        else:
            self.ax_garmin.text(0.5, 0.5, "No data – click Full Download or Update",
                                ha="center", va="center", fontsize=12)

        self._safe_legend(self.ax_garmin, twin_ax)
        self.ax_garmin.tick_params(axis="x", rotation=30)
        self._style_figure(self.fig_garmin, self.ax_garmin, twin_ax)
        self.fig_garmin.tight_layout()
        self.canvas_garmin.draw()
        self.stats_var.set(stats_text)
        x_lo, x_hi = self.ax_garmin.get_xlim()
        if x_hi > x_lo:
            self._garmin_x_full_range = (x_lo, x_hi)
        self._garmin_scrolling = True
        self.xscrollbar_garmin.set(0.0, 1.0)
        self._garmin_scrolling = False        
        
    # ─── FIT Files Tab ──────────────────────────────────────────────────────

    def _build_fit_tab(self):
        frame = ttk.Frame(self.tab_fit)
        frame.pack(fill=tk.BOTH, expand=True)
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="Open .FIT…", bootstyle="primary",
               command=self._on_open_fit).pack(side=tk.LEFT, padx=5)
        Button(top, text="From Export Scan", bootstyle="info",
               command=self._on_load_from_scan).pack(side=tk.LEFT, padx=5)
        self.fit_info_var = tk.StringVar(value="No file loaded")
        ttk.Label(top, textvariable=self.fit_info_var).pack(side=tk.LEFT, padx=10)
        self.fit_chart_var = tk.StringVar(value="hr_speed")
        charts = [
            ("hr_speed", "HR + Speed"),
            ("elevation", "Elevation"),
            ("gps_track", "GPS"),
            ("cadence_power", "Cadence + Power"),
            ("all", "All (norm.)"),
        ]
        for key, label in charts:
            ttk.Radiobutton(top, text=label, value=key,
                            variable=self.fit_chart_var,
                            command=self._redraw_fit_chart).pack(side=tk.LEFT, padx=8)
        self.fit_stats_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.fit_stats_var, anchor=tk.W).pack(
            fill=tk.X, padx=10, pady=2)
        self.fig_fit = Figure(figsize=(13, 6), dpi=100)
        self._fit_twin_ax = None
        self.ax_fit = self.fig_fit.add_subplot(111)
        self.canvas_fit = FigureCanvasTkAgg(self.fig_fit, master=frame)
        self.canvas_fit.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        c = self._get_chart_colors()
        self.xscrollbar_fit = tk.Scrollbar(
            frame, orient=tk.HORIZONTAL, command=self._on_fit_xscroll,
            troughcolor=c["fig_bg"], background=c["spine"],
            activebackground=c["text"])     
        self.xscrollbar_fit.pack(fill=tk.X, padx=5, pady=(0, 2))
        self._fit_x_full_range = (0, 1)
        self._fit_scrolling = False
        NavigationToolbar2Tk(self.canvas_fit, frame).update()
        self.fig_fit.canvas.mpl_connect('scroll_event', self._on_fit_scroll)
        self.fit_listbox = tk.Listbox(frame, height=4, selectmode=tk.SINGLE)
        self.fit_listbox.pack(fill=tk.X, padx=5, pady=2)
        self.fit_listbox.bind("<<ListboxSelect>>", self._on_fit_list_select)
        
    def _build_bp_tab(self):
        frame = ttk.Frame(self.tab_bp)
        frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="➕ Add Row", bootstyle="success",
               command=self._on_bp_add_row).pack(side=tk.LEFT, padx=5)
        Button(top, text="🗑 Delete Row", bootstyle="danger",
               command=self._on_bp_delete_row).pack(side=tk.LEFT, padx=5)
        Button(top, text="💾 Save to File", bootstyle="primary",
               command=self._on_bp_save_all).pack(side=tk.LEFT, padx=5)
        Button(top, text="🔄 Reload", bootstyle="info",
               command=self._on_bp_reload).pack(side=tk.LEFT, padx=5)
        self.bp_tab_info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.bp_tab_info_var).pack(side=tk.LEFT, padx=15)

        # Table
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("datum", "time", "sys", "dia", "pulse")
        self.bp_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=20)
        self.bp_tree.heading("datum", text="datum")
        self.bp_tree.heading("time", text="time")
        self.bp_tree.heading("sys", text="sys")
        self.bp_tree.heading("dia", text="dia")
        self.bp_tree.heading("pulse", text="pulse")
        self.bp_tree.column("datum", width=80, anchor=tk.CENTER)
        self.bp_tree.column("time", width=80, anchor=tk.CENTER)
        self.bp_tree.column("sys", width=80, anchor=tk.CENTER)
        self.bp_tree.column("dia", width=80, anchor=tk.CENTER)
        self.bp_tree.column("pulse", width=80, anchor=tk.CENTER)

        ysb = tk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.bp_tree.yview)
        xsb = tk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.bp_tree.xview)
        self.bp_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.bp_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)

        # Double-click to edit a cell
        self.bp_tree.bind("<Double-1>", self._on_bp_cell_edit)
        ttk.Label(frame, text="Double-click a cell to edit. Use Add Row / Delete Row to manage entries.",
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
        col_name = ("datum", "time", "sys", "dia", "pulse")[col_idx]

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
        self.bp_tree.insert("", tk.END, values=("26.11.26", "12:00", "", "", ""))

    def _on_bp_delete_row(self):
        sel = self.bp_tree.selection()
        if sel:
            self.bp_tree.delete(*sel)

    def _on_bp_save_all(self):
        rows = self.bp_tree.get_children()
        if not rows:
            messagebox.showwarning("Warning", "No data to save.")
            return
        lines = ["datum\ttime\tsys\tdia\tpulse"]
        for row in rows:
            vals = self.bp_tree.item(row, "values")
            lines.append("\t".join(str(v) for v in vals))
        text = "\n".join(lines)
        def work():
            df = save_bp_data(text)
            if df is None:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", "Could not save data."))
                return
            self.bp_data = load_bp_data()
            n = len(self.bp_data) if self.bp_data is not None else 0
            self.root.after(0, lambda: self.bp_tab_info_var.set(f"Saved {n} readings."))
            self.root.after(0, lambda: self.status_var.set(f"BP saved: {n} readings."))
            if self.garmin_chart_var.get() == "blood_pressure":
                self.root.after(0, self._redraw_garmin_chart)
        threading.Thread(target=work, daemon=True).start()   
                  
    def _on_bp_save(self):
        text = self.bp_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Warning", "Nothing to save.")
            return
        def work():
            df = save_bp_data(text)
            if df is None:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", "Could not parse or save data.\n"
                             "Make sure odfpy is installed."))
                return
            self.bp_data = load_bp_data()
            n = len(self.bp_data) if self.bp_data is not None else 0
            info = f"Saved {n} readings to {BP_FILE.name}"
            self.root.after(0, lambda: self.bp_tab_info_var.set(info))
            self.root.after(0, lambda: self.status_var.set(f"BP saved: {n} readings."))
            self.root.after(0, self._populate_bp_table)
            self.bp_text.delete("1.0", tk.END)
            if self.garmin_chart_var.get() == "blood_pressure":
                self.root.after(0, self._redraw_garmin_chart)
        threading.Thread(target=work, daemon=True).start()   

    def _on_bp_reload(self):
        def work():
            df = load_bp_data()
            if df is None:
                self.bp_data = None
                self.root.after(0, self._populate_bp_table)
                self.root.after(0, lambda: self.bp_tab_info_var.set("No data file found."))
                return
            self.bp_data = df
            n = len(df)
            self.root.after(0, self._populate_bp_table)
            self.root.after(0, lambda: self.bp_tab_info_var.set(
                f"Loaded {n} readings from {BP_FILE.name}"))
            if self.garmin_chart_var.get() == "blood_pressure":
                self.root.after(0, self._redraw_garmin_chart)
        threading.Thread(target=work, daemon=True).start()   

    def _on_bp_clear(self):
        for item in self.bp_tree.get_children():
            self.bp_tree.delete(item)
        self.bp_text.delete("1.0", tk.END)
        self.bp_tab_info_var.set("Cleared.")
        self.bp_data = None
        if self.garmin_chart_var.get() == "blood_pressure":
            self._redraw_garmin_chart() 
	    
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
            p = int(row["pulse"]) if pd.notna(row["pulse"]) else ""
            self.bp_tree.insert("", tk.END, values=(d, t, s, dia, p))   

    # ─── FIT X-Scrollbar Sync ───────────────────────────────────────────────

    def _update_fit_xscrollbar(self):
        cur_lo, cur_hi = self.ax_fit.get_xlim()
        full_lo, full_hi = self._fit_x_full_range
        total = full_hi - full_lo
        if total <= 0:
            return
        frac_lo = max(0.0, (cur_lo - full_lo) / total)
        frac_hi = min(1.0, (cur_hi - full_lo) / total)
        self._fit_scrolling = True
        self.xscrollbar_fit.set(frac_lo, frac_hi)
        self._fit_scrolling = False   

    def _on_fit_scroll(self, event):
        if event.inaxes != self.ax_fit:
            return
        factor = 0.8 if event.button == 'up' else (1.25 if event.button == 'down' else None)
        if factor is None:
            return
        x_lo, x_hi = self.ax_fit.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) / 2 * factor
        self.ax_fit.set_xlim(center - half, center + half)
        self.fig_fit.canvas.draw_idle()
        self._update_fit_xscrollbar()

    def _on_fit_xscroll(self, *args):
        if self._fit_scrolling:
            return
        if len(args) >= 2 and args[0] == "moveto":
            frac = float(args[1])
            full_lo, full_hi = self._fit_x_full_range
            total = full_hi - full_lo
            if total <= 0:
                return
            cur_lo, cur_hi = self.ax_fit.get_xlim()
            visible = min(cur_hi - cur_lo, total)
            new_lo = full_lo + frac * total
            new_hi = new_lo + visible
            if new_hi > full_hi:
                new_hi = full_hi
                new_lo = new_hi - visible
            if new_lo < full_lo:
                new_lo = full_lo
                new_hi = new_lo + visible
            self.ax_fit.set_xlim(new_lo, new_hi)
            self.fig_fit.canvas.draw_idle()   

    def _on_open_fit(self):
        if not FIT_AVAILABLE:
            messagebox.showerror("Error", "fitparse not installed.")
            return
        path = filedialog.askopenfilename(
            title="Select .FIT file",
            filetypes=[("FIT files", "*.fit"), ("All files", "*.*")])
        if path:
            self._load_fit(path)

    def _on_load_from_scan(self):
        if not self.export_scan.get("fit_files"):
            messagebox.showinfo("Info", "No FIT files. Scan export folder in Settings.")
            return
        if not self.fit_listbox.size():
            for f in self.export_scan["fit_files"]:
                label = f"{f[0].name} → {f[1]}" if isinstance(f, tuple) else str(f)
                self.fit_listbox.insert(tk.END, label)
        if self.fit_listbox.size() > 0:
            self.fit_listbox.selection_set(0)
            self._on_fit_list_select(None)

    def _on_fit_list_select(self, event):
        sel = self.fit_listbox.curselection()
        if not sel:
            return
        self._load_fit(self.export_scan["fit_files"][sel[0]])

    def _load_fit(self, item):
        if not FIT_AVAILABLE:
            messagebox.showerror("Error", "fitparse not installed.")
            return
        self.status_var.set("Reading FIT…")
        self.root.update()
        def work():
            try:
                if isinstance(item, tuple):
                    self.fit_data = read_fit_from_zip(item[0], item[1])
                else:
                    self.fit_data = read_fit_file(str(item))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", f"Failed to read FIT file:\n{e}"))
                self.root.after(0, lambda: self.status_var.set("FIT load failed."))
                return
            sess = self.fit_data["session"]
            n_records = len(self.fit_data["records"])
            n_laps = len(self.fit_data["laps"])
            dist_km = (sess.get("total_distance", 0) or 0) / 100
            tmin = int((sess.get("total_time", 0) or 0) / 60)
            avg_hr = sess.get("avg_heart_rate", "–")
            info = (f"{self.fit_data['file']}  |  {dist_km:.1f} km  |  "
                    f"{tmin} min  |  Avg HR: {avg_hr} bpm  |  "
                    f"{n_records} records  |  {n_laps} laps")
            self.root.after(0, lambda: self.fit_info_var.set(info))
            self.root.after(0, lambda: self.status_var.set(
                f"Loaded: {n_records} records, {n_laps} laps"))
            self.root.after(0, self._redraw_fit_chart)
        threading.Thread(target=work, daemon=True).start()

    def _redraw_fit_chart(self):
        if self._fit_twin_ax is not None:
            self.fig_fit.delaxes(self._fit_twin_ax)
            self._fit_twin_ax = None
        self.ax_fit.clear()   
        df = self.fit_data.get("records_df")
        twin_ax = None

        if df is None or df.empty:
            laps = self.fit_data.get("laps", [])
            if not laps:
                self.ax_fit.text(0.5, 0.5, "No data in this file",
                                 ha="center", va="center", fontsize=11)
                self.fig_fit.tight_layout()
                self.canvas_fit.draw()
                self.fit_stats_var.set("Empty file.")
                return
            lap_records = []
            cumulative_dist = 0
            for lap in laps:
                lap_dist = (lap.get("total_distance", 0) or 0) / 100
                cumulative_dist += lap_dist
                lap_records.append({
                    "distance": cumulative_dist,
                    "heart_rate": lap.get("avg_heart_rate"),
                    "speed": lap.get("avg_speed"),
                })
            df = pd.DataFrame(lap_records)
            df["distance"] = df["distance"].fillna(0)

        has_distance = ("distance" in df.columns and
                        df["distance"].notna().any() and
                        (df["distance"] > 0).any())
        if has_distance:
            x = df["distance"]
            xlabel = "Distance (km)"
        else:
            x = np.arange(len(df))
            xlabel = "Lap #" if (self.fit_data.get("records_df") is None or
                                  self.fit_data.get("records_df").empty) else "Record #"

        choice = self.fit_chart_var.get()
        stats_text = ""

        if choice == "hr_speed":
            plotted = False
            if "heart_rate" in df.columns and df["heart_rate"].notna().any():
                self.ax_fit.plot(x, df["heart_rate"],
                                 color=COLORS["resting_hr"], linewidth=1.2, label="HR (bpm)")
                self.ax_fit.set_ylabel("BPM")
                plotted = True
            if "speed" in df.columns and df["speed"].notna().any():
                twin_ax = self._fit_twin_ax = self.ax_fit.twinx()
                twin_ax.plot(x, df["speed"] * 3.6,
                             color=COLORS["speed"], linewidth=1.2, label="Speed (km/h)")
                twin_ax.set_ylabel("km/h")
                plotted = True
            if not plotted:
                self.ax_fit.text(0.5, 0.5, "No HR or Speed data",
                                 ha="center", va="center", fontsize=11)
            else:
                parts = []
                if "heart_rate" in df.columns and df["heart_rate"].notna().any():
                    parts.append(f"HR: {compute_stats(df['heart_rate'])} bpm")
                if "speed" in df.columns and df["speed"].notna().any():
                    parts.append(f"Speed: {compute_stats(df['speed']*3.6)} km/h")
                stats_text = "   |   ".join(parts)    

        elif choice == "elevation":
            if "altitude" in df.columns and df["altitude"].notna().any():
                self.ax_fit.fill_between(x, df["altitude"],
                                         alpha=0.3, color=COLORS["elevation"])
                self.ax_fit.plot(x, df["altitude"],
                                 color=COLORS["elevation"], linewidth=0.8, label="Altitude")
                self.ax_fit.set_ylabel("m")
                stats_text = f"Elevation: {compute_stats(df['altitude'])} m"
            else:
                self.ax_fit.text(0.5, 0.5, "No elevation data",
                                 ha="center", va="center", fontsize=11)

        elif choice == "gps_track":
            if ("position_lat" in df.columns and "position_long" in df.columns
                    and df["position_lat"].notna().any()):
                self.ax_fit.plot(df["position_long"], df["position_lat"],
                                 linewidth=0.5, color="#9B59B6", label="GPS")
                self.ax_fit.set_xlabel("Longitude")
                self.ax_fit.set_ylabel("Latitude")
                self.ax_fit.set_aspect("equal")
                stats_text = f"GPS points: {len(df)}"
            else:
                self.ax_fit.text(0.5, 0.5, "No GPS data",
                                 ha="center", va="center", fontsize=11)

        elif choice == "cadence_power":
            plotted = False
            if "cadence" in df.columns and df["cadence"].notna().any():
                self.ax_fit.plot(x, df["cadence"],
                                 color=COLORS["cadence"], linewidth=1.2, label="Cadence (rpm)")
                self.ax_fit.set_ylabel("RPM")
                plotted = True
            if "power" in df.columns and df["power"].notna().any():
                twin_ax = self._fit_twin_ax = self.ax_fit.twinx()   
                twin_ax.plot(x, df["power"],
                             color=COLORS["power"], linewidth=1.2, label="Power (W)")
                twin_ax.set_ylabel("W")
                plotted = True
            if not plotted:
                self.ax_fit.text(0.5, 0.5, "No cadence or power data",
                                 ha="center", va="center", fontsize=11)
            else:
                parts = []
                if "cadence" in df.columns and df["cadence"].notna().any():
                    parts.append(f"Cadence: {compute_stats(df['cadence'])} rpm")
                if "power" in df.columns and df["power"].notna().any():
                    parts.append(f"Power: {compute_stats(df['power'])} W")
                stats_text = "   |   ".join(parts)

        elif choice == "all":
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            numeric_cols = [c for c in numeric_cols
                           if c not in ("position_lat", "position_long", "timestamp")]
            if not numeric_cols:
                self.ax_fit.text(0.5, 0.5, "No numeric data",
                                 ha="center", va="center", fontsize=11)
            else:
                palette = plt.cm.tab10(np.linspace(0, 1, min(len(numeric_cols), 10)))
                for i, col in enumerate(numeric_cols[:8]):
                    vals = df[col].dropna()
                    if len(vals) > 0 and vals.max() > vals.min():
                        norm = (df[col] - vals.min()) / (vals.max() - vals.min())
                        self.ax_fit.plot(x, norm, label=col,
                                         color=palette[i % 10], linewidth=0.8)
                self.ax_fit.set_ylabel("Normalized (0–1)")
                stats_text = f"{min(len(numeric_cols), 8)} of {len(numeric_cols)} metrics"

        self.ax_fit.set_xlabel(xlabel)
        self._safe_legend(self.ax_fit, twin_ax)
        self.ax_fit.tick_params(axis="x", rotation=30)
        self._style_figure(self.fig_fit, self.ax_fit, twin_ax)
        self.fig_fit.tight_layout()
        self.canvas_fit.draw()
        self.fit_stats_var.set(stats_text)
        x_lo, x_hi = self.ax_fit.get_xlim()
        if x_hi > x_lo:
            self._fit_x_full_range = (x_lo, x_hi)
        self._fit_scrolling = True
        self.xscrollbar_fit.set(0.0, 1.0)
        self._fit_scrolling = False   


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    app = tb.Window(themename="darkly", title="Garmin Data Viewer")
    app.geometry("1400x900")
    viewer = GarminViewerApp(app)
    app.mainloop()


if __name__ == "__main__":
    main()   
