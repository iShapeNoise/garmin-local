"""DuckDB persistence layer. Creates data/garmin_data.db if missing."""  
from datetime import date  
  
import duckdb  
import pandas as pd  
  
from config import DATA_DIR, DB_PATH, TOKEN_DIR
  
  
def _get_db():  
    DATA_DIR.mkdir(exist_ok=True)  
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH)) 
  
  
def init_db() -> bool:  
    """Create DB + tables if missing. Returns True if a NEW db was created."""  
    is_new = not DB_PATH.exists()  
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
            value REAL,  
            highest REAL,  
            lowest REAL  
        );  
        CREATE TABLE IF NOT EXISTS body_battery_values (  
            timestamp TEXT,  
            value REAL  
        );  
        CREATE TABLE IF NOT EXISTS blood_pressure (  
            datum TEXT,  
            time TEXT,  
            sys REAL,  
            dia REAL
        );  
        CREATE TABLE IF NOT EXISTS meta (  
            key TEXT PRIMARY KEY,  
            value TEXT  
        );  
    """)  
    conn.close()  
    return is_new  
  
  
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
            INSERT INTO body_battery (date, value, highest, lowest) VALUES (?, ?, ?, ?)  
            ON CONFLICT(date) DO UPDATE SET  
                value=excluded.value, highest=excluded.highest, lowest=excluded.lowest  
        """, [r["date"], r["value"], r.get("highest"), r.get("lowest")])  
    conn.close()  
  
  
def db_upsert_body_battery_values(rows: list):  
    if not rows:  
        return  
    conn = _get_db()  
    dates = set()  
    for r in rows:  
        dates.add(r["timestamp"][:10])  
    for d in dates:  
        conn.execute("DELETE FROM body_battery_values WHERE timestamp LIKE ?", [d + "%"])  
    for r in rows:  
        conn.execute("INSERT INTO body_battery_values (timestamp, value) VALUES (?, ?)",  
                     [r["timestamp"], r["value"]])  
    conn.close()  


def db_save_bp(rows: list):  
    conn = _get_db()  
    conn.execute("DELETE FROM blood_pressure")  
    for r in rows:  
        conn.execute("INSERT INTO blood_pressure (datum, time, sys, dia) VALUES (?,?,?,?)",  
                     [r["datum"], r["time"], r["sys"], r["dia"]])  
    conn.close()  
  
  
def db_get_bp():  
    conn = _get_db()  
    df = conn.execute("SELECT datum, time, sys, dia FROM blood_pressure ORDER BY datum, time").fetchdf()  
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
    for col in ("sys", "dia"):  
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
        "body_battery_values": [],  
    }  
  
    df_act = conn.execute("SELECT * FROM activities ORDER BY startTimeLocal").fetchdf()  
    if not df_act.empty:  
        data["activities"] = df_act.to_dict("records")  
  
    df_bb = conn.execute("SELECT * FROM body_battery ORDER BY date").fetchdf()  
    if not df_bb.empty:  
        data["body_battery"] = df_bb.to_dict("records")  
  
    df_bbv = conn.execute("SELECT timestamp, value FROM body_battery_values ORDER BY timestamp").fetchdf()  
    if not df_bbv.empty:  
        data["body_battery_values"] = df_bbv.to_dict("records")  
  
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
