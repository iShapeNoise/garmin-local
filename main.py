#!/usr/bin/env python3
"""
Garmin Data Viewer – GUI app to fetch & visualize Garmin Connect data,
local .FIT files, and blood pressure data.

Dependencies:
    pip install "garminconnect==0.3.20" curl_cffi fitparse matplotlib pandas numpy odfpy ttkbootstrap Pillow duckdb
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap import Button
from datetime import date, timedelta
from pathlib import Path
import json
import duckdb
import threading
import time
import re

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

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

try:
    from fitparse import FitFile
    FIT_AVAILABLE = True
except ImportError:
    FIT_AVAILABLE = False

try:
    import odf
    ODS_AVAILABLE = True
except ImportError:
    ODS_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─── Paths ────────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
DB_PATH = DATA_DIR / "garmin_data.db"


# ─── Settings ─────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {"email": "", "password": "", "local_fit_path": "",
            "token_path": "~/.garminconnect",
            "sleep_start": "22:00", "sleep_end": "07:00",
            "theme": "darkly"}


def save_settings(s: dict):
    DATA_DIR.mkdir(exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))


# ─── Database ─────────────────────────────────────────────────────────────────

def _get_db():
    DATA_DIR.mkdir(exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def init_db():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            steps INTEGER DEFAULT 0,
            distance_km REAL DEFAULT 0,
            calories REAL DEFAULT 0,
            active_calories REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS heart_rate (
            date TEXT PRIMARY KEY,
            resting_hr REAL,
            max_hr REAL,
            min_hr REAL
        );
        CREATE TABLE IF NOT EXISTS stress (
            date TEXT PRIMARY KEY,
            avg_stress REAL,
            max_stress REAL,
            min_stress REAL,
            low_minutes REAL DEFAULT 0,
            medium_minutes REAL DEFAULT 0,
            high_minutes REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sleep (
            date TEXT PRIMARY KEY,
            sleep_score REAL,
            sleep_time_min REAL,
            deep_sleep_min REAL,
            light_sleep_min REAL,
            rem_sleep_min REAL,
            awake_min REAL
        );
        CREATE TABLE IF NOT EXISTS sleep_levels (
            date TEXT,
            start TEXT,
            end_time TEXT,
            level INTEGER
        );
        CREATE TABLE IF NOT EXISTS respiration (
            date TEXT PRIMARY KEY,
            avg_respiration REAL,
            max_respiration REAL,
            min_respiration REAL
        );
        CREATE TABLE IF NOT EXISTS activities (
            activityId TEXT PRIMARY KEY,
            activityName TEXT,
            activityType TEXT,
            startTimeLocal TEXT,
            distance REAL,
            duration REAL,
            averageHeartRate REAL,
            maxHeartRate REAL,
            calories REAL,
            source TEXT
        );
        CREATE TABLE IF NOT EXISTS body_battery (
            date TEXT PRIMARY KEY,
            value REAL
        );
        CREATE TABLE IF NOT EXISTS blood_pressure (
            datum TEXT,
            time TEXT,
            sys REAL,
            dia REAL,
            pulse REAL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.close()


def db_upsert_daily_stats(rows: list):
    if not rows:
        return
    conn = _get_db()
    for r in rows:
        conn.execute("""
            INSERT INTO daily_stats (date, steps, distance_km, calories, active_calories)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                steps=excluded.steps, distance_km=excluded.distance_km,
                calories=excluded.calories, active_calories=excluded.active_calories
        """, [r["date"], r["steps"], r["distance_km"], r["calories"], r["active_calories"]])
    conn.close()


def db_upsert_heart_rate(rows: list):
    if not rows:
        return
    conn = _get_db()
    for r in rows:
        conn.execute("""
            INSERT INTO heart_rate (date, resting_hr, max_hr, min_hr)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                resting_hr=excluded.resting_hr, max_hr=excluded.max_hr, min_hr=excluded.min_hr
        """, [r["date"], r["resting_hr"], r["max_hr"], r["min_hr"]])
    conn.close()


def db_upsert_stress(rows: list):
    if not rows:
        return
    conn = _get_db()
    for r in rows:
        conn.execute("""
            INSERT INTO stress (date, avg_stress, max_stress, min_stress, low_minutes, medium_minutes, high_minutes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                avg_stress=excluded.avg_stress, max_stress=excluded.max_stress,
                min_stress=excluded.min_stress, low_minutes=excluded.low_minutes,
                medium_minutes=excluded.medium_minutes, high_minutes=excluded.high_minutes
        """, [r["date"], r["avg_stress"], r["max_stress"], r["min_stress"],
              r["low_minutes"], r["medium_minutes"], r["high_minutes"]])
    conn.close()


def db_upsert_sleep(rows: list):
    if not rows:
        return
    conn = _get_db()
    for row in rows:
        levels = row.pop("sleep_levels", None)
        conn.execute("""
            INSERT INTO sleep (date, sleep_score, sleep_time_min, deep_sleep_min,
                               light_sleep_min, rem_sleep_min, awake_min)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                sleep_score=excluded.sleep_score, sleep_time_min=excluded.sleep_time_min,
                deep_sleep_min=excluded.deep_sleep_min, light_sleep_min=excluded.light_sleep_min,
                rem_sleep_min=excluded.rem_sleep_min, awake_min=excluded.awake_min
        """, [row["date"], row["sleep_score"], row["sleep_time_min"],
              row["deep_sleep_min"], row["light_sleep_min"], row["rem_sleep_min"], row["awake_min"]])
        if levels:
            conn.execute("DELETE FROM sleep_levels WHERE date=?", [row["date"]])
            for lv in levels:
                conn.execute("INSERT INTO sleep_levels (date, start, end_time, level) VALUES (?,?,?,?)",
                             [row["date"], lv["start"], lv["end"], lv["level"]])
    conn.close()


def db_upsert_respiration(rows: list):
    if not rows:
        return
    conn = _get_db()
    for r in rows:
        conn.execute("""
            INSERT INTO respiration (date, avg_respiration, max_respiration, min_respiration)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                avg_respiration=excluded.avg_respiration,
                max_respiration=excluded.max_respiration,
                min_respiration=excluded.min_respiration
        """, [r["date"], r["avg_respiration"], r["max_respiration"], r["min_respiration"]])
    conn.close()


def db_upsert_activities(rows: list):
    if not rows:
        return
    conn = _get_db()
    for r in rows:
        at = r.get("activityType", {})
        if isinstance(at, dict):
            r["activityType"] = at.get("typeKey", "unknown")
        conn.execute("""
            INSERT INTO activities (activityId, activityName, activityType, startTimeLocal,
                                    distance, duration, averageHeartRate, maxHeartRate, calories, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(activityId) DO UPDATE SET
                activityName=excluded.activityName, activityType=excluded.activityType,
                startTimeLocal=excluded.startTimeLocal, distance=excluded.distance,
                duration=excluded.duration, averageHeartRate=excluded.averageHeartRate,
                maxHeartRate=excluded.maxHeartRate, calories=excluded.calories,
                source=excluded.source
        """, [r["activityId"], r.get("activityName", ""), r.get("activityType", ""),
              r.get("startTimeLocal", ""), r.get("distance", 0), r.get("duration", 0),
              r.get("averageHeartRate"), r.get("maxHeartRate"), r.get("calories"),
              r.get("source", "")])
    conn.close()


def db_upsert_body_battery(rows: list):
    if not rows:
        return
    conn = _get_db()
    for r in rows:
        conn.execute("""
            INSERT INTO body_battery (date, value) VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET value=excluded.value
        """, [r["date"], r["value"]])
    conn.close()


def db_save_bp(rows: list):
    conn = _get_db()
    conn.execute("DELETE FROM blood_pressure")
    for r in rows:
        conn.execute("INSERT INTO blood_pressure (datum, time, sys, dia, pulse) VALUES (?,?,?,?,?)",
                     [r["datum"], r["time"], r["sys"], r["dia"], r["pulse"]])
    conn.close()


def db_get_bp():
    conn = _get_db()
    df = conn.execute("SELECT datum, time, sys, dia, pulse FROM blood_pressure ORDER BY datum, time").fetchdf()
    conn.close()
    if df.empty:
        return None
    try:
        df["datum"] = pd.to_datetime(df["datum"], format="%d.%m.%y", errors="coerce")
    except (ValueError, TypeError):
        df["datum"] = pd.to_datetime(df["datum"], errors="coerce", dayfirst=True)
    df["time"] = pd.to_datetime(df["time"].astype(str).str.strip(), format="mixed", errors="coerce")
    df["datetime"] = pd.to_datetime(
        df["datum"].dt.strftime("%Y-%m-%d") + " " + df["time"].dt.strftime("%H:%M:%S"),
        errors="coerce"
    )
    for col in ("sys", "dia", "pulse"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime"]).reset_index(drop=True)
    return df if not df.empty else None


def db_load_garmin_data() -> dict:
    conn = _get_db()

    def table_to_list(name):
        df = conn.execute(f"SELECT * FROM {name} ORDER BY date").fetchdf()
        return df.to_dict("records") if not df.empty else []

    data = {
        "daily_stats": table_to_list("daily_stats"),
        "heart_rate": table_to_list("heart_rate"),
        "stress": table_to_list("stress"),
        "sleep": table_to_list("sleep"),
        "respiration": table_to_list("respiration"),
        "activities": [],
        "body_battery": [],
    }

    df_act = conn.execute("SELECT * FROM activities ORDER BY startTimeLocal").fetchdf()
    if not df_act.empty:
        data["activities"] = df_act.to_dict("records")

    df_bb = conn.execute("SELECT * FROM body_battery ORDER BY date").fetchdf()
    if not df_bb.empty:
        data["body_battery"] = df_bb.to_dict("records")

    df_lv = conn.execute("SELECT date, start, end_time, level FROM sleep_levels ORDER BY start").fetchdf()
    if not df_lv.empty:
        levels_by_date = {}
        for _, r in df_lv.iterrows():
            levels_by_date.setdefault(r["date"], []).append(
                {"start": r["start"], "end": r["end_time"], "level": int(r["level"])})
        for entry in data["sleep"]:
            if entry["date"] in levels_by_date:
                entry["sleep_levels"] = levels_by_date[entry["date"]]

    for a in data["activities"]:
        if isinstance(a.get("activityType"), str):
            a["activityType"] = {"typeKey": a["activityType"]}

    conn.close()
    return data


def db_get_last_date():
    conn = _get_db()
    row = conn.execute("SELECT MAX(date) FROM daily_stats").fetchone()
    conn.close()
    if row and row[0]:
        return date.fromisoformat(row[0])
    return None


def db_set_meta(key: str, value: str):
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", [key, value])
    conn.close()


def db_get_meta(key: str):
    conn = _get_db()
    row = conn.execute("SELECT value FROM meta WHERE key=?", [key]).fetchone()
    conn.close()
    return row[0] if row else None


# ─── Colors ───────────────────────────────────────────────────────────────────

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
    "bp_sys":       "#E74C3C",
    "bp_dia":       "#2980B9",
    "bp_pulse":     "#2ECC71",
}   
    
# ─── FIT Reader ───────────────────────────────────────────────────────────────

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


# ─── Stats ────────────────────────────────────────────────────────────────────

def compute_stats(series: pd.Series) -> str:
    s = series.dropna()
    if s.empty:
        return "No data"
    return f"Avg: {s.mean():.1f}  |  Min: {s.min():.1f}  |  Max: {s.max():.1f}"


# ─── Art Functions ────────────────────────────────────────────────────────────

def art_daily_mosaic(garmin_data: dict, size: int = 3000, metric: str = "steps", palette: str = "thermal") -> Image.Image:
        
    daily = garmin_data.get("daily_stats", [])
    if not daily:
        img = Image.new("RGB", (size, size), "#0d1117")
        draw = ImageDraw.Draw(img)
        draw.text((size // 2, size // 2), "No daily data", fill="#888")
        return img
    palettes = {
        "thermal":  [(30, 40, 80), (255, 100, 30), (255, 30, 30)],
        "ocean":    [(10, 20, 60), (0, 150, 180), (100, 230, 255)],
        "neon":     [(40, 10, 80), (200, 50, 255), (50, 255, 150)],
        "rainbow":  [(255, 0, 0), (255, 255, 0), (0, 200, 0)],
    }
    stops = palettes.get(palette, palettes["thermal"])
    n = len(daily)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    cell = size // max(cols, rows)
    img_size = max(cols * cell, rows * cell)
    img = Image.new("RGB", (img_size, img_size), "#0d1117")
    draw = ImageDraw.Draw(img)
    values = [d.get(metric, 0) or 0 for d in daily]
    v_min, v_max = min(values), max(values)
    v_range = v_max - v_min or 1

    def lerp_color(t):
        if t <= 0.5:
            c0, c1 = stops[0], stops[1]
            lt = t * 2
        else:
            c0, c1 = stops[1], stops[2]
            lt = (t - 0.5) * 2
        return tuple(int(c0[i] + (c1[i] - c0[i]) * lt) for i in range(3))

    for i, d in enumerate(daily):
        col = i % cols
        row = i // cols
        x0, y0 = col * cell, row * cell
        t = ((d.get(metric, 0) or 0) - v_min) / v_range
        draw.rectangle([x0 + 1, y0 + 1, x0 + cell - 1, y0 + cell - 1], fill=lerp_color(t))
    for i in [0, n - 1]:
        col, row = i % cols, i // cols
        draw.text((col * cell + 4, row * cell + 4), daily[i].get("date", "")[:10], fill="#ffffff")
    draw.text((20, 20), f"Daily {metric}  |  {n} days  |  {palette}", fill="#ffffff")
    return img


def art_sleep_heatmap(garmin_data: dict, size: int = 3000, palette: str = "thermal") -> Image.Image:
    sleep = garmin_data.get("sleep", [])
    sleep = [s for s in sleep if s.get("deep_sleep_min") is not None]
    if not sleep:
        img = Image.new("RGB", (size, size), "#0d1117")
        draw = ImageDraw.Draw(img)
        draw.text((size // 2, size // 2), "No sleep data", fill="#888")
        return img
    palettes = {
        "thermal": {"deep": (27, 42, 74), "light": (93, 173, 226), "rem": (244, 208, 63), "awake": (231, 76, 60)},
        "ocean":   {"deep": (10, 25, 60), "light": (0, 140, 170), "rem": (100, 220, 200), "awake": (255, 120, 80)},
        "neon":    {"deep": (50, 10, 100), "light": (0, 220, 255), "rem": (255, 80, 200), "awake": (50, 255, 100)},
        "rainbow": {"deep": (180, 0, 60), "light": (0, 150, 200), "rem": (255, 200, 0), "awake": (50, 200, 50)},
    }
    colors = palettes.get(palette, palettes["thermal"])
    n = len(sleep)
    bar_h = max(4, int(size * 0.7 / max(n, 1)))
    img = Image.new("RGB", (size, size), "#0d1117")
    draw = ImageDraw.Draw(img)
    max_total = max(
        (s.get("deep_sleep_min", 0) + s.get("light_sleep_min", 0) +
         s.get("rem_sleep_min", 0) + s.get("awake_min", 0)) or 0 for s in sleep) or 1
    y_start = int(size * 0.15)
    for i, s in enumerate(sleep):
        y = y_start + i * bar_h
        x = int(size * 0.15)
        total_w = int(size * 0.75)
        for key in ("deep", "light", "rem", "awake"):
            mins = s.get(f"{key}_sleep_min", 0) or 0
            if key == "awake":
                mins = s.get("awake_min", 0) or 0
            w = int(total_w * mins / max_total)
            if w > 0:
                draw.rectangle([x, y, x + w, y + bar_h - 1], fill=colors[key])
                x += w
    ly = int(size * 0.08)
    for j, (label, color) in enumerate(colors.items()):
        lx = int(size * 0.15) + j * 120
        draw.rectangle([lx, ly, lx + 16, ly + 16], fill=color)
        draw.text((lx + 22, ly), label.capitalize(), fill="#ccc")
    draw.text((20, 20), f"Sleep  |  {n} nights  |  {palette}", fill="#ffffff")
    return img


def art_bp_wave(bp_df: pd.DataFrame, size: int = 3000, palette: str = "thermal") -> Image.Image:
    if bp_df is None or bp_df.empty:
        img = Image.new("RGB", (size, size), "#0d1117")
        draw = ImageDraw.Draw(img)
        draw.text((size // 2, size // 2), "No BP data", fill="#888")
        return img
    palettes = {
        "thermal": {"sys": (231, 76, 60), "dia": (41, 128, 185), "pulse": (46, 204, 113)},
        "ocean":   {"sys": (255, 120, 80), "dia": (0, 180, 200), "pulse": (100, 230, 255)},
        "neon":    {"sys": (255, 50, 150), "dia": (0, 220, 255), "pulse": (50, 255, 100)},
        "rainbow": {"sys": (255, 100, 0), "dia": (0, 150, 255), "pulse": (0, 220, 80)},
    }
    colors = palettes.get(palette, palettes["thermal"])
    img = Image.new("RGB", (size, size), "#0d1117")
    draw = ImageDraw.Draw(img)
    df = bp_df.sort_values("datetime").reset_index(drop=True)
    n = len(df)
    margin = 0.1
    plot_w = int(size * (1 - 2 * margin))
    plot_h = int(size * 0.6)
    y0 = int(size * 0.25)
    all_min = min(df["sys"].min(), df["dia"].min()) - 5
    all_max = max(df["sys"].max(), df["dia"].max()) + 5
    v_range = all_max - all_min or 1

    def to_xy(i, val):
        x = int(margin * size + i / max(n - 1, 1) * plot_w)
        y = int(y0 + plot_h - (val - all_min) / v_range * plot_h)
        return x, y

    for i in range(n - 1):
        draw.line([to_xy(i, df["sys"].iloc[i]), to_xy(i + 1, df["sys"].iloc[i + 1])], fill=colors["sys"], width=3)
    for i in range(n - 1):
        draw.line([to_xy(i, df["dia"].iloc[i]), to_xy(i + 1, df["dia"].iloc[i + 1])], fill=colors["dia"], width=3)
    if "pulse" in df.columns and df["pulse"].notna().any():
        for i in range(n):
            p = to_xy(i, df["pulse"].iloc[i])
            draw.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=colors["pulse"])
    draw.text((20, 20), f"Blood Pressure  |  {n} readings  |  {palette}", fill="#ffffff")
    draw.text((int(size * 0.65), y0 - 30), "— Sys  — Dia  • Pulse", fill="#aaa")
    return img   
    
# ─── Data Fetchers ────────────────────────────────────────────────────────────

def garmin_login(email: str, password: str,
                 token_path: str = "~/.garminconnect",
                 status_cb=None, mfa_callback=None) -> tuple:
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


def find_oldest_activity_date(client: Garmin, status_cb=None):
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
                "date": ds, "steps": s.get("totalSteps", 0) or 0,
                "distance_km": (s.get("totalDistanceMeters", 0) or 0) / 1000,
                "calories": s.get("totalKilocalories", 0) or 0,
                "active_calories": s.get("activeKilocalories", 0) or 0,
            })
        hr = _safe_call(client.get_heart_rates, ds)
        if hr:
            hr_data.append({"date": ds, "resting_hr": hr.get("restingHeartRate"),
                            "max_hr": hr.get("maxHeartRate"), "min_hr": hr.get("minHeartRate")})
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
                entry = {"date": ds, "sleep_score": score,
                         "sleep_time_min": sleep_time / 60,
                         "deep_sleep_min": (dto.get("deepSleepSeconds", 0) or 0) / 60,
                         "light_sleep_min": (dto.get("lightSleepSeconds", 0) or 0) / 60,
                         "rem_sleep_min": (dto.get("remSleepSeconds", 0) or 0) / 60,
                         "awake_min": (dto.get("awakeSleepSeconds", 0) or 0) / 60}
                levels = sl.get("sleepLevels", [])
                if levels:
                    entry["sleep_levels"] = [
                        {"start": lv.get("startGMT"), "end": lv.get("endGMT"),
                         "level": int(lv.get("activityLevel", 0))} for lv in levels]
                sleep_data.append(entry)
        resp = _safe_call(client.get_respiration_data, ds)
        if resp:
            resp_data.append({
                "date": ds,
                "avg_respiration": (resp.get("averageRespirationRate") or resp.get("avgRespirationRate")
                                    or resp.get("respirationRate")),
                "max_respiration": resp.get("maxRespirationRate") or resp.get("maxRespiration"),
                "min_respiration": resp.get("minRespirationRate") or resp.get("minRespiration"),
            })
        d += timedelta(days=1)
        time.sleep(0.3)
    return {"daily_stats": daily_stats, "heart_rate": hr_data,
            "stress": stress_data, "sleep": sleep_data, "respiration": resp_data}


def fetch_full_history(client: Garmin, status_cb=None) -> dict:
    def _msg(s):
        if status_cb:
            status_cb(s)
    _msg("🔍 Finding oldest activity date…")
    oldest = find_oldest_activity_date(client, status_cb)
    if oldest is None:
        oldest = date.today() - timedelta(days=365)
    end = date.today()
    _msg(f"  → Range: {oldest.isoformat()} → {end.isoformat()}")
    all_acts = []
    start = 0
    while True:
        batch = _safe_call(client.get_activities, start, 100)
        if not batch:
            break
        all_acts.extend(batch)
        if len(batch) < 100:
            break
        start += 100
        time.sleep(0.5)
    _msg(f"  → {len(all_acts)} activities")
    daily = fetch_daily_data(client, oldest, end, status_cb)
    bb = _safe_call(client.get_body_battery, oldest.isoformat(), end.isoformat())
    daily["body_battery"] = bb if isinstance(bb, list) else ([bb] if bb else [])
    daily["activities"] = all_acts
    return daily


def fetch_incremental(client: Garmin, last_date: date, status_cb=None) -> dict:
    def _msg(s):
        if status_cb:
            status_cb(s)
    end = date.today()
    start = last_date + timedelta(days=1)
    _msg(f"  → Updating {start.isoformat()} → {end.isoformat()}")
    acts = _safe_call(client.get_activities_by_date, start.isoformat(), end.isoformat())
    daily = fetch_daily_data(client, start, end, status_cb)
    bb = _safe_call(client.get_body_battery, start.isoformat(), end.isoformat())
    daily["body_battery"] = bb if isinstance(bb, list) else []
    daily["activities"] = acts if acts else []
    return daily


def save_to_db(merged: dict):
    """Upsert data into DB. Existing rows are overwritten, new rows are added."""
    if merged.get("daily_stats"):
        db_upsert_daily_stats(merged["daily_stats"])
    if merged.get("heart_rate"):
        db_upsert_heart_rate(merged["heart_rate"])
    if merged.get("stress"):
        db_upsert_stress(merged["stress"])
    if merged.get("sleep"):
        db_upsert_sleep([dict(s) for s in merged["sleep"]])
    if merged.get("respiration"):
        db_upsert_respiration(merged["respiration"])
    if merged.get("activities"):
        db_upsert_activities([dict(a) for a in merged["activities"]])
    if merged.get("body_battery"):
        bb_rows = []
        for e in merged["body_battery"]:
            d = e.get("date") or e.get("calendarDate")
            v = e.get("charged") or e.get("bodyBattery") or e.get("value")
            if d and v is not None:
                bb_rows.append({"date": d, "value": v})
        if bb_rows:
            db_upsert_body_battery(bb_rows)   
            
def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {"email": "", "password": "", "local_fit_path": "",
            "token_path": "~/.garminconnect",
            "sleep_start": "22:00", "sleep_end": "07:00",
            "theme": "darkly"}

            
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
        self.notebook.add(self.tab_art, text="  🎨 Art  ")
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
        sb = getattr(self, 'xscrollbar_data', None)
        if sb is not None:
            sb.config(troughcolor=c["fig_bg"], background=c["spine"], activebackground=c["text"])   
        
    # ─── Garmin Tab ─────────────────────────────────────────────────────────

    def _build_garmin_tab(self):
        frame = ttk.Frame(self.tab_garmin)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

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
        Button(theme_frame, text="🌙/☀ Toggle", bootstyle="secondary",
               command=self._toggle_dark).grid(row=0, column=3, padx=5)

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

        local_frame = ttk.LabelFrame(frame, text="Garmin Local")
        local_frame.pack(fill=tk.X, padx=5, pady=10)
        ttk.Label(local_frame, text="Path:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.local_fit_path_var = tk.StringVar(value=self.settings.get("local_fit_path", ""))
        ttk.Entry(local_frame, textvariable=self.local_fit_path_var, width=50).grid(
            row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        Button(local_frame, text="Browse…",
               command=self._browse_local_fit_path).grid(row=0, column=2, padx=5)
        Button(local_frame, text="⬇ Import FIT Files", bootstyle="primary",
               command=self._on_local_fit_import).grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        Button(local_frame, text="💾 Save to DB", bootstyle="success",
               command=self._on_local_save_db).grid(row=1, column=1, padx=5, pady=5, sticky=tk.E)   
        self.local_fit_info_var = tk.StringVar(value="")
        ttk.Label(local_frame, textvariable=self.local_fit_info_var).grid(
            row=1, column=2, padx=5, pady=5, sticky=tk.W)
        local_frame.columnconfigure(1, weight=1)

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

        Button(frame, text="Save Settings", bootstyle="success",
               command=self._save_settings).pack(anchor=tk.W, padx=5, pady=10)
               
    def _on_local_save_db(self):
        if not self.garmin_data.get("daily_stats") and not self.garmin_data.get("activities"):
            messagebox.showinfo("Info", "No data to save. Import first.")
            return

        self.status_var.set("💾 Saving to database…")
        self.root.update()

        def work():
            try:
                save_to_db(self.garmin_data)
                db_set_meta("first_download", db_get_meta("first_download") or date.today().isoformat())
                db_set_meta("last_update", date.today().isoformat())
                # Reload from DB to ensure chart matches what's actually stored
                self.garmin_data = db_load_garmin_data()
                n_days = len(self.garmin_data.get("daily_stats", []))
                n_acts = len(self.garmin_data.get("activities", []))
                dates = [d["date"] for d in self.garmin_data.get("daily_stats", []) if d.get("date")]
                d_min = min(dates) if dates else "?"
                d_max = max(dates) if dates else "?"
                msg = f"✅ Data saved to database  ({n_days} days: {d_min} → {d_max}, {n_acts} activities)"
            except Exception as e:
                err_msg = str(e)
                msg = f"❌ Save failed: {err_msg}"
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
            "token_path": self.token_path_var.get().strip(),
            "sleep_start": self.sleep_start_var.get().strip(),
            "sleep_end": self.sleep_end_var.get().strip(),
            "theme": self.theme_var.get().strip(),
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

                # ── 1. MONITOR folder ──
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

                # ── 2. SLEEP folder ──
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

                # ── 3. ACTIVITY folder ──
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

                # Show date range
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
        Button(top, text="🔄 Reload from DB", bootstyle="info",
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
        self.ax_data.set_xlim(center - half, center + half)
        self.fig_data.canvas.draw_idle()
        self._update_data_xscrollbar()

    def _zoom_out(self):
        x_lo, x_hi = self.ax_data.get_xlim()
        if x_hi - x_lo <= 0:
            return
        center = (x_lo + x_hi) / 2
        half = (x_hi - x_lo) * 0.75
        self.ax_data.set_xlim(center - half, center + half)
        self.fig_data.canvas.draw_idle()
        self._update_data_xscrollbar()

    def _zoom_reset(self):
        lo, hi = self._data_x_full_range
        if hi > lo:
            self.ax_data.set_xlim(lo, hi)
            self.fig_data.canvas.draw_idle()
        self._update_data_xscrollbar()

    def _update_data_info(self):
        n_days = len(self.garmin_data.get("daily_stats", []))
        dates = [d["date"] for d in self.garmin_data.get("daily_stats", []) if d.get("date")]
        d_min = min(dates) if dates else None
        d_max = max(dates) if dates else None
        if d_min and d_max:
            self.data_info_var.set(f"Data: {d_min} → {d_max}  |  {n_days} days")
        else:
            self.data_info_var.set("No data yet – click Connect Update or Import")   

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
        self.ax_data.set_xlim(center - half, center + half)
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
            self.ax_data.set_xlim(new_lo, new_hi)
            self.fig_data.canvas.draw_idle()
            self._update_data_xscrollbar()   
            
    # ─── Download / Update ──────────────────────────────────────────────────

    def _get_credentials(self) -> tuple:
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        token_path = self.token_path_var.get().strip() or "~/.garminconnect"
        return email, password, token_path

    def _on_connect_update(self):
        if not GARMIN_AVAILABLE:
            messagebox.showerror("Error",
                                 "garminconnect not installed.\n"
                                 "Run: pip install garminconnect==0.3.20 curl_cffi")
            return
        email, password, token_path = self._get_credentials()
        if not email or not password:
            messagebox.showwarning("Warning", "Enter email and password in Garmin tab.")
            return

        last_date = db_get_last_date()
        is_full = last_date is None

        if is_full:
            self._show_progress("Garmin Connect – Full Download")
            self._set_status("🔗 No data in DB. Starting full download…")
        else:
            if last_date >= date.today():
                self.status_var.set("✅ Already up to date (DB current).")
                return
            self._show_progress("Garmin Connect – Incremental Update")
            self._set_status(f"🔗 DB has data up to {last_date.isoformat()}. Fetching new days…")

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
                self.root.after(0, lambda: messagebox.showerror("Update Error", err_msg))
        threading.Thread(target=work, daemon=True).start()

    def _on_reload_from_db(self):
        self.status_var.set("🔄 Reloading from database…")
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

    def _redraw_data_chart(self):
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
            bottom = np.zeros(len(df))
            self.ax_data.bar(df["date"], df["deep_sleep_min"].fillna(0), color=COLORS["sleep_deep"],
                             label="Deep", bottom=bottom, width=0.8)
            bottom += df["deep_sleep_min"].fillna(0).values
            self.ax_data.bar(df["date"], df["light_sleep_min"].fillna(0), color=COLORS["sleep_light"],
                             label="Light", bottom=bottom, width=0.8)
            bottom += df["light_sleep_min"].fillna(0).values
            self.ax_data.bar(df["date"], df["rem_sleep_min"].fillna(0), color=COLORS["sleep_rem"],
                             label="REM", bottom=bottom, width=0.8)
            self.ax_data.set_ylabel("Minutes")
            self.ax_data.set_title("Sleep Summary")
            stats_text = (f"Total: {compute_stats(df['sleep_time_min'])} min   |   "
                          f"Deep: {compute_stats(df['deep_sleep_min'])}   |   "
                          f"REM: {compute_stats(df['rem_sleep_min'])}   |   "
                          f"Score: {compute_stats(df['sleep_score'])}")

        elif choice == "sleep_tracing" and self.garmin_data.get("sleep"):
            sleep_entries = [e for e in self.garmin_data.get("sleep", []) if e.get("sleep_levels")]
            if not sleep_entries:
                self.ax_data.text(0.5, 0.5, "No sleep tracing data available.",
                                  ha="center", va="center", fontsize=11)
            else:
                sleep_entries = sorted(sleep_entries, key=lambda x: x["date"])[-14:]
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

        elif choice == "body_battery" and self.garmin_data.get("body_battery"):
            bb = self.garmin_data["body_battery"]
            if bb:
                dates, values = [], []
                for entry in bb:
                    d = entry.get("date")
                    v = entry.get("value")
                    if d and v is not None:
                        try:
                            dates.append(pd.Timestamp(d))
                            values.append(v)
                        except Exception:
                            pass
                if dates:
                    self.ax_data.fill_between(dates, values, alpha=0.3, color=COLORS["body_battery"])
                    self.ax_data.plot(dates, values, color=COLORS["body_battery"],
                                      linewidth=1.5, label="Body Battery")
                    self.ax_data.set_ylabel("Points")
                    self.ax_data.set_title("Body Battery")
                    stats_text = f"Body Battery: {compute_stats(pd.Series(values))}"
                else:
                    self.ax_data.text(0.5, 0.5, "No valid body battery entries",
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
                width = 0.3
                self.ax_data.bar(x - width, df["sys"], width=width, color=COLORS["bp_sys"], label="Systolic")
                self.ax_data.bar(x, df["dia"], width=width, color=COLORS["bp_dia"], label="Diastolic")
                if "pulse" in df.columns and df["pulse"].notna().any():
                    self.ax_data.bar(x + width, df["pulse"], width=width, color=COLORS["bp_pulse"], label="Pulse")
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
                if "pulse" in df.columns:
                    parts.append(f"Pulse: {compute_stats(df['pulse'])} bpm")
                stats_text = "   |   ".join(parts)

        else:
            self.ax_data.text(0.5, 0.5, "No data – click Full Download or Import",
                              ha="center", va="center", fontsize=12)

        self._safe_legend(self.ax_data, twin_ax)
        self.ax_data.tick_params(axis="x", rotation=30)
        self._style_figure(self.fig_data, self.ax_data, twin_ax)
        self.fig_data.tight_layout()
        self.canvas_data.draw()
        self.stats_var.set(stats_text)
        x_lo, x_hi = self.ax_data.get_xlim()
        if x_hi > x_lo:
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
        Button(top, text="💾 Save to DB", bootstyle="primary",
               command=self._on_bp_save_all).pack(side=tk.LEFT, padx=5)
        Button(top, text="🔄 Reload", bootstyle="info",
               command=self._on_bp_reload).pack(side=tk.LEFT, padx=5)
        self.bp_tab_info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.bp_tab_info_var).pack(side=tk.LEFT, padx=15)

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
                "pulse": float(vals[4]) if vals[4] else None,
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
            p = int(row["pulse"]) if pd.notna(row["pulse"]) else ""
            self.bp_tree.insert("", tk.END, values=(d, t, s, dia, p))   
            
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

    _ART_DATA_SOURCES = {
        "daily_stats":    "Daily Stats (Steps/Cal)",
        "heart_rate":     "Heart Rate",
        "stress":         "Stress",
        "sleep":          "Sleep",
        "respiration":    "Respiration",
        "body_battery":   "Body Battery",
        "blood_pressure": "Blood Pressure",
    }

    def _build_art_tab(self):
        frame = ttk.Frame(self.tab_art)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ── Top controls ──
        ctrl = ttk.LabelFrame(frame, text="Art Settings")
        ctrl.pack(fill=tk.X, padx=5, pady=5)

        # Row 0: Style
        ttk.Label(ctrl, text="Style:").grid(row=0, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_style_var = tk.StringVar(value="scribble")
        style_frame = ttk.Frame(ctrl)
        style_frame.grid(row=0, column=1, columnspan=5, padx=5, pady=2, sticky=tk.W)
        for i, (key, label) in enumerate(self._ART_STYLES.items()):
            ttk.Radiobutton(style_frame, text=label, value=key,
                            variable=self.art_style_var,
                            command=self._on_art_style_changed).grid(
                row=0, column=i, padx=6, pady=2, sticky=tk.W)

        # Row 1: Data source + Metric
        ttk.Label(ctrl, text="Data:").grid(row=1, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_data_var = tk.StringVar(value="stress")
        self.art_data_combo = ttk.Combobox(ctrl, textvariable=self.art_data_var,
                                           values=list(self._ART_DATA_SOURCES.keys()),
                                           width=16, state="readonly")
        self.art_data_combo.bind("<<ComboboxSelected>>", lambda e: self._on_art_data_changed())
        ttk.Label(ctrl, text="Metric:").grid(row=1, column=2, padx=5, pady=4, sticky=tk.W)
        self.art_metric_var = tk.StringVar(value="avg_stress")
        self.art_metric_combo = ttk.Combobox(ctrl, textvariable=self.art_metric_var,
                                             values=[], width=14, state="readonly")
        self.art_metric_combo.grid(row=1, column=3, padx=5, pady=4, sticky=tk.W)

        # Row 2: Period (Weekly / Monthly) + Period dropdown
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

        # Row 3: Palette + Size
        ttk.Label(ctrl, text="Palette:").grid(row=3, column=0, padx=5, pady=4, sticky=tk.W)
        self.art_palette_var = tk.StringVar(value="thermal")
        palettes = ["thermal", "ocean", "neon", "rainbow"]
        self.art_palette_combo = ttk.Combobox(ctrl, textvariable=self.art_palette_var,
                                              values=palettes, width=10, state="readonly")
        self.art_palette_combo.grid(row=3, column=1, padx=5, pady=4, sticky=tk.W)

        ttk.Label(ctrl, text="Size:").grid(row=3, column=2, padx=5, pady=4, sticky=tk.W)
        self.art_size_var = tk.StringVar(value="3000")
        sizes = ["1500", "2000", "3000", "4000", "6000"]
        self.art_size_combo = ttk.Combobox(ctrl, textvariable=self.art_size_var,
                                           values=sizes, width=6, state="readonly")
        self.art_size_combo.grid(row=3, column=3, padx=5, pady=4, sticky=tk.W)

        # ── Buttons ──
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=5, pady=5)
        Button(btn_row, text="🎨 Generate", bootstyle="primary",
               command=self._on_generate_art).pack(side=tk.LEFT, padx=5)
        Button(btn_row, text="💾 Save PNG", bootstyle="success",
               command=self._on_save_art).pack(side=tk.LEFT, padx=5)
        self.art_info_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self.art_info_var).pack(side=tk.LEFT, padx=15)

        # ── Preview ──
        preview_frame = ttk.Frame(frame)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.art_preview_label = tk.Label(preview_frame, text="Art will appear here",
                                          bg="#353535", fg="#666",
                                          font=("TkDefaultFont", 14))
        self.art_preview_label.pack(fill=tk.BOTH, expand=True)
        self._art_image = None
        self._art_photo = None

        # Initialize period dropdown
        self._on_art_period_changed()

    # ── Art helpers ──

    def _on_art_style_changed(self):
        """Show/hide metric combo based on style."""
        style = self.art_style_var.get()
        needs_metric = style in ("daily_mosaic", "scribble", "kandinsky",
                                  "mondrian", "rothko", "pollock", "bauhaus", "wild_mix")
        if needs_metric:
            self.art_metric_combo.state(["!disabled"])
            self._populate_art_metrics()
        else:
            self.art_metric_combo.state(["disabled"])

    def _on_art_data_changed(self):
        self._populate_art_metrics()
        self._populate_art_periods()

    def _on_art_period_changed(self):
        """Rebuild the period dropdown when Weekly/Monthly is toggled."""
        self._populate_art_periods()

    def _populate_art_metrics(self):
        """Set available metrics for the selected data source."""
        data_key = self.art_data_var.get()
        metrics_map = {
            "daily_stats":    ["steps", "calories", "active_calories", "distance_km"],
            "heart_rate":     ["resting_hr", "max_hr", "min_hr"],
            "stress":         ["avg_stress", "max_stress", "min_stress",
                               "low_minutes", "medium_minutes", "high_minutes"],
            "sleep":          ["sleep_time_min", "deep_sleep_min", "light_sleep_min",
                               "rem_sleep_min", "awake_min", "sleep_score"],
            "respiration":    ["avg_respiration", "max_respiration", "min_respiration"],
            "body_battery":   ["value"],
            "blood_pressure": ["sys", "dia", "pulse"],
        }
        vals = metrics_map.get(data_key, [])
        self.art_metric_combo.configure(values=vals)
        if vals:
            self.art_metric_var.set(vals[0])

    def _populate_art_periods(self):
        """Populate the period dropdown with available weeks or months."""
        period_mode = self.art_period_var.get()
        data_key = self.art_data_var.get()

        # Get dates from the relevant data source
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
            self.art_period_select_var.set(labels[-1])  # default to latest

    def _get_dates_for_data_source(self, data_key: str) -> list:
        """Extract a list of pd.Timestamp dates from the given data source."""
        if data_key == "blood_pressure":
            if self.bp_data is not None and not self.bp_data.empty:
                return list(self.bp_data["datetime"].dropna())
            return []
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
        """Return (start_date, end_date) for the selected period."""
        period_mode = self.art_period_var.get()
        selected = self.art_period_select_var.get()
        if not selected:
            return None, None
        if period_mode == "weekly":
            # Format: "2025-W42"
            try:
                year, week = selected.split("-W")
                year, week = int(year), int(week)
                # Monday of that ISO week
                start = date.fromisocalendar(year, week, 1)
                end = date.fromisocalendar(year, week, 7)
                return pd.Timestamp(start), pd.Timestamp(end)
            except Exception:
                return None, None
        else:
            # Format: "2025-06"
            try:
                year, month = map(int, selected.split("-"))
                start = pd.Timestamp(year=year, month=month, day=1)
                end = (start + pd.offsets.MonthEnd(0))
                return start, end
            except Exception:
                return None, None

    def _extract_art_data(self) -> dict:
        """Extract min/avg/max arrays and color data for the selected period.

        Returns a dict with keys:
            data_min, data_avg, data_max  – arrays of floats (normalized 0-1 for scribble)
            raw_min, raw_avg, raw_max     – raw values (for labels)
            color_values                   – per-day values for color mapping
            title                          – display title
            value_label                    – summary string
            n_points                       – number of data points
        """
        data_key = self.art_data_var.get()
        metric = self.art_metric_var.get()
        start, end = self._get_period_date_range()

        if start is None:
            return {}

        # Get relevant rows in range
        if data_key == "blood_pressure":
            if self.bp_data is None or self.bp_data.empty:
                return {}
            df = self.bp_data[(self.bp_data["datetime"] >= start) &
                              (self.bp_data["datetime"] <= end)].copy()
            if df.empty:
                return {}
            # Group by day
            df["day"] = df["datetime"].dt.date
            daily = df.groupby("day")[metric].agg(["min", "mean", "max"]).reset_index()
            daily = daily.sort_values("day")
            raw_min = daily["min"].tolist()
            raw_avg = daily["mean"].tolist()
            raw_max = daily["max"].tolist()
            color_values = daily["mean"].tolist()
            n_points = len(daily)
            title = f"BP {metric}"
            value_label = f"avg: {np.mean(raw_avg):.1f}"

        else:
            data = self.garmin_data.get(data_key, [])
            if not data:
                return {}
            # Filter by date range
            filtered = []
            for entry in data:
                d = entry.get("date")
                if not d:
                    continue
                try:
                    ts = pd.Timestamp(d)
                except Exception:
                    continue
                if start <= ts <= end:
                    filtered.append(entry)
            if not filtered:
                return {}

            # For stress, build min/avg/max from the 3 columns
            if data_key == "stress" and metric == "avg_stress":
                raw_min = [e.get("min_stress") or 0 for e in filtered]
                raw_avg = [e.get("avg_stress") or 0 for e in filtered]
                raw_max = [e.get("max_stress") or 0 for e in filtered]
            elif data_key == "heart_rate" and metric == "resting_hr":
                raw_min = [e.get("min_hr") or 0 for e in filtered]
                raw_avg = [e.get("resting_hr") or 0 for e in filtered]
                raw_max = [e.get("max_hr") or 0 for e in filtered]
            elif data_key == "respiration" and metric == "avg_respiration":
                raw_min = [e.get("min_respiration") or 0 for e in filtered]
                raw_avg = [e.get("avg_respiration") or 0 for e in filtered]
                raw_max = [e.get("max_respiration") or 0 for e in filtered]
            elif data_key == "sleep":
                # Use sleep stages as min/avg/max proxy
                raw_min = [e.get("awake_min") or 0 for e in filtered]
                raw_avg = [e.get("sleep_time_min") or 0 for e in filtered]
                raw_max = [e.get("sleep_time_min") or 0 for e in filtered]
                # Better: use deep as "low", total as "avg", score-scaled as "max"
                raw_min = [e.get("deep_sleep_min") or 0 for e in filtered]
                raw_avg = [e.get("light_sleep_min") or 0 for e in filtered]
                raw_max = [e.get("rem_sleep_min") or 0 for e in filtered]
            else:
                vals = [e.get(metric) or 0 for e in filtered]
                raw_avg = vals
                raw_min = [max(0, v * 0.8) for v in vals]
                raw_max = [v * 1.1 for v in vals]

            color_values = raw_avg[:]
            n_points = len(filtered)

            # Title
            period_label = self.art_period_select_var.get()
            source_label = self._ART_DATA_SOURCES.get(data_key, data_key)
            title = f"{source_label} – {period_label}"

            # Value label
            if raw_avg:
                avg_val = np.mean(raw_avg)
                unit = ""
                if "min" in metric or "minutes" in metric:
                    unit = " min"
                elif "hr" in metric or metric in ("sys", "dia", "pulse"):
                    unit = " bpm" if metric == "pulse" else ""
                elif "cal" in metric:
                    unit = " kcal"
                elif "km" in metric:
                    unit = " km"
                value_label = f"avg: {avg_val:.1f}{unit}  |  {n_points} days"
            else:
                value_label = ""

        # Normalize for scribble
        all_vals = raw_min + raw_avg + raw_max
        v_min, v_max = min(all_vals), max(all_vals)
        v_range = v_max - v_min or 1
        data_min = [(v - v_min) / v_range for v in raw_min]
        data_avg = [(v - v_min) / v_range for v in raw_avg]
        data_max = [(v - v_min) / v_range for v in raw_max]

        return {
            "data_min": data_min,
            "data_avg": data_avg,
            "data_max": data_max,
            "raw_min": raw_min,
            "raw_avg": raw_avg,
            "raw_max": raw_max,
            "color_values": color_values,
            "title": title,
            "value_label": value_label,
            "n_points": n_points,
        }

    # ── Data-driven color palettes for art ──

    _DATA_PALETTES = {
        "thermal": {
            "low":  (30, 40, 80),
            "mid":  (255, 100, 30),
            "high": (255, 30, 30),
            "bg":   (13, 17, 23),
        },
        "ocean": {
            "low":  (10, 20, 60),
            "mid":  (0, 150, 180),
            "high": (100, 230, 255),
            "bg":   (5, 10, 30),
        },
        "neon": {
            "low":  (40, 10, 80),
            "mid":  (200, 50, 255),
            "high": (50, 255, 150),
            "bg":   (15, 5, 25),
        },
        "rainbow": {
            "low":  (255, 0, 0),
            "mid":  (255, 255, 0),
            "high": (0, 200, 0),
            "bg":   (20, 20, 20),
        },
    }

    def _lerp_data_color(self, t: float, palette: str) -> tuple:
        """Interpolate a color based on normalized value t (0-1) and palette."""
        stops = self._DATA_PALETTES.get(palette, self._DATA_PALETTES["thermal"])
        if t <= 0.5:
            c0, c1 = stops["low"], stops["mid"]
            lt = t * 2
        else:
            c0, c1 = stops["mid"], stops["high"]
            lt = (t - 0.5) * 2
        return tuple(int(c0[i] + (c1[i] - c0[i]) * lt) for i in range(3))

    # ── Art generation (data-driven wrappers) ──

    def _generate_art_image(self) -> Image.Image:
        """Dispatch to the correct art function with real data."""
        style = self.art_style_var.get()
        size = int(self.art_size_var.get())
        palette = self.art_palette_var.get()
        metric = self.art_metric_var.get()
        data = self._extract_art_data()

        if style == "daily_mosaic":
            return self._art_daily_mosaic_data(data, size, metric, palette)
        elif style == "sleep_heatmap":
            return self._art_sleep_heatmap_data(data, size, palette)
        elif style == "bp_wave":
            return art_bp_wave(self.bp_data, size, palette)
        elif style == "scribble":
            return self._art_scribble_data(data, size, palette)
        elif style == "kandinsky":
            return self._art_kandinsky_data(data, size, palette)
        elif style == "mondrian":
            return self._art_mondrian_data(data, size, palette)
        elif style == "rothko":
            return self._art_rothko_data(data, size, palette)
        elif style == "pollock":
            return self._art_pollock_data(data, size, palette)
        elif style == "bauhaus":
            return self._art_bauhaus_data(data, size, palette)
        elif style == "wild_mix":
            return self._art_wild_mix_data(data, size, palette)
        else:
            raise ValueError(f"Unknown style: {style}")

    def _art_daily_mosaic_data(self, data: dict, size: int, metric: str, palette: str) -> Image.Image:
        """Mosaic where each cell color = that day's value mapped through palette."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#0d1117")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        values = data["color_values"]
        n = len(values)
        cols = int(np.ceil(np.sqrt(n)))
        rows = int(np.ceil(n / cols))
        cell = size // max(cols, rows)
        img_size = max(cols * cell, rows * cell)
        img = Image.new("RGB", (img_size, img_size), "#0d1117")
        draw = ImageDraw.Draw(img)

        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1

        for i, val in enumerate(values):
            col, row = i % cols, i // cols
            x0, y0 = col * cell, row * cell
            t = (val - v_min) / v_range
            color = self._lerp_data_color(t, palette)
            draw.rectangle([x0 + 1, y0 + 1, x0 + cell - 1, y0 + cell - 1], fill=color)

        # Labels
        draw.text((20, 20), data["title"], fill="#ffffff")
        draw.text((20, 45), data["value_label"], fill="#aaa")
        return img

    def _art_sleep_heatmap_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Sleep heatmap using actual stage proportions from the selected period."""
        start, end = self._get_period_date_range()
        sleep_data = self.garmin_data.get("sleep", [])
        filtered = []
        for e in sleep_data:
            d = e.get("date")
            if not d:
                continue
            try:
                ts = pd.Timestamp(d)
            except Exception:
                continue
            if start and end and start <= ts <= end:
                filtered.append(e)

        if not filtered:
            img = Image.new("RGB", (size, size), "#0d1117")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No sleep data", fill="#888")
            return img

        palettes = {
            "thermal": {"deep": (27, 42, 74), "light": (93, 173, 226),
                        "rem": (244, 208, 63), "awake": (231, 76, 60)},
            "ocean":   {"deep": (10, 25, 60), "light": (0, 140, 170),
                        "rem": (100, 220, 200), "awake": (255, 120, 80)},
            "neon":    {"deep": (50, 10, 100), "light": (0, 220, 255),
                        "rem": (255, 80, 200), "awake": (50, 255, 100)},
            "rainbow": {"deep": (180, 0, 60), "light": (0, 150, 200),
                        "rem": (255, 200, 0), "awake": (50, 200, 50)},
        }
        colors = palettes.get(palette, palettes["thermal"])
        n = len(filtered)
        bar_h = max(6, int(size * 0.7 / max(n, 1)))
        img = Image.new("RGB", (size, size), "#0d1117")
        draw = ImageDraw.Draw(img)

        max_total = max(
            (s.get("deep_sleep_min", 0) + s.get("light_sleep_min", 0) +
             s.get("rem_sleep_min", 0) + s.get("awake_min", 0)) or 0 for s in filtered) or 1

        y_start = int(size * 0.15)
        for i, s in enumerate(filtered):
            y = y_start + i * bar_h
            x = int(size * 0.15)
            total_w = int(size * 0.75)
            for key in ("deep", "light", "rem", "awake"):
                mins = s.get(f"{key}_sleep_min", 0) or 0
                if key == "awake":
                    mins = s.get("awake_min", 0) or 0
                w = int(total_w * mins / max_total)
                if w > 0:
                    draw.rectangle([x, y, x + w, y + bar_h - 1], fill=colors[key])
                    x += w

        # Legend
        ly = int(size * 0.08)
        for j, (label, color) in enumerate(colors.items()):
            lx = int(size * 0.15) + j * 140
            draw.rectangle([lx, ly, lx + 16, ly + 16], fill=color)
            draw.text((lx + 22, ly), label.capitalize(), fill="#ccc")

        draw.text((20, 20), data.get("title", "Sleep"), fill="#ffffff")
        draw.text((20, 45), data.get("value_label", ""), fill="#aaa")
        return img

    def _art_scribble_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Scribble style driven by real min/avg/max data."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#faf8f5")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        # Use data-driven palette for scribble colors
        pal = self._DATA_PALETTES.get(palette, self._DATA_PALETTES["thermal"])
        scribble_colors = [
            pal["low"], pal["mid"], pal["high"],
            (80, 140, 100), (120, 60, 140), (200, 100, 60),
        ]

        # Call the scribble function with real data
        img = style_scribble(
            size, size,
            data_min=data["data_min"],
            data_avg=data["data_avg"],
            data_max=data["data_max"],
            title=data["title"],
            value_label=data["value_label"],
        )
        return img

    def _art_kandinsky_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Kandinsky where shape density and color are driven by data values."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#0d1117")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        img = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        values = data["color_values"]
        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1
        n = len(values)
        # More data points → more shapes
        n_shapes = max(20, n * 3)

        for i in range(n_shapes):
            # Map shape to a data point for color
            idx = i % n
            t = (values[idx] - v_min) / v_range
            color = self._lerp_data_color(t, palette)
            cx, cy = random.randint(20, size - 20), random.randint(20, size - 20)
            size_shape = random.randint(15, 100)
            shape = random.choice(['circle', 'triangle', 'square', 'line'])
            if shape == 'circle':
                draw.ellipse([cx - size_shape, cy - size_shape,
                              cx + size_shape, cy + size_shape],
                             fill=color if random.random() > 0.4 else None,
                             outline=(30, 30, 30), width=3)
            elif shape == 'triangle':
                pts = [(cx, cy - size_shape), (cx - size_shape, cy + size_shape),
                       (cx + size_shape, cy + size_shape)]
                draw.polygon(pts, fill=color, outline=(30, 30, 30), width=3)
            elif shape == 'square':
                draw.rectangle([cx - size_shape, cy - size_shape,
                                cx + size_shape, cy + size_shape],
                               fill=color, outline=(30, 30, 30), width=3)
            else:
                ex, ey = random.randint(20, size - 20), random.randint(20, size - 20)
                draw.line([cx, cy, ex, ey], fill=color, width=random.randint(2, 8))

        draw.text((20, 20), data["title"], fill=(30, 30, 30))
        draw.text((20, 45), data["value_label"], fill=(80, 80, 80))
        return img

    def _art_mondrian_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Mondrian where cell colors are mapped from data values."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#fff")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        img = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        values = data["color_values"]
        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1
        line_w = 4

        # Use data values to color cells
        cols, rows = random.randint(3, 5), random.randint(3, 5)
        n_cells = cols * rows
        # Assign data values to cells (cycle if fewer data points)
        cell_values = [values[i % len(values)] for i in range(n_cells)]

        def subdivide(x, y, rw, rh, depth, cell_idx):
            if depth == 0 or (rw < 60 and rh < 60):
                t = (cell_values[cell_idx % len(cell_values)] - v_min) / v_range
                color = self._lerp_data_color(t, palette)
                # Mix with white for Mondrian feel
                if random.random() < 0.3:
                    color = (255, 255, 255)
                draw.rectangle([x, y, x + rw, y + rh], fill=color)
                return
            if random.random() < 0.5 and rw > 80:
                split = random.randint(rw // 4, rw * 3 // 4)
                subdivide(x, y, split, rh, depth - 1, cell_idx)
                subdivide(x + split, y, rw - split, rh, depth - 1, cell_idx + 1)
                draw.line([x + split, y, x + split, y + rh], fill=(0, 0, 0), width=line_w)
            elif rh > 80:
                split = random.randint(rh // 4, rh * 3 // 4)
                subdivide(x, y, rw, split, depth - 1, cell_idx)
                subdivide(x, y + split, rw, rh - split, depth - 1, cell_idx + 1)
                draw.line([x, y + split, x + rw, y + split], fill=(0, 0, 0), width=line_w)
            else:
                t = (cell_values[cell_idx % len(cell_values)] - v_min) / v_range
                color = self._lerp_data_color(t, palette)
                draw.rectangle([x, y, x + rw, y + rh], fill=color)

        subdivide(0, 0, size, size, random.randint(4, 6), 0)
        draw.rectangle([0, 0, size - 1, size - 1], outline=(0, 0, 0), width=line_w)
        draw.text((20, 20), data["title"], fill=(30, 30, 30))
        return img

    def _art_rothko_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Rothko where band colors are mapped from data quantiles."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#0d1117")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        img = Image.new("RGB", (size, size), (40, 30, 30))
        draw = ImageDraw.Draw(img)
        values = data["color_values"]
        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1

        num_bands = max(2, min(5, data["n_points"]))
        band_h = size // num_bands
        gap = random.randint(10, 30)

        for i in range(num_bands):
            # Map band index to a data quantile
            t = i / max(num_bands - 1, 1)
            color = self._lerp_data_color(t, palette)
            y0 = i * band_h + gap // 2
            y1 = (i + 1) * band_h - gap // 2
            x0 = random.randint(0, 30)
            x1 = size - random.randint(0, 30)
            for offset in range(5, 0, -1):
                c = tuple(max(0, c - offset * 3) for c in color)
                draw.rectangle([x0 + offset, y0 + offset, x1 - offset, y1 - offset], fill=c)
            draw.rectangle([x0, y0, x1, y1], fill=color)

        img = img.filter(ImageFilter.GaussianBlur(radius=random.randint(8, 20)))
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), data["title"], fill="#ffffff")
        draw.text((20, 45), data["value_label"], fill="#ccc")
        return img

    def _art_pollock_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Pollock where stroke density and color follow data values."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#0d1117")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        img = Image.new("RGB", (size, size), (20, 18, 15))
        draw = ImageDraw.Draw(img)
        values = data["color_values"]
        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1
        n = len(values)

        # Number of strokes proportional to data
        n_strokes = max(15, n * 2)
        for _ in range(n_strokes):
            idx = random.randint(0, n - 1)
            t = (values[idx] - v_min) / v_range
            color = self._lerp_data_color(t, palette)
            width = random.randint(1, 6)
            x, y = random.randint(0, size), random.randint(0, size)
            points = [(x, y)]
            for _ in range(random.randint(5, 20)):
                x += random.randint(-80, 80)
                y += random.randint(-80, 80)
                x = max(0, min(size - 1, x))
                y = max(0, min(size - 1, y))
                points.append((x, y))
            draw.line(points, fill=color, width=width, joint='curve')

        # Dots
        for _ in range(random.randint(200, 500)):
            idx = random.randint(0, n - 1)
            t = (values[idx] - v_min) / v_range
            color = self._lerp_data_color(t, palette)
            x, y = random.randint(0, size), random.randint(0, size)
            r = random.randint(1, 5)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)

        draw.text((20, 20), data["title"], fill="#ffffff")
        draw.text((20, 45), data["value_label"], fill="#aaa")
        return img

    def _art_bauhaus_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Bauhaus where grid cell colors are mapped from data."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#f5f0e6")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        img = Image.new("RGB", (size, size), (245, 240, 230))
        draw = ImageDraw.Draw(img)
        values = data["color_values"]
        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1
        n = len(values)

        cols, rows = random.randint(3, 5), random.randint(3, 5)
        cell_w, cell_h = size // cols, size // rows

        for row in range(rows):
            for col in range(cols):
                if random.random() < 0.2:
                    continue
                idx = (row * cols + col) % n
                t = (values[idx] - v_min) / v_range
                color = self._lerp_data_color(t, palette)
                cx = col * cell_w + cell_w // 2
                cy = row * cell_h + cell_h // 2
                sz = min(cell_w, cell_h) // 2 - 5
                shape = random.choice(['circle', 'half_circle', 'rect', 'arc'])
                if shape == 'circle':
                    draw.ellipse([cx - sz, cy - sz, cx + sz, cy + sz],
                                 fill=color, outline=(30, 30, 30), width=2)
                elif shape == 'half_circle':
                    draw.pieslice([cx - sz, cy - sz, cx + sz, cy + sz],
                                  180, 360, fill=color, outline=(30, 30, 30), width=2)
                elif shape == 'rect':
                    draw.rectangle([cx - sz, cy - sz, cx + sz, cy + sz],
                                   fill=color, outline=(30, 30, 30), width=2)
                else:
                    draw.arc([cx - sz, cy - sz, cx + sz, cy + sz],
                             0, random.choice([90, 180, 270]),
                             fill=(30, 30, 30), width=4)

        draw.text((20, 20), data["title"], fill=(30, 30, 30))
        draw.text((20, 45), data["value_label"], fill=(80, 80, 80))
        return img

    def _art_wild_mix_data(self, data: dict, size: int, palette: str) -> Image.Image:
        """Wild Mix where all sub-styles use data-driven colors."""
        if not data or data["n_points"] == 0:
            img = Image.new("RGB", (size, size), "#0d1117")
            ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")
            return img

        img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        values = data["color_values"]
        v_min, v_max = min(values), max(values)
        v_range = v_max - v_min or 1
        n = len(values)

        # Background: Rothko bands from data
        if random.random() > 0.5:
            num_bands = random.randint(2, 4)
            band_h = size // num_bands
            for i in range(num_bands):
                t = i / max(num_bands - 1, 1)
                color = self._lerp_data_color(t, palette)
                y0 = i * band_h + random.randint(5, 20)
                y1 = (i + 1) * band_h - random.randint(5, 20)
                draw.rectangle([0, y0, size, y1], fill=color + (255,))
            img = img.filter(ImageFilter.GaussianBlur(radius=random.randint(10, 25)))
            draw = ImageDraw.Draw(img)
        else:
            bg = self._lerp_data_color(0.1, palette)
            draw.rectangle([0, 0, size, size], fill=bg + (255,))

        # Mondrian grid with data colors
        if random.random() > 0.4:
            cols, rows = random.randint(2, 4), random.randint(2, 4)
            cell_w, cell_h = size // cols, size // rows
            for row in range(rows):
                for col in range(cols):
                    if random.random() < 0.5:
                        idx = (row * cols + col) % n
                        t = (values[idx] - v_min) / v_range
                        color = self._lerp_data_color(t, palette)
                        x0, y0 = col * cell_w, row * cell_h
                        x1, y1 = x0 + cell_w, y0 + cell_h
                        draw.rectangle([x0, y0, x1, y1],
                                       fill=color + (180,),
                                       outline=(0, 0, 0, 200), width=3)

        # Kandinsky shapes with data colors
        if random.random() > 0.2:
            for _ in range(random.randint(8, 20)):
                idx = random.randint(0, n - 1)
                t = (values[idx] - v_min) / v_range
                color = self._lerp_data_color(t, palette)
                cx, cy = random.randint(20, size - 20), random.randint(20, size - 20)
                sz = random.randint(15, 80)
                shape = random.choice(['circle', 'triangle', 'square', 'line'])
                if shape == 'circle':
                    draw.ellipse([cx - sz, cy - sz, cx + sz, cy + sz],
                                 fill=color + (255,) if random.random() > 0.3 else None,
                                 outline=(30, 30, 30, 255), width=3)
                elif shape == 'triangle':
                    pts = [(cx, cy - sz), (cx - sz, cy + sz), (cx + sz, cy + sz)]
                    draw.polygon(pts, fill=color + (255,), outline=(30, 30, 30, 255), width=3)
                elif shape == 'square':
                    draw.rectangle([cx - sz, cy - sz, cx + sz, cy + sz],
                                   fill=color + (255,), outline=(30, 30, 30, 255), width=3)
                else:
                    ex, ey = random.randint(20, size - 20), random.randint(20, size - 20)
                    draw.line([cx, cy, ex, ey], fill=color + (255,), width=random.randint(2, 8))

        # Pollock drips
        if random.random() > 0.2:
            for _ in range(random.randint(8, 20)):
                idx = random.randint(0, n - 1)
                t = (values[idx] - v_min) / v_range
                color = self._lerp_data_color(t, palette)
                width = random.randint(1, 5)
                x, y = random.randint(0, size), random.randint(0, size)
                points = [(x, y)]
                for _ in range(random.randint(4, 15)):
                    x += random.randint(-60, 60)
                    y += random.randint(-60, 60)
                    x = max(0, min(size - 1, x))
                    y = max(0, min(size - 1, y))
                    points.append((x, y))
                draw.line(points, fill=color + (255,), width=width, joint='curve')
            for _ in range(random.randint(100, 300)):
                idx = random.randint(0, n - 1)
                t = (values[idx] - v_min) / v_range
                color = self._lerp_data_color(t, palette)
                x, y = random.randint(0, size), random.randint(0, size)
                r = random.randint(1, 4)
                draw.ellipse([x - r, y - r, x + r, y + r], fill=color + (255,))

        if random.random() > 0.7:
            img = img.filter(ImageFilter.GaussianBlur(radius=random.randint(1, 4)))

        img = img.convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), data["title"], fill="#ffffff")
        draw.text((20, 45), data["value_label"], fill="#ccc")
        return img

    # ── Art generate / save ──

    def _on_generate_art(self):
        if not PIL_AVAILABLE:
            messagebox.showerror("Error", "Pillow not installed.\nRun: pip install Pillow")
            return

        style = self.art_style_var.get()

        # Validate data availability
        data_key = self.art_data_var.get()
        if style in ("daily_mosaic", "scribble", "kandinsky", "mondrian",
                     "rothko", "pollock", "bauhaus", "wild_mix"):
            dates = self._get_dates_for_data_source(data_key)
            if not dates:
                self.art_info_var.set(f"❌ No {self._ART_DATA_SOURCES.get(data_key, data_key)} data.")
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

        self.art_info_var.set("Generating…")
        self.root.update()

        def work():
            try:
                img = self._generate_art_image()
                self._art_image = img
                preview_size = 500
                ratio = preview_size / max(img.size[0], img.size[1])
                preview = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)),
                                     Image.LANCZOS)
                self._art_photo = ImageTk.PhotoImage(preview)

                def _show():
                    self.art_preview_label.config(image=self._art_photo, text="")
                    self.art_info_var.set(
                        f"✅ {img.size[0]}×{img.size[1]} px  |  {style}  |  "
                        f"{self.art_data_var.get()}  |  {self.art_period_select_var.get()}")
                self.root.after(0, _show)

            except Exception as e:
                err_msg = str(e)
                def _err():
                    self.art_info_var.set(f"❌ {err_msg}")
                    self.art_preview_label.config(
                        image=None, text=f"Error: {err_msg}", bg="#2a1a1a", fg="#e74c3c")
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

    def _build_overview_tab(self):
        frame = ttk.Frame(self.tab_overview)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        Button(top, text="📊 Generate", bootstyle="primary",
               command=self._on_overview_generate).pack(side=tk.LEFT, padx=5)
        Button(top, text="💾 Export ODS", bootstyle="success",
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

        tree.tag_configure(tag, background=color, foreground="#ffffff")
        tree.tag_configure(tag_m, background=color, foreground="#ffffff",
                           font=("TkDefaultFont", 10, "bold"))

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
                for col in ("sys", "dia", "pulse"):
                    if col in sub.columns:
                        s = sub[col].dropna()
                        if s.empty:
                            vals += ["", "", ""]
                        else:
                            vals += [f"{s.min():.0f}", f"{s.max():.0f}", f"{s.mean():.0f}"]
                    else:
                        vals += ["", "", ""]
                return vals

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
                 ["Total", "Deep", "Light", "REM", "Awake"],
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
                  "Dia Min", "Dia Max", "Dia Avg",
                  "Pulse Min", "Pulse Max", "Pulse Avg"],
                 compute_bp),
            ]

            for sec_name, df_key, headers, compute_fn in sections:
                if df_key not in dfs:
                    continue
                df = dfs[df_key].copy()
                df["week"] = df["date"].apply(week_label)
                df["month"] = df["date"].apply(month_label)

                tree = self._make_section_tree(self.overview_notebook, sec_name, headers)
                tag = f"sec_{sec_name.replace(' ', '_')}"

                months = sorted(df["month"].unique())
                for mo in months:
                    sub = df[df["month"] == mo]
                    vals = compute_fn(sub)
                    row_vals = [f"📅 {mo}"] + vals + [""] * (9 - len(vals))
                    tree.insert("", tk.END, values=row_vals[:10], tags=(tag,))
                    self._overview_export_data.append(
                        {"section": sec_name, "type": "monthly",
                         "period": mo, "values": vals, "headers": headers})

                tree.insert("", tk.END, values=[""] * 10, tags=())

                weeks = sorted(df["week"].unique())
                for wk in weeks:
                    sub = df[df["week"] == wk]
                    vals = compute_fn(sub)
                    row_vals = [wk] + vals + [""] * (9 - len(vals))
                    tree.insert("", tk.END, values=row_vals[:10], tags=(tag,))
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

                    for entry in monthly_entries:
                        row = TableRow()
                        pcell = TableCell(stylename=style_bold)
                        pcell.addElement(P(text=f"📅 {entry['period']}", stylename=text_bold))
                        row.addElement(pcell)
                        for i in range(len(headers)):
                            v = entry["values"][i] if i < len(entry["values"]) else ""
                            cell = TableCell(stylename=style_bold)
                            cell.addElement(P(text=v, stylename=text_bold))
                            row.addElement(cell)
                        table.addElement(row)

                    for _ in range(3):
                        table.addElement(TableRow())

                    for entry in weekly_entries:
                        row = TableRow()
                        pcell = TableCell(stylename=style_normal)
                        pcell.addElement(P(text=entry["period"]))
                        row.addElement(pcell)
                        for i in range(len(headers)):
                            v = entry["values"][i] if i < len(entry["values"]) else ""
                            cell = TableCell(stylename=style_normal)
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
