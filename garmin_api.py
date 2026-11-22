"""Garmin Connect login + data fetching."""  
import time  
from datetime import date, timedelta  
from pathlib import Path  
  
import pandas as pd  
  
from database import (db_upsert_daily_stats, db_upsert_heart_rate, db_upsert_stress,  
                      db_upsert_sleep, db_upsert_respiration, db_upsert_activities,  
                      db_upsert_body_battery, db_upsert_body_battery_values)  
  
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

def garmin_login(email: str, password: str,  
                 api_token: str = "data/garmin_tokens",  
                 status_cb=None, mfa_callback=None) -> tuple:  
    api_token = str(Path(api_token).expanduser())
  
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
            g.login(api_token)  
            return g, None  
        except Exception:  
            pass  
        _msg("  → Cached tokens failed, trying fresh login…")  
        try:  
            g = Garmin(email, password, prompt_mfa=_get_mfa)  
            g.login(api_token)  
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
    _msg(f" → Updating {start.isoformat()} → {end.isoformat()}")  
    acts = _safe_call(client.get_activities_by_date, start.isoformat(), end.isoformat())  
    daily = fetch_daily_data(client, start, end, status_cb)  
    bb = _safe_call(client.get_body_battery, start.isoformat(), end.isoformat())  
    daily["body_battery"] = bb if isinstance(bb, list) else []  
    daily["activities"] = acts if acts else []  
    return daily  
  
def save_to_db(merged: dict):  
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
        bb_values_rows = []  
        for e in merged["body_battery"]:  
            d = e.get("date") or e.get("calendarDate")  
            if not d:  
                continue  
            arr = e.get("bodyBatteryValuesArray")  
            levels = []  
            if arr:  
                for pair in arr:  
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2:  
                        ts_ms, level = pair[0], pair[1]  
                        if ts_ms and level is not None:  
                            ts_str = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_localize(None).isoformat()  
                            bb_values_rows.append({"timestamp": ts_str, "value": float(level)})  
                            levels.append(float(level))  
            if levels:  
                bb_rows.append({  
                    "date": d,  
                    "value": levels[-1],  
                    "highest": max(levels),  
                    "lowest": min(levels),  
                })  
            elif e.get("charged") is not None:  
                bb_rows.append({"date": d, "value": e.get("charged"),  
                                "highest": e.get("charged"), "lowest": 0})  
        if bb_rows:  
            db_upsert_body_battery(bb_rows)  
        if bb_values_rows:  
            db_upsert_body_battery_values(bb_values_rows)
