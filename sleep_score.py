"""Age-adjusted sleep quality scoring."""  
  
  
def compute_sleep_quality_score(deep_min, light_min, rem_min, awake_min,  
                                 sleep_time_min, age: int) -> float | None:  
    if sleep_time_min is None or sleep_time_min <= 0:  
        return None  
  
    total = float(sleep_time_min)  
    deep_pct = (deep_min or 0) / total  
    rem_pct  = (rem_min or 0) / total  
    awake_pct = (awake_min or 0) / total  
  
    if age < 30:  
        deep_lo, deep_hi = 0.20, 0.25  
        rem_lo, rem_hi   = 0.22, 0.25  
        awake_cap        = 0.10  
    elif age < 40:  
        deep_lo, deep_hi = 0.16, 0.20  
        rem_lo, rem_hi   = 0.20, 0.24  
        awake_cap        = 0.10  
    elif age < 50:  
        deep_lo, deep_hi = 0.12, 0.18  
        rem_lo, rem_hi   = 0.19, 0.23  
        awake_cap        = 0.12  
    elif age < 60:  
        deep_lo, deep_hi = 0.10, 0.15  
        rem_lo, rem_hi   = 0.18, 0.22  
        awake_cap        = 0.12  
    else:  
        deep_lo, deep_hi = 0.05, 0.12  
        rem_lo, rem_hi   = 0.17, 0.21  
        awake_cap        = 0.15  
  
    if total < 420:  
        dur_score = max(0.0, 30.0 * (total - 300) / 120)  
    elif total <= 540:  
        dur_score = 30.0  
    elif total <= 660:  
        dur_score = max(0.0, 30.0 * (660 - total) / 120)  
    else:  
        dur_score = 0.0  
  
    if deep_lo <= deep_pct <= deep_hi:  
        deep_score = 25.0  
    else:  
        dist = (deep_lo - deep_pct) if deep_pct < deep_lo else (deep_pct - deep_hi)  
        span = max(deep_hi - deep_lo, 0.01)  
        deep_score = max(0.0, 25.0 - (dist / span) * 25.0)  
  
    if rem_lo <= rem_pct <= rem_hi:  
        rem_score = 25.0  
    else:  
        dist = (rem_lo - rem_pct) if rem_pct < rem_lo else (rem_pct - rem_hi)  
        span = max(rem_hi - rem_lo, 0.01)  
        rem_score = max(0.0, 25.0 - (dist / span) * 25.0)  
  
    if awake_pct <= awake_cap * 0.5:  
        awake_score = 20.0  
    elif awake_pct <= awake_cap:  
        awake_score = 20.0 * (1.0 - (awake_pct - awake_cap * 0.5) / (awake_cap * 0.5))  
    else:  
        awake_score = max(0.0, 20.0 - (awake_pct - awake_cap) / awake_cap * 20.0)  
  
    return round(min(100.0, max(0.0, dur_score + deep_score + rem_score + awake_score)), 1)
