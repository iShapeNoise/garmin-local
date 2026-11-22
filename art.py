"""Generative art engine — fully self-contained.  
app.py calls generate_art(); nothing here touches GarminViewerApp."""  
import math  
import random  
import colorsys  
import numpy as np  
import pandas as pd  
  
try:  
    from PIL import Image, ImageDraw, ImageFilter  
    PIL_AVAILABLE = True  
except ImportError:  
    PIL_AVAILABLE = False  
  
  
# ─── public entry point ───────────────────────────────────────────────  
  
def generate_art(style: str, data: dict, size: int, bg: tuple,  
                 bp_df=None, sleep_entries=None) -> "Image.Image":  
    """Dispatch to a style renderer. `bg` is an (r,g,b) tuple from the caller."""  
    # print(f"[art] style={style} bg={bg}") 
    renderers = {  
        "daily_mosaic":  _art_daily_mosaic,  
        "sleep_heatmap": _art_sleep_heatmap,  
        "bp_wave":       _art_bp_wave,  
        "scribble":      _art_scribble,  
        "kandinsky":     _art_kandinsky,  
        "mondrian":      _art_mondrian,  
        "rothko":        _art_rothko,  
        "pollock":       _art_pollock,  
        "bauhaus":       _art_bauhaus,  
        "wild_mix":      _art_wild_mix,  
    }  
    fn = renderers.get(style)  
    if fn is None:  
        raise ValueError(f"Unknown style: {style}")  
    if style == "bp_wave":  
        img = fn(bp_df, size, bg)  
    elif style == "sleep_heatmap":  
        img = fn(data, size, bg, sleep_entries or [])  
    else:  
        img = fn(data, size, bg)  
    # print(f"[art] corner={img.getpixel((0, 0))}")   # temporary  
    return img 
  

# ─── helpers ──────────────────────────────────────────────────────────  
  
_DARK_THEMES = {"darkly", "superhero", "solar", "cyborg"}  
  
def _art_bg(theme: str = "darkly") -> tuple:  
    rnd = random.Random()  
    p_dark = 0.7 if theme in _DARK_THEMES else 0.3  
    if rnd.random() < p_dark:  
        return tuple(rnd.randint(15, 70) for _ in range(3))  
    return tuple(rnd.randint(170, 240) for _ in range(3))
  
def _fg(bg: tuple) -> tuple:  
    """Readable text/outline color on the given background."""  
    return (30, 30, 30) if sum(bg) > 380 else (230, 230, 230)  
  
def _no_data(size: int, bg: tuple) -> "Image.Image":  
    img = Image.new("RGB", (size, size), bg)  
    ImageDraw.Draw(img).text((size // 2, size // 2), "No data", fill="#888")  
    return img  
  
def _art_seed(data: dict) -> tuple:  
    """Fresh unseeded RNG + random channel→attribute map.  
    Every call re-rolls which data series drives which visual property."""  
    rnd = random.Random()  
    n = data.get("n_points", 1) if data else 1  
    spread = [max(0.0, data["data_max"][i] - data["data_min"][i])  
              for i in range(n)] if data else [0.5] * n  
    channels = [data["data_min"], data["data_avg"], data["data_max"], spread] if data else [[0.5] * n] * 4  
    attrs = ["color", "size", "position", "thickness", "roughness", "rotation"]  
    rnd.shuffle(attrs)  
    ch = {attr: channels[i % len(channels)] for i, attr in enumerate(attrs)}  
    return rnd, ch  
  
def _lerp(t: float, rnd: "random.Random") -> tuple:  
    """Random hue biased by data value t (0..1) → RGB tuple."""  
    h = (t * 0.7 + rnd.uniform(-0.08, 0.08)) % 1.0  
    s = 0.55 + 0.4 * rnd.random()  
    v = 0.55 + 0.45 * t  
    r, g, b = colorsys.hsv_to_rgb(h, s, v)  
    return (int(r * 255), int(g * 255), int(b * 255))
  
def _rand_color(rnd, t=0.5):  
    """Data value t (0..1) → hue; random saturation/value → new palette each run."""  
    h = (t * 0.75 + rnd.uniform(-0.08, 0.08)) % 1.0  
    r, g, b = colorsys.hsv_to_rgb(h, rnd.uniform(0.55, 0.95), rnd.uniform(0.55, 0.95))  
    return (int(r * 255), int(g * 255), int(b * 255))  
  
  
# ─── renderers ────────────────────────────────────────────────────────  
  
def _art_daily_mosaic(data, size, bg):  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    values = data["color_values"]  
    n = len(values)  
    cols = int(np.ceil(np.sqrt(n)))  
    rows = int(np.ceil(n / cols))  
    cell = size // max(cols, rows)  
    img_size = max(cols * cell, rows * cell)  
    img = Image.new("RGB", (img_size, img_size), bg)  
    draw = ImageDraw.Draw(img)  
    v_min, v_max = min(values), max(values)  
    v_range = v_max - v_min or 1  
    for i, val in enumerate(values):  
        col, row = i % cols, i // cols  
        t = (val - v_min) / v_range  
        inset = 1 + int(ch["size"][i % n] * cell * 0.3)   # data → cell inset  
        draw.rectangle([col * cell + inset, row * cell + inset,  
                        (col + 1) * cell - inset, (row + 1) * cell - inset],  
                       fill=_rand_color(rnd, t))  
    draw.text((20, 20), data["title"], fill=_fg(bg))  
    draw.text((20, 45), data["value_label"], fill=_fg(bg))  
    return img  
  
  
def _art_sleep_heatmap(data, size, bg, sleep_entries=None):  
    """Sleep stacked bars. sleep_entries: list of dicts with  
    deep_sleep_min / light_sleep_min / rem_sleep_min / awake_min."""  
    entries = sleep_entries or []  
    if not entries:  
        return _no_data(size, bg)  
    rnd, _ = _art_seed(data or {"data_min": [0], "data_avg": [0], "data_max": [0]})  
    # random but harmonious stage colors each click  
    base_h = rnd.random()  
    stage_hues = {"deep": base_h, "light": (base_h + 0.15) % 1.0,  
                  "rem": (base_h + 0.45) % 1.0, "awake": (base_h + 0.75) % 1.0}  
    colors = {k: tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, 0.7, 0.85))  
              for k, h in stage_hues.items()}  
    n = len(entries)  
    bar_h = max(6, int(size * 0.7 / max(n, 1)))  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    fg = _fg(bg)  
    max_total = max(  
        (s.get("deep_sleep_min", 0) or 0) + (s.get("light_sleep_min", 0) or 0) +  
        (s.get("rem_sleep_min", 0) or 0) + (s.get("awake_min", 0) or 0)  
        for s in entries) or 1  
    y_start = int(size * 0.15)  
    for i, s in enumerate(entries):  
        y = y_start + i * bar_h  
        x = int(size * 0.15)  
        total_w = int(size * 0.75)  
        for key in ("deep", "light", "rem", "awake"):  
            mins = s.get("awake_min", 0) if key == "awake" else s.get(f"{key}_sleep_min", 0)  
            w = int(total_w * (mins or 0) / max_total)  
            if w > 0:  
                draw.rectangle([x, y, x + w, y + bar_h - 1], fill=colors[key])  
                x += w  
    ly = int(size * 0.08)  
    for j, (label, color) in enumerate(colors.items()):  
        lx = int(size * 0.15) + j * 140  
        draw.rectangle([lx, ly, lx + 16, ly + 16], fill=color)  
        draw.text((lx + 22, ly), label.capitalize(), fill=fg)  
    draw.text((20, 20), (data or {}).get("title", "Sleep"), fill=fg)  
    draw.text((20, 45), (data or {}).get("value_label", ""), fill=fg)  
    return img
  
def _art_bp_wave(bp_df, size, bg):  
    """BP waveform: sys/dia lines + filled band. Colors random per call."""  
    if bp_df is None or bp_df.empty:  
        return _no_data(size, bg)  
    rnd = random.Random()  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    fg = _fg(bg)  
    df = bp_df.sort_values("datetime").reset_index(drop=True)  
    n = len(df)  
    sys_v = df["sys"].fillna(df["sys"].mean()).tolist()  
    dia_v = df["dia"].fillna(df["dia"].mean()).tolist()  
    all_v = sys_v + dia_v  
    v_min, v_max = min(all_v), max(all_v)  
    v_range = v_max - v_min or 1  
    h_sys = rnd.random()  
    c_sys = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h_sys, 0.75, 0.95))  
    c_dia = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((h_sys + 0.5) % 1.0, 0.75, 0.9))  
  
    def pt(i, v):  
        return (int(size * 0.08 + size * 0.84 * i / max(n - 1, 1)),  
                int(size * 0.9 - (v - v_min) / v_range * size * 0.75))  
  
    band = [pt(i, sys_v[i]) for i in range(n)] + [pt(i, dia_v[i]) for i in reversed(range(n))]  
    draw.polygon(band, fill=tuple(int((c_sys[k] + c_dia[k]) / 2 * 0.4) for k in range(3)))  
    draw.line([pt(i, sys_v[i]) for i in range(n)], fill=c_sys, width=4, joint='curve')  
    draw.line([pt(i, dia_v[i]) for i in range(n)], fill=c_dia, width=4, joint='curve')  
    draw.text((20, 20), "Blood Pressure", fill=fg)  
    draw.text((20, 45), f"{n} readings  |  sys {np.mean(sys_v):.0f}  dia {np.mean(dia_v):.0f}", fill=fg)  
    return img
  
def _art_scribble(data, size, bg):  
    """Doodle renderer: wandering lines, loops, spirals, zigzags and spots.  
    Data graph (min/avg/max envelope) drawn faintly underneath."""  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    n = data["n_points"]  
    fg = _fg(bg)  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
  
    # faint data graph underneath — keeps the data relation visible  
    for series, alpha in ((data["data_min"], 60), (data["data_max"], 60),  
                          (data["data_avg"], 110)):  
        pts = []  
        for i in range(n):  
            x = int(50 + (size - 100) * i / max(n - 1, 1))  
            y = int(size - 100 - series[i] * (size - 200))  
            pts.append((x, y))  
        if len(pts) > 1:  
            faint = tuple(int(fg[k] * alpha / 255 + bg[k] * (1 - alpha / 255))  
                          for k in range(3))  
            draw.line(pts, fill=faint, width=2)  
  
    # doodles on top  
    n_doodles = max(10, n * 2)  
    for i in range(n_doodles):  
        idx = i % n  
        color = _lerp(ch["color"][idx], rnd)  
        lw = 1 + int(ch["thickness"][idx] * 5)  
        cx = int(ch["position"][idx] * (size - 80)) + 40  
        cy = int(ch["position"][(idx + 1) % n] * (size - 80)) + 40  
        r = 10 + int(ch["size"][idx] * 60)  
        kind = rnd.choice(['line', 'loop', 'spiral', 'zigzag', 'spot'])  
        if kind == 'line':  
            pts = [(cx, cy)]  
            for _ in range(rnd.randint(3, 8)):  
                cx += rnd.randint(-r, r)  
                cy += rnd.randint(-r, r)  
                pts.append((max(0, min(size - 1, cx)),  
                            max(0, min(size - 1, cy))))  
            draw.line(pts, fill=color, width=lw, joint='curve')  
        elif kind == 'loop':  
            start_a = math.degrees(ch["rotation"][idx] * 2 * math.pi) % 360  
            draw.arc([cx - r, cy - r, cx + r, cy + r],  
                     start_a, start_a + rnd.randint(90, 300),  
                     fill=color, width=lw)  
        elif kind == 'spiral':  
            pts = []  
            turns = rnd.randint(2, 5)  
            for k in range(turns * 20):  
                a = k * 0.3 + ch["rotation"][idx] * 2 * math.pi  
                rr = r * k / (turns * 20)  
                pts.append((cx + int(rr * math.cos(a)),  
                            cy + int(rr * math.sin(a))))  
            if len(pts) > 1:  
                draw.line(pts, fill=color, width=lw)  
        elif kind == 'zigzag':  
            pts = [(cx - r, cy)]  
            for k in range(1, 6):  
                pts.append((cx - r + k * r // 3,  
                            cy + (r // 2 if k % 2 else -r // 2)))  
            draw.line(pts, fill=color, width=lw, joint='curve')  
        else:  
            draw.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2],  
                         fill=color)  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img
  
def _art_kandinsky(data, size, bg):  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    n = data["n_points"]  
    fg = _fg(bg)  
    for i in range(max(20, n * 3)):  
        idx = i % n  
        color = _rand_color(rnd, ch["color"][idx])  
        sz = 15 + int(ch["size"][idx] * 85)  
        cx = int(sz + ch["position"][idx] * (size - 2 * sz))  
        cy = rnd.randint(sz, size - sz)  
        rot = ch["rotation"][idx] * 2 * math.pi  
        lw = 1 + int(ch["thickness"][idx] * 6)  
        shape = rnd.choice(["circle", "triangle", "square", "line", "arc"])  
        if shape == "circle":  
            draw.ellipse([cx - sz, cy - sz, cx + sz, cy + sz],  
                         fill=color if rnd.random() > 0.4 else None,  
                         outline=fg, width=lw)  
        elif shape == "triangle":  
            pts = [(cx + sz * math.cos(rot + a), cy + sz * math.sin(rot + a))  
                   for a in (-math.pi / 2, math.pi * 5 / 6, math.pi / 6)]  
            draw.polygon(pts, fill=color, outline=fg, width=lw)  
        elif shape == "square":  
            draw.rectangle([cx - sz, cy - sz, cx + sz, cy + sz],  
                           fill=color, outline=fg, width=lw)  
        elif shape == "arc":  
            start = math.degrees(rot) % 360  
            draw.arc([cx - sz, cy - sz, cx + sz, cy + sz],  
                     start, start + rnd.randint(90, 300), fill=color, width=lw)  
        else:  
            ex = cx + int(sz * 2 * math.cos(rot))  
            ey = cy + int(sz * 2 * math.sin(rot))  
            draw.line([cx, cy, ex, ey], fill=color, width=lw)  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img  
    
def _art_mondrian(data, size, bg):  
    """Mondrian grid: recursive subdivision; split depth ∝ roughness channel,  
    line thickness ∝ thickness channel, cell colors ∝ color channel."""  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    n = data["n_points"]  
    fg = _fg(bg)  
    line_w = 2 + int(max(ch["thickness"]) * 8)  
    dark = sum(bg) < 380  
    line_col = (230, 230, 230) if dark else (0, 0, 0)  
    empty_col = bg  
  
    def subdivide(x, y, rw, rh, depth, cell_idx):  
        if depth == 0 or (rw < 60 and rh < 60):  
            t = ch["color"][cell_idx % n]  
            color = _lerp(t, rnd)  
            if rnd.random() < 0.3:  
                color = empty_col  
            draw.rectangle([x, y, x + rw, y + rh], fill=color)  
            return  
        # roughness channel biases toward more splits  
        split_bias = ch["roughness"][cell_idx % n]  
        if rnd.random() < 0.4 + split_bias * 0.3 and rw > 80:  
            split = rnd.randint(rw // 4, rw * 3 // 4)  
            subdivide(x, y, split, rh, depth - 1, cell_idx)  
            subdivide(x + split, y, rw - split, rh, depth - 1, cell_idx + 1)  
            draw.line([x + split, y, x + split, y + rh], fill=line_col, width=line_w)  
        elif rh > 80:  
            split = rnd.randint(rh // 4, rh * 3 // 4)  
            subdivide(x, y, rw, split, depth - 1, cell_idx)  
            subdivide(x, y + split, rw, rh - split, depth - 1, cell_idx + 1)  
            draw.line([x, y + split, x + rw, y + split], fill=line_col, width=line_w)  
        else:  
            t = ch["color"][cell_idx % n]  
            draw.rectangle([x, y, x + rw, y + rh], fill=_lerp(t, rnd))  
  
    subdivide(0, 0, size, size, rnd.randint(4, 6), 0)
    draw.rectangle([0, 0, size - 1, size - 1], outline=line_col, width=line_w)  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img
    
def _art_rothko(data, size, bg):  
    """Rothko bands: height ∝ size channel, blur ∝ spread (max-min)."""  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    n = data["n_points"]  
    fg = _fg(bg)  
  
    num_bands = max(2, min(6, n))  
    # band heights proportional to the size channel  
    weights = [ch["size"][i % n] + 0.15 for i in range(num_bands)]  
    total = sum(weights)  
    heights = [int(size * w / total) for w in weights]  
    heights[-1] += size - sum(heights)          # absorb rounding  
  
    y = 0  
    for i in range(num_bands):  
        idx = i % n  
        color = _lerp(ch["color"][idx], rnd)  
        spread = data["data_max"][idx] - data["data_min"][idx]  
        gap = 6 + int(spread * 40)              # volatile → wider gaps  
        x0 = int(ch["position"][idx] * 40)  
        x1 = size - int(ch["position"][(idx + 1) % n] * 40)  
        y0, y1 = y + gap // 2, y + heights[i] - gap // 2  
        # soft edge: stacked darker rects behind the band  
        for off in range(5, 0, -1):  
            c = tuple(max(0, cc - off * 3) for cc in color)  
            draw.rectangle([x0 + off, y0 + off, x1 - off, y1 - off], fill=c)  
        draw.rectangle([x0, y0, x1, y1], fill=color)  
        y += heights[i]  
  
    mean_spread = sum(data["data_max"][i] - data["data_min"][i]  
                      for i in range(n)) / n  
    img = img.filter(ImageFilter.GaussianBlur(radius=4 + int(mean_spread * 24)))  
    draw = ImageDraw.Draw(img)  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img
    
def _art_pollock(data, size, bg):  
    """Pollock drips bent by z = z² + c; c = roughness channel (or rnd 0..1).  
    Escape-time iteration count steers each step's direction."""  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    n = data["n_points"]  
    fg = _fg(bg)  
  
    def bend(x, y, c):  
        """One z²+c iteration → (dx, dy) nudge from escape speed."""  
        z = complex((x / size) * 2 - 1, (y / size) * 2 - 1)  
        it = 0  
        while abs(z) <= 2 and it < 12:  
            z = z * z + c  
            it += 1  
        ang = it * 0.9 + c.real * 6.28  
        step = 10 + it * 6  
        return int(step * math.cos(ang)), int(step * math.sin(ang))  
  
    n_strokes = max(15, n * 2)  
    for _ in range(n_strokes):  
        idx = rnd.randrange(n)  
        color = _lerp(ch["color"][idx], rnd)  
        width = 1 + int(ch["thickness"][idx] * 6)  
        # c from data (roughness channel) or random 0..1  
        rv = ch["roughness"][idx] if rnd.random() < 0.7 else rnd.random()  
        c = complex(-0.8 + rv * 1.2, -0.4 + rv * 0.8)  
        x = int(ch["position"][idx] * size)  
        y = int(ch["position"][(idx * 7 + 3) % n] * size)  
        pts = [(x, y)]  
        for _ in range(rnd.randint(6, 22)):  
            dx, dy = bend(x, y, c)  
            x = max(0, min(size - 1, x + dx))  
            y = max(0, min(size - 1, y + dy))  
            pts.append((x, y))  
        if len(pts) > 1:  
            draw.line(pts, fill=color, width=width, joint="curve")  
  
    # splatter — density ∝ spread  
    n_splat = 150 + int(max(data["data_max"][i] - data["data_min"][i]  
                            for i in range(n)) * 400)  
    for _ in range(n_splat):  
        idx = rnd.randrange(n)  
        color = _lerp(ch["color"][idx], rnd)  
        x, y = rnd.randrange(size), rnd.randrange(size)  
        r = 1 + int(ch["size"][idx] * 5)  
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)  
  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img
    
def _art_bauhaus(data, size, bg):  
    """Bauhaus grid: circles, half-circles, rects, arcs.  
    Cell size ∝ size channel, skip probability ∝ spread."""  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    img = Image.new("RGB", (size, size), bg)  
    draw = ImageDraw.Draw(img)  
    n = data["n_points"]  
    fg = _fg(bg)  
    spread = [mx - mn for mn, mx in zip(data["data_min"], data["data_max"])]  
    cols, rows = rnd.randint(3, 5), rnd.randint(3, 5)  
    cell_w, cell_h = size // cols, size // rows  
    for row in range(rows):  
        for col in range(cols):  
            idx = (row * cols + col) % n  
            if rnd.random() < 0.1 + spread[idx] * 0.3:  
                continue  
            color = _lerp(ch["color"][idx], rnd)  
            cx = col * cell_w + cell_w // 2  
            cy = row * cell_h + cell_h // 2  
            sz = int((min(cell_w, cell_h) // 2 - 5) * (0.4 + ch["size"][idx] * 0.6))  
            sz = max(4, sz)  
            lw = 1 + int(ch["thickness"][idx] * 4)  
            shape = rnd.choice(['circle', 'half_circle', 'rect', 'arc'])  
            if shape == 'circle':  
                draw.ellipse([cx - sz, cy - sz, cx + sz, cy + sz],  
                             fill=color, outline=fg, width=lw)  
            elif shape == 'half_circle':  
                start_a = int(ch["rotation"][idx] * 360)  
                draw.pieslice([cx - sz, cy - sz, cx + sz, cy + sz],  
                              start_a, start_a + 180, fill=color, outline=fg, width=lw)  
            elif shape == 'rect':  
                draw.rectangle([cx - sz, cy - sz, cx + sz, cy + sz],  
                               fill=color, outline=fg, width=lw)  
            else:  
                draw.arc([cx - sz, cy - sz, cx + sz, cy + sz],  
                         0, rnd.choice([90, 180, 270]), fill=fg, width=lw + 2)  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img
    
def _art_wild_mix(data, size, bg):  
    """Layered collage: Rothko bands + Mondrian grid + Kandinsky shapes +  
    Pollock strokes + optional blur. Layers are translucent so bg shows  
    through; channel map re-rolls which data drives what per click."""  
    if not data or data["n_points"] == 0:  
        return _no_data(size, bg)  
    rnd, ch = _art_seed(data)  
    n = data["n_points"]  
    spread = [max(0.0, data["data_max"][i] - data["data_min"][i])  
              for i in range(n)]  
    fg = _fg(bg)  
    img = Image.new("RGBA", (size, size), bg + (255,))  
    draw = ImageDraw.Draw(img)  
  
    # Layer 1: translucent bands or tint — bg always shows through  
    if rnd.random() > 0.5:  
        num_bands = rnd.randint(2, 4)  
        weights = [ch["size"][i % n] + 0.2 for i in range(num_bands)]  
        total = sum(weights)  
        y = 0  
        for i in range(num_bands):  
            bh = int(size * weights[i] / total)  
            color = _lerp(ch["color"][i % n], rnd)  
            draw.rectangle([0, y, size, y + bh], fill=color + (90,))  
            y += bh  
        img = img.filter(ImageFilter.GaussianBlur(radius=rnd.randint(10, 25)))  
        draw = ImageDraw.Draw(img)  
    else:  
        tint = _lerp(ch["color"][0], rnd)  
        draw.rectangle([0, 0, size, size], fill=tint + (60,))  
  
    # Layer 2: Mondrian-ish grid cells  
    if rnd.random() > 0.4:  
        cols, rows = rnd.randint(2, 4), rnd.randint(2, 4)  
        cell_w, cell_h = size // cols, size // rows  
        for row in range(rows):  
            for col in range(cols):  
                if rnd.random() < 0.5:  
                    idx = (row * cols + col) % n  
                    color = _lerp(ch["color"][idx], rnd)  
                    x0, y0 = col * cell_w, row * cell_h  
                    draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h],  
                                   fill=color + (140,),  
                                   outline=fg + (200,), width=3)  
  
    # Layer 3: Kandinsky-ish shapes — size/rotation from data  
    if rnd.random() > 0.2:  
        for i in range(rnd.randint(8, 20)):  
            idx = rnd.randint(0, n - 1)  
            color = _lerp(ch["color"][idx], rnd)  
            sz = 15 + int(ch["size"][idx] * 80)  
            cx = int(ch["position"][idx] * (size - 40)) + 20  
            cy = int(ch["position"][(idx + 1) % n] * (size - 40)) + 20  
            rot = ch["rotation"][idx] * 2 * math.pi  
            lw = 1 + int(ch["thickness"][idx] * 6)  
            shape = rnd.choice(['circle', 'triangle', 'square', 'line'])  
            if shape == 'circle':  
                draw.ellipse([cx - sz, cy - sz, cx + sz, cy + sz],  
                             fill=color + (255,) if rnd.random() > 0.3 else None,  
                             outline=fg + (255,), width=lw)  
            elif shape == 'triangle':  
                pts = []  
                for k in range(3):  
                    a = rot + k * 2 * math.pi / 3  
                    pts.append((cx + int(sz * math.cos(a)),  
                                cy + int(sz * math.sin(a))))  
                draw.polygon(pts, fill=color + (255,), outline=fg + (255,))  
            elif shape == 'square':  
                draw.rectangle([cx - sz, cy - sz, cx + sz, cy + sz],  
                               fill=color + (255,), outline=fg + (255,), width=lw)  
            else:  
                ex = cx + int(sz * 2 * math.cos(rot))  
                ey = cy + int(sz * 2 * math.sin(rot))  
                draw.line([cx, cy, ex, ey], fill=color + (255,), width=lw)  
  
    # Layer 4: Pollock-ish strokes — fractal-bent paths  
    if rnd.random() > 0.2:  
        for _ in range(rnd.randint(8, 20)):  
            idx = rnd.randint(0, n - 1)  
            color = _lerp(ch["color"][idx], rnd)  
            width = 1 + int(ch["thickness"][idx] * 4)  
            c = complex(ch["roughness"][idx] * 0.7 - 0.35,  
                        ch["roughness"][(idx + 1) % n] * 0.7 - 0.35)  
            z = 0j  
            x = rnd.randint(0, size)  
            y = rnd.randint(0, size)  
            points = [(x, y)]  
            for _ in range(rnd.randint(4, 15)):  
                z = z * z + c  
                bend = math.atan2(z.imag, z.real) if abs(z) > 1e-9 else 0.0  
                step = 20 + int(spread[idx] * 60)  
                x += int(step * math.cos(bend)) + rnd.randint(-30, 30)  
                y += int(step * math.sin(bend)) + rnd.randint(-30, 30)  
                x = max(0, min(size - 1, x))  
                y = max(0, min(size - 1, y))  
                points.append((x, y))  
            draw.line(points, fill=color + (255,), width=width, joint='curve')  
        for _ in range(rnd.randint(100, 300)):  
            idx = rnd.randint(0, n - 1)  
            color = _lerp(ch["color"][idx], rnd)  
            x, y = rnd.randint(0, size), rnd.randint(0, size)  
            r = 1 + int(spread[idx] * 4)  
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color + (255,))  
  
    if rnd.random() > 0.7:  
        img = img.filter(ImageFilter.GaussianBlur(radius=rnd.randint(1, 4)))  
  
    img = img.convert("RGB")  
    draw = ImageDraw.Draw(img)  
    draw.text((20, 20), data["title"], fill=fg)  
    draw.text((20, 45), data["value_label"], fill=fg)  
    return img
    
