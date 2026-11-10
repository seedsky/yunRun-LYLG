"""轨迹合成（deform 模式）：纯几何二维形变 + 上传任务表构造。

本模块只做一件事：把一条已有轨迹（tasklist / GeoJSON / 裸坐标列表）**形变**成
一条视觉尺度接近历史演示包的新轨迹，并补齐上传所需的逐点里程/时间/步数。

算法（与历史演示包同源，参数见 DEMO_PROFILE）：
    1. 按弧长把来源重采样成 spacing_m（默认 5.5m）等间距折线；
    2. 沿法线做 OU（Ornstein-Uhlenbeck）低频游走 —— 跨道/走廊摆动；
    3. 叠加 x/y 两维相关漂移 —— 定位噪声尺度的大范围位移；
    4. 叠加稀疏、指数衰减的偶发大偏移（约 1.2 次/km，sigma 14m）；
    5. 用固定 seed 保证可复现；可选把几何长度校准到来源的 +3%。

与旧 V4 引擎的区别：V4 是"沿底图法线的单值侧移 offset(i)"，幅度被 max_offset_m
（≤20m）锁死在底图两侧的条带内；deform 直接对二维坐标做形变场，因此能复现演示
包里那种跨越内场、彼此缠绕的密集轨迹。V4 已于 2026-10 移除，旧 cfg 键会得到明确
的迁移报错（见 LEGACY_V4_KEYS）。

纯本地计算：不读取账号、不联网、不判断道路可通行性。围栏（allowed_polygon_geojson）
是可选的一致性校验，不是可跑性证明——但任务要求踩点时（cards / cards_from）围栏
变成必填，因为打卡点与围栏要一起校验。

打卡点（cards）：形变之后把离卡片最近的顶点拉回卡片（邻域半余弦跟随），再按
≤20 m 口径生成 manageList（marked=Y + index）与 recodeDislikes；命中数不足
cards_min 时换种子重抽（最多 MAX_ATTEMPTS 次）。这是让 deform 生成的轨迹能直接
满足“踩满打卡点”要求的唯一必要步骤。
"""
import bisect
import json
import math
import random
import secrets
from pathlib import Path

EARTH_M = 111320.0

# 形变参数：默认值 / 允许下界 / 允许上界。默认值即历史演示包使用的尺度，
# 改动默认值会改变"与演示包一致"的复现基准（回归测试 test_deform_matches_demo_scale）。
PROFILE_LIMITS = {
    "spacing_m": (5.5, 1.0, 50.0),            # 输出等弧长重采样间距（演示包 5.4~5.7m）
    "lane_sigma_m": (2.8, 0.0, 20.0),         # 法向 OU 游走强度
    "lane_tau_m": (280.0, 10.0, 5000.0),      # 法向 OU 相关长度
    "max_lane_m": (5.0, 0.0, 20.0),           # 法向游走硬限幅
    "drift_sigma_m": (4.2, 0.0, 20.0),        # 二维定位漂移强度
    "drift_tau_m": (200.0, 10.0, 5000.0),     # 二维漂移相关长度
    "jump_sigma_m": (14.0, 0.0, 60.0),        # 偶发大偏移幅值（标准差）
    "jump_rate_per_km": (1.2, 0.0, 20.0),     # 偶发大偏移频次
    "jump_decay": (0.90, 0.50, 1.0),          # 偶发偏移逐点衰减
    "length_gain_pct": (0.03, 0.0, 0.5),      # 几何长度校准（相对来源）
    "start_trim_m": (0.0, 0.0, 20000.0),      # 头/尾按弧长裁剪
    "end_trim_m": (0.0, 0.0, 20000.0),
}
DEMO_PROFILE = {k: v[0] for k, v in PROFILE_LIMITS.items()}

# 里程区间不满足时的重抽上限：连续这么多次都没抽中就直接失败（用户指定 5 次）。
MAX_ATTEMPTS = 5

# 来源步频的合理区间（步/分）：越界视为来源字段异常，退回 cfg 名义步频。
SANE_CADENCE_SPM = (110.0, 220.0)

# 已移除的 V4 配置键：命中即给出迁移指引，而不是含糊的"未知键"。
LEGACY_V4_KEYS = {
    "mode", "base_geojson", "base_task", "start_trim", "end_trim",
    "lane_change_indices", "lane_change_choices_m", "lane_transition_points",
    "detour_enabled", "detour_index", "detour_rejoin_offset", "detour_exit_m",
    "detour_forward_m", "detour_step_m", "max_offset_m",
    "telemetry_task", "telemetry_variation",
}


def distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def number(cfg, key, default, low, high, integer=False):
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} 必须是有限数字")
    if integer and value != int(value):
        raise ValueError(f"{key} 必须为整数")
    if not low <= value <= high:
        raise ValueError(f"{key} 必须在 {low}～{high} 之间" + ("且为整数" if integer else ""))
    return int(value) if integer else float(value)


def pair_range(cfg, key, low, high, unit=""):
    """解析 [最小值, 最大值] 区间：留空=None；上下限相等即定值；越界报错。"""
    raw = cfg.get(key)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{key} 必须写成 [最小值, 最大值]")
    a = number({f"{key}_lo": raw[0]}, f"{key}_lo", 0, low, high)
    b = number({f"{key}_hi": raw[1]}, f"{key}_hi", 0, low, high)
    if a > b:
        raise ValueError(f"{key} 的最小值 {a}{unit} 不能大于最大值 {b}{unit}")
    return a, b


def profile_of(cfg):
    """合并并校验 deform_profile（未知键与越界值都报错）。"""
    over = cfg.get("deform_profile") or {}
    if not isinstance(over, dict):
        raise ValueError("deform_profile 必须是对象")
    unknown = set(over) - set(PROFILE_LIMITS)
    if unknown:
        raise ValueError(f"未知的形变参数：{sorted(unknown)}")
    merged = dict(DEMO_PROFILE)
    for key, value in over.items():
        low, high = PROFILE_LIMITS[key][1], PROFILE_LIMITS[key][2]
        merged[key] = number({"v": value}, "v", DEMO_PROFILE[key], low, high)
    return merged


def extract_points(obj):
    """从 tasklist / GeoJSON / 裸坐标列表抽出 [(lon,lat), ...]。

    容忍三种输入形态，这是"任选底图 json"的实现基础：
      * tasklist：``data.pointsList[*].point = "lon,lat"``（也接受顶层 pointsList）
      * GeoJSON：LineString / Feature / 单条线的 FeatureCollection
      * 裸坐标列表：``[[lon, lat], ...]``
    """
    if isinstance(obj, list):
        raw = obj
    elif not isinstance(obj, dict):
        raise ValueError("输入必须是 JSON 对象或坐标列表")
    elif isinstance(obj.get("data"), dict) and isinstance(obj["data"].get("pointsList"), list):
        raw = []
        for row in obj["data"]["pointsList"]:
            text = row.get("point") if isinstance(row, dict) else None
            if not isinstance(text, str) or text.count(",") != 1:
                raise ValueError("tasklist pointsList 中存在非法 point")
            raw.append([float(x) for x in text.split(",")])
    elif isinstance(obj.get("pointsList"), list):
        raw = [[float(x) for x in row["point"].split(",")] for row in obj["pointsList"]]
    else:
        geo = obj
        if obj.get("type") == "FeatureCollection":
            features = obj.get("features", [])
            if not isinstance(features, list) or len(features) != 1:
                raise ValueError("GeoJSON FeatureCollection 必须只有一条线")
            geo = features[0].get("geometry", {}) if isinstance(features[0], dict) else {}
        elif obj.get("type") == "Feature":
            geo = obj.get("geometry", {})
        if geo.get("type") != "LineString":
            raise ValueError("无法识别输入：需要 tasklist、GeoJSON LineString 或坐标列表")
        raw = geo.get("coordinates", [])
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise ValueError("坐标必须为 [经度, 纬度]")
        lon, lat = float(p[0]), float(p[1])
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -85 <= lat <= 85):
            raise ValueError("存在非法经纬度")
        if not out or distance(out[-1], (lon, lat)) > 0.01:
            out.append((lon, lat))
    if len(out) < 8:
        raise ValueError("有效轨迹点少于 8 个")
    # 局部投影形变只适用于校园尺度；跨度过大时投影畸变会失真，直接拒绝。
    if any(max(p[k] for p in out) - min(p[k] for p in out) > 0.5 for k in (0, 1)):
        raise ValueError("仅支持局部校园几何，经纬度跨度不能超过 0.5 度")
    return out


def load_points(path):
    return extract_points(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def load_source(path):
    """读来源文件，返回 (点列, 节奏指标)。

    节奏指标只在来源是 tasklist（带 runMileage/runTime/runStep）时可得：
      * length_m    —— 总里程（米）
      * pace_min_km —— 总体配速（分/公里）
      * cadence_spm —— 总体步频（步/分）
    GeoJSON / 裸坐标列表只有几何，配速与步频为 None（此时用 cfg 的名义值做基准）。
    """
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    points = extract_points(obj)
    metrics = {"length_m": None, "pace_min_km": None, "cadence_spm": None}
    rows = None
    if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
        rows = obj["data"].get("pointsList")
    elif isinstance(obj, dict) and isinstance(obj.get("pointsList"), list):
        rows = obj.get("pointsList")
    if isinstance(rows, list) and len(rows) >= 2:
        try:
            miles = [float(r["runMileage"]) for r in rows]
            times = [float(r["runTime"]) for r in rows]
            steps = [float(r["runStep"]) for r in rows]
        except (KeyError, TypeError, ValueError):
            miles = times = steps = None
        if miles:
            span_m, span_s = miles[-1]-miles[0], times[-1]-times[0]
            if span_m > 0:
                metrics["length_m"] = span_m
                if span_s > 0:
                    pace = span_s/60.0/(span_m/1000.0)
                    if 2.0 <= pace <= 30.0:          # 与 cfg 名义配速同一合理区间
                        metrics["pace_min_km"] = pace
                    step_span = steps[-1]-steps[0]
                    cadence = step_span*60.0/span_s if step_span > 0 else 0.0
                    # 合理性闸门：实测 fch 表的 runStep 约 489 spm（单位/来源异常），
                    # 跑步步频合理区间取 110~220；越界一律不采信、退回 cfg 名义步频。
                    if SANE_CADENCE_SPM[0] <= cadence <= SANE_CADENCE_SPM[1]:
                        metrics["cadence_spm"] = cadence
    if metrics["length_m"] is None:
        metrics["length_m"] = sum(distance(a, b) for a, b in zip(points, points[1:]))
    return points, metrics


def local_projection(points):
    lat0 = sum(p[1] for p in points) / len(points)
    origin = points[0]
    sx = EARTH_M * math.cos(math.radians(lat0))
    sy = EARTH_M
    fwd = lambda p: ((p[0]-origin[0])*sx, (p[1]-origin[1])*sy)
    inv = lambda p: (p[0]/sx+origin[0], p[1]/sy+origin[1])
    return fwd, inv


def cumulative_xy(points):
    out = [0.0]
    for a, b in zip(points, points[1:]):
        out.append(out[-1]+math.dist(a, b))
    return out


def point_at(points, lengths, s):
    if s <= 0:
        return points[0]
    if s >= lengths[-1]:
        return points[-1]
    j = max(1, min(len(points)-1, bisect.bisect_left(lengths, s)))
    a, b = points[j-1], points[j]
    den = lengths[j]-lengths[j-1]
    f = 0.0 if den <= 1e-12 else (s-lengths[j-1])/den
    return (a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f)


def resample(points, spacing_m=5.5, start_trim_m=0.0, end_trim_m=0.0):
    fwd, inv = local_projection(points)
    xy = [fwd(p) for p in points]
    lengths = cumulative_xy(xy)
    lo = max(0.0, float(start_trim_m))
    hi = max(lo, lengths[-1]-max(0.0, float(end_trim_m)))
    if hi-lo < max(20.0, spacing_m*4):
        raise ValueError("裁剪后轨迹过短")
    n = max(8, int(math.floor((hi-lo)/spacing_m))+1)
    ss = [lo+(hi-lo)*i/(n-1) for i in range(n)]
    return [inv(point_at(xy, lengths, s)) for s in ss]


def tangent_normal(xy, i):
    a, b = xy[max(0, i-2)], xy[min(len(xy)-1, i+2)]
    dx, dy = b[0]-a[0], b[1]-a[1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        a, b = xy[max(0, i-1)], xy[min(len(xy)-1, i+1)]
        dx, dy = b[0]-a[0], b[1]-a[1]
        n = math.hypot(dx, dy)
    if n < 1e-9:
        return (1.0, 0.0), (0.0, 1.0)
    tx, ty = dx/n, dy/n
    return (tx, ty), (-ty, tx)


def ou_distance(rng, s_values, tau_m, sigma_m):
    """按空间距离推进的 OU 过程（不是按点序，换采样密度不影响形状）。"""
    if sigma_m <= 0:
        return [0.0]*len(s_values)
    x = 0.0
    out = []
    prev = s_values[0]
    for s in s_values:
        ds = max(0.0, s-prev)
        prev = s
        phi = math.exp(-ds/max(tau_m, 1e-6))
        step = sigma_m*math.sqrt(max(0.0, 1.0-phi*phi))
        x = phi*x+rng.gauss(0.0, step)
        out.append(x)
    return out


def deform_points(points, seed=31337, profile=None):
    """核心形变：[(lon,lat), ...] -> [(lon,lat), ...]（实现与演示包一致）。"""
    cfg = dict(DEMO_PROFILE)
    if profile:
        cfg.update(profile)
    base = resample(points, cfg["spacing_m"], cfg["start_trim_m"], cfg["end_trim_m"])
    fwd, inv = local_projection(base)
    xy = [fwd(p) for p in base]
    ss = cumulative_xy(xy)
    rng = random.Random(int(seed) ^ 0x8FFF)
    lane = ou_distance(rng, ss, cfg["lane_tau_m"], cfg["lane_sigma_m"])
    dx = ou_distance(rng, ss, cfg["drift_tau_m"], cfg["drift_sigma_m"])
    dy = ou_distance(rng, ss, cfg["drift_tau_m"], cfg["drift_sigma_m"])
    jump_p_per_m = max(0.0, cfg["jump_rate_per_km"])/1000.0
    jx = jy = 0.0
    components = []
    prev_s = ss[0]
    for i, (p, s) in enumerate(zip(xy, ss)):
        _, nrm = tangent_normal(xy, i)
        lane_i = max(-cfg["max_lane_m"], min(cfg["max_lane_m"], lane[i]))
        ds = max(0.0, s-prev_s)
        prev_s = s
        # 泊松近似：按空间长度触发，与采样点数量解耦。
        if cfg["jump_sigma_m"] > 0 and rng.random() < 1.0-math.exp(-jump_p_per_m*ds):
            ang = rng.uniform(0, 2*math.pi)
            mag = abs(rng.gauss(0, cfg["jump_sigma_m"]))
            jx += mag*math.cos(ang)
            jy += mag*math.sin(ang)
        jx *= cfg["jump_decay"]
        jy *= cfg["jump_decay"]
        components.append((nrm[0]*lane_i+dx[i]+jx, nrm[1]*lane_i+dy[i]+jy))

    def candidate(scale):
        return [(p[0]+u*scale, p[1]+v*scale) for p, (u, v) in zip(xy, components)]

    # 可选的视觉长度校准：只缩放同一组随机位移、不重新抽样，因此同 seed 可复现。
    gain = max(0.0, float(cfg.get("length_gain_pct", 0.0)))
    scale = 1.0
    if gain > 0 and len(xy) > 2:
        source_xy = [fwd(p) for p in points]
        original_len = sum(math.dist(a, b) for a, b in zip(source_xy, source_xy[1:]))
        target_len = original_len*(1.0+gain)
        lo, hi = 0.0, 3.0
        for _ in range(24):
            mid = (lo+hi)/2
            test = candidate(mid)
            length = sum(math.dist(a, b) for a, b in zip(test, test[1:]))
            if length < target_len:
                lo = mid
            else:
                hi = mid
        scale = (lo+hi)/2
    out = []
    for q in candidate(scale):
        lon, lat = inv(q)
        out.append((round(lon, 8), round(lat, 8)))
    return out


def deform_geometry(source_points, *, seed=31337, profile=None):
    """公开的纯几何 API：来源点列 -> 形变点列（不涉及上传字段）。

    source_points 可以是 [(lon,lat), ...]、[[lon,lat], ...]、tasklist dict 或
    GeoJSON dict；profile 键值会先按 PROFILE_LIMITS 校验。
    """
    points = extract_points(source_points if isinstance(source_points, (list, dict)) else list(source_points))
    merged = profile_of({"deform_profile": profile} if profile else {})
    return deform_points(points, seed=int(seed), profile=merged)


def cumulative(points):
    result = [0.0]
    for a, b in zip(points, points[1:]):
        result.append(result[-1]+distance(a, b))
    return result


def client_speed(meters, seconds):
    # APK SportRunMapActivity:4140-4154：字段名 speed，实际为 min/km。
    return format(min(900, max(1, seconds/60/(meters/1000))), '.2f') if meters > 0 and seconds > 0 else '0.0'


# ---------------------------------------------------------------- 真实感配速曲线
PACING_DEFAULTS = {"slow_windows": (2, 3), "slow_gain": (1.35, 1.95),
                   "start_gain": 0.18, "finish_gain": 0.08}


def pace_profile(n, rng, base_pace, low=3.0, high=10.0, cfg=None, amp=1.0):
    """每个点的瞬时配速（min/km）。均值会被后续重定时拉回 base_pace。

    形状 = 三层随机正弦（平滑、不规律）
         + 起步 8% 线性放慢（热身）
         + 2–3 段三角包络的减速窗（路口/人多/喘口气）
         + 末尾 3% 略快（收尾冲刺）
    单点夹在 [low, high]：学校判的是平均配速，但单点太离谱也不好看。

    amp 是"起伏强度"：0.6 平缓、1.0 正常、1.5 明显（面板上就是那个下拉框）。
    """
    if n <= 2:
        return [base_pace]*n
    conf = dict(PACING_DEFAULTS, **(cfg or {}))
    amp = max(0.0, min(3.0, float(amp or 1.0)))
    xs = [i/(n-1) for i in range(n)]
    waves = [(rng.uniform(2.5, 5.0), rng.uniform(0, 2*math.pi), rng.uniform(0.05, 0.10)*amp),
             (rng.uniform(7.0, 11.0), rng.uniform(0, 2*math.pi), rng.uniform(0.04, 0.08)*amp),
             (rng.uniform(15.0, 22.0), rng.uniform(0, 2*math.pi), rng.uniform(0.02, 0.05)*amp)]
    out = []
    for x in xs:
        f = 1.0
        for k, ph, a in waves:
            f += a*math.sin(2*math.pi*k*x+ph)
        if x < 0.08:                                  # 起步慢
            f *= 1.0 + conf["start_gain"]*amp*(1-x/0.08)
        if x > 0.97:                                  # 收尾快
            f *= 1.0 - conf["finish_gain"]*amp
        out.append(f)
    lo_w, hi_w = conf["slow_windows"]
    g_lo, g_hi = conf["slow_gain"]
    for _ in range(rng.randint(int(lo_w), int(hi_w))):
        c, w, g = rng.uniform(0.12, 0.90), rng.uniform(0.010, 0.030), rng.uniform(g_lo, g_hi)
        for i, x in enumerate(xs):
            if abs(x-c) < w:
                out[i] *= 1.0 + (g-1.0)*amp*(1-abs(x-c)/w)
    return [min(high, max(low, base_pace*f)) for f in out]


def retime(mileage, paces, cadence, duration):
    """把"配速曲线 + 等距点"折算成逐点 (runTime, speed, runStep)，末点正好 duration。

    Δt_i = Δs_i/1000 × pace_i × 60；整条时间轴按 duration/Σ Δt 等比缩放，
    这样总时长不变（配速/步频平均值仍与任务表声明一致），只有点与点之间快慢不同。
    """
    n = len(mileage)
    seg = [(mileage[i]-mileage[i-1])/1000.0*paces[i]*60.0 for i in range(1, n)]
    raw = sum(seg)
    k = (duration/raw) if raw > 0 else 1.0
    ts = [0]
    acc = 0.0
    for s in seg:
        acc += s*k
        ts.append(int(round(acc)))
    for i in range(1, n):                       # 至少 1 秒，且必须单调
        if ts[i] <= ts[i-1]:
            ts[i] = ts[i-1]+1
    ts[-1] = duration
    if len(ts) > 1 and ts[-2] >= duration:
        ts[-2] = duration-1
    rows = []
    steps = 0.0
    for i in range(n):
        dt = ts[i]-ts[i-1] if i else 0
        ds = (mileage[i]-mileage[i-1]) if i else 0.0
        cad = cadence
        if i and ds > 0 and dt > 0:             # 跑快一点步频自然高一点（幅度收窄，别冲出申报区间）
            cad = max(120.0, min(210.0, cadence*(paces[0]/paces[i])**0.22))
            cad = max(cadence*0.94, min(cadence*1.08, cad))
            steps += dt*cad/60.0
        # 展示用配速直接取曲线值（夹到 2.5–12 分/公里），避免 Δt 只有 1 秒时
        # 被舍入放大成 50+ 的假尖峰；轨迹几何带来的 0 距离点保持 0.0
        if i and ds >= 0.2:
            pace_txt = format(min(12.0, max(2.5, paces[i]*k)), '.2f')
        else:
            pace_txt = '0.0'
        rows.append({"runTime": ts[i], "speed": pace_txt,
                     "runStep": int(round(steps)), "cadence_spm": round(cad, 1)})
    return rows


def position_at(route, lengths, meters):
    j = min(len(route)-1, max(1, bisect.bisect_left(lengths, meters)))
    a, b = route[j-1:j+1]
    f = (meters-lengths[j-1])/(lengths[j]-lengths[j-1])
    return tuple(round(a[k]+(b[k]-a[k])*f, 8) for k in (0, 1))


# ---------------------------------------------------------------- 围栏校验
def allowed_polygon(path):
    obj = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(obj, dict):
        raise ValueError('可跑区域必须是 GeoJSON 对象')
    if obj.get('type') == 'FeatureCollection':
        features = obj.get('features', [])
        if len(features) != 1:
            raise ValueError('可跑区域文件只能包含一个 Polygon')
        obj = features[0].get('geometry', {})
    elif obj.get('type') == 'Feature':
        obj = obj.get('geometry', {})
    if obj.get('type') != 'Polygon' or len(obj.get('coordinates', [])) != 1:
        raise ValueError('可跑区域必须是单环 Polygon；带洞或多区域暂不支持')
    raw_ring = obj['coordinates'][0]
    if not isinstance(raw_ring, list):
        raise ValueError('可跑区域坐标必须是列表')
    ring = []
    for p in raw_ring:
        if (not isinstance(p, list) or len(p) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                    not math.isfinite(v) for v in p)):
            raise ValueError('可跑区域坐标必须是有限经纬度')
        if not -180 <= p[0] <= 180 or not -85 <= p[1] <= 85:
            raise ValueError('可跑区域经纬度超出支持范围')
        if not ring or tuple(p) != ring[-1]:
            ring.append(tuple(p))
    if ring and ring[0] == ring[-1]:
        ring.pop()
    if len(ring) < 3:
        raise ValueError('可跑区域至少需要三个不同顶点')
    if len(ring) > 10000:
        raise ValueError('可跑区域顶点超过 10000 个')
    x0, y0 = ring[0]
    area = sum(((a[0]-x0)*(b[1]-y0)-(b[0]-x0)*(a[1]-y0))
               for a, b in zip(ring, ring[1:]+ring[:1]))
    if abs(area) < 1e-18:
        raise ValueError('可跑区域多边形面积为零')
    edges = list(zip(ring, ring[1:]+ring[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i+1, len(edges)):
            if j == i+1 or (i == 0 and j == len(edges)-1):
                continue
            c, d = edges[j]
            if segment_intersections(a, b, c, d):
                raise ValueError('可跑区域多边形自交')
    return ring


def cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def on_segment(p, a, b):
    ab = (b[0]-a[0], b[1]-a[1])
    ap = (p[0]-a[0], p[1]-a[1])
    scale = max(abs(ab[0]), abs(ab[1]), 1e-12)
    return (abs(cross(ab, ap)) <= 1e-13*scale and
            min(a[0], b[0])-1e-12 <= p[0] <= max(a[0], b[0])+1e-12 and
            min(a[1], b[1])-1e-12 <= p[1] <= max(a[1], b[1])+1e-12)


def segment_intersections(a, b, c, d):
    """返回 AB 与 CD 的交点在 AB 上的参数，含相切及共线端点。"""
    r = (b[0]-a[0], b[1]-a[1])
    s = (d[0]-c[0], d[1]-c[1])
    qa = (c[0]-a[0], c[1]-a[1])
    det = cross(r, s)
    if abs(det) < 1e-20:
        if abs(cross(qa, r)) > 1e-20:
            return []
        rr = r[0]*r[0]+r[1]*r[1]
        if rr == 0:
            return [0.0] if on_segment(a, c, d) else []
        return [max(0.0, min(1.0, ((p[0]-a[0])*r[0]+(p[1]-a[1])*r[1])/rr))
                for p in (c, d) if on_segment(p, a, b)] or [
                    0.0 if on_segment(a, c, d) else 1.0
                    for p in (a, b) if on_segment(p, c, d)]
    t = cross(qa, s)/det
    u = cross(qa, r)/det
    return [max(0.0, min(1.0, t))] if -1e-12 <= t <= 1+1e-12 and -1e-12 <= u <= 1+1e-12 else []


def inside(point, ring):
    x, y = point
    result = False
    for a, b in zip(ring, ring[1:]+ring[:1]):
        if on_segment(point, a, b):
            return True
        if (a[1] > y) != (b[1] > y):
            edge = a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x < edge:
                result = not result
    return result


def validate_polygon_route(route, ring):
    # 以线段与全部边界的交点切分，再检查每段中点；狭窄凹口不会被采样跳过。
    edges = list(zip(ring, ring[1:]+ring[:1]))
    for a, b in zip(route, route[1:]):
        cuts = [0.0, 1.0]
        for c, d in edges:
            cuts.extend(segment_intersections(a, b, c, d))
        cuts = sorted(set(cuts))
        for f in cuts[:1]+[(x+y)/2 for x, y in zip(cuts, cuts[1:])]+cuts[-1:]:
            p = (a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f)
            if not inside(p, ring):
                raise ValueError('生成路线有点或线段超出配置的可跑区域')


def validate_output_polygon(data, ring):
    if ring is not None:
        points = [tuple(map(float, p['point'].split(','))) for p in data['pointsList']]
        validate_polygon_route(points, ring)


# ---------------------------------------------------------------- 打卡点（manageList）
# App 的判定口径：轨迹点距离打卡点 ≤ 20 m 即“踩到”。finish 的 manageList 里
# marked=Y + index 指向这次真正上传的某个点，recodeDislikes 是踩到的数量。
CARD_TOL_M = 20.0
# 拉卡片时的影响半径（点数）：只有最近顶点精确落到卡片上，前后若干点按半余弦
# 权重一起弯，曲率不会集中在一点（避免“去了又回”的毛刺）。
CARD_PULL_RADIUS = 8


def parse_cards(raw):
    """把 cards 配置解析成 [[lon, lat], ...]。

    接受四种写法（都是任务里现成能拿到的形态，省得用户自己转格式）：
      * ``["112.41,34.61", ...]``           —— 单个 “lon,lat” 字符串（可含 | 分隔）
      * ``[[112.41, 34.61], ...]``          —— 已解析的坐标
      * ``"112.41,34.61|112.42,34.62"``     —— getHomeRunInfo 的 points 字段原样
      * 整份任务表 / 首页返回 ``{data: {manageList 或 points}}``
    """
    def _pair(text):
        a, b = [float(x) for x in str(text).strip().split(',')]
        return [a, b]

    if raw is None:
        return []
    if isinstance(raw, dict):
        data = raw.get('data') if isinstance(raw.get('data'), dict) else raw
        cards = []
        for item in data.get('manageList') or []:
            if isinstance(item, dict):
                if str(item.get('marked', 'Y')).strip().upper() == 'N' or not item.get('point'):
                    continue
                cards.append(_pair(item['point']))
            elif item:
                cards.append(_pair(item))
        if cards:
            return cards
        text = data.get('points')
        if isinstance(text, str) and text.strip():
            return [_pair(p) for p in text.split('|') if p.strip()]
        raise ValueError('cards 字典里既没有 manageList 也没有 points')
    if isinstance(raw, (list, tuple)) and len(raw) == 2 \
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw):
        return [[float(raw[0]), float(raw[1])]]
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    cards = []
    for item in items:
        if isinstance(item, str):
            cards.extend(_pair(p) for p in item.replace(';', '|').split('|') if p.strip())
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            cards.append([float(item[0]), float(item[1])])
        else:
            raise ValueError(f"cards 元素无法解析：{item!r}")
    return cards


def load_cards(cfg, base_dir):
    """cards_from 优先（文件路径），其次 cards（内联）；都没有就是不打点任务。"""
    ref = cfg.get('cards_from')
    if ref:
        if not isinstance(ref, str):
            raise ValueError('cards_from 必须是任务表 / JSON 文件路径')
        path = resolve_resource(base_dir, ref)
        if not path.exists():
            raise FileNotFoundError(f"cards_from 指向的文件不存在：{path}")
        return parse_cards(json.loads(path.read_text(encoding='utf-8-sig')))
    if cfg.get('cards') is not None:
        return parse_cards(cfg.get('cards'))
    return []


def pull_cards(route, cards, tol_m=6.0, radius=CARD_PULL_RADIUS):
    """形变后把路线拉回打卡点：最近的顶点落到卡片上，邻域平滑跟随。

    deform 是为了让轨迹“看起来不是原轨迹”，代价是原本贴着打卡点的顶点会被
    甩开 5~30 m。这里逐张卡片找最近顶点，把偏移按半余弦权重摊到前后 radius
    个点上：最近顶点精确重合，两侧逐渐恢复原形，地图上表现为自然的一段弯。
    """
    pts = [[float(p[0]), float(p[1])] for p in route]
    fwd, inv = local_projection([tuple(p) for p in pts])
    xy = [fwd(tuple(p)) for p in pts]
    for card in cards:
        c = fwd(tuple(card))
        i = min(range(len(xy)), key=lambda k: math.dist(xy[k], c))
        if math.dist(xy[i], c) <= tol_m:
            continue
        dx, dy = c[0]-xy[i][0], c[1]-xy[i][1]
        lo, hi = max(0, i-radius), min(len(xy)-1, i+radius)
        for k in range(lo, hi+1):
            w = 0.5*(1.0+math.cos(math.pi*abs(k-i)/max(1, radius)))
            xy[k] = (xy[k][0]+dx*w, xy[k][1]+dy*w)
    return [list(inv(p)) for p in xy]


def card_manage_list(sampled, cards, tol_m=CARD_TOL_M):
    """按最终采样点算 manageList：≤ tol 的记为 marked=Y，index 指向最近点。"""
    fwd, _ = local_projection([tuple(p) for p in sampled])
    xy = [fwd(tuple(p)) for p in sampled]
    listed = []
    for card in cards:
        c = fwd(tuple(card))
        j = min(range(len(xy)), key=lambda k: math.dist(xy[k], c))
        hit = math.dist(xy[j], c) <= tol_m
        listed.append({"point": f"{float(card[0]):.6f},{float(card[1]):.6f}",
                       "marked": "Y" if hit else "N", "index": str(j) if hit else ""})
    return listed


def extend_to(route, need_m):
    """几何不够长时从尾部原路折返补足（不重复踩卡片区段）。

    拉卡片是"弯过去"，会吃掉一点弦长；用 distance_m 精确指定时可能刚好差几米。
    折返发生在尾部，卡片多在中前段，既保证里程够，也不会让卡片被经过两次。
    """
    pts = [[float(p[0]), float(p[1])] for p in route]
    if len(pts) < 2:
        return pts
    total = cumulative(pts)[-1]
    if total >= need_m:
        return pts
    out = [list(p) for p in pts]
    walked = total
    seq = list(reversed(pts[:-1]))+[pts[-1]]     # 从尾部折返一趟再回来
    while walked < need_m:
        for p in seq:
            if walked >= need_m:
                break
            out.append(list(p))
            walked += distance(out[-2], out[-1])
        seq = list(reversed(seq))                 # 还不够就换向继续折返
    return out


# ---------------------------------------------------------------- 生成入口
def resolve_resource(base_dir, rel):
    """相对路径解析：先按 cfg 所在目录，再按项目根兜底。

    cfg 常放在 examples/routes/ 下（写 ../../tasks_lylg/… 在仓库里能跑），但整个
    目录被拷到别处后层数就变了。路径在 cfg 目录找不到时再试项目根，两边都命中。
    """
    path = Path(rel)
    if path.is_absolute():
        return path
    for cand in (Path(base_dir)/rel, Path(__file__).resolve().parent/rel):
        if cand.exists():
            return cand
    return Path(base_dir)/rel


def generate(config_path):
    """读 cfg 文件并生成上传任务表（CLI / 交互菜单入口）。"""
    path = Path(config_path).resolve()
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    return generate_cfg(cfg, path.parent)


def generate_cfg(cfg, base_dir):
    """按 cfg 生成上传任务表（CLI 与 web 预览共用同一入口）。

    三类随机偏移（默认各 ±10%，围绕**来源轨迹的实测值**）：
      * 配速 `pace_min_km`：来源总体配速（来源无节奏字段时用 cfg 的 pace_min_km）× (1+U(-o,o))
      * 步频 `cadence_spm`：来源总体步频（同上退回 cfg 的 cadence_spm）× (1+U(-o,o))
      * 总里程：来源总里程 × (1+U(-o,o))；若给了 `distance_m` 则精确用该值、不再抽签
    一致性规则（这是"三个参数互相算得出来"的关键）：
      * 采样是「等时间间隔 + 等里程增量」：n = round(target/(interval×速度))，
        Δm = target/n，Δt = sample_seconds，duration = n×interval → 每点配速
        interval/Δm 恒等于总体配速 duration/60/总里程；runMileage/runTime 严格等差，
        发送间隔与里程、配速三者自洽（舍入误差 < 1 个采样间隔）。
      * 步频同理：runStep = round(t×cadence/60)，末点步数×60/总时长 ≈ cadence。
      * 几何弦长（地图上量出来的折线长）比 target 短 1~3%（折返尖峰所致），
        在 metadata.geometry_chord_m 如实给出，不参与里程判定。
    里程区间：`min_distance_m`/`max_distance_m` 给定时，抽到的总里程必须落在区间内，
    否则换种子重抽；连续 MAX_ATTEMPTS(5) 次不满足直接报错，不静默放宽区间。
    """
    if not isinstance(cfg, dict):
        raise ValueError("路线配置必须为 JSON 对象")
    legacy = sorted(set(cfg) & LEGACY_V4_KEYS)
    if legacy:
        raise ValueError(f"V4 合成参数已移除：{legacy}；请改用 source_json + deform_profile"
                         "（见 README §3.4，旧 cfg 需按新键改写）")
    allowed = {"source_json", "coordinate_system", "distance_m", "pace_min_km", "cadence_spm",
               "sample_seconds", "seed", "allowed_polygon_geojson", "deform_profile",
               "min_distance_m", "max_distance_m", "pace_offset_pct", "cadence_offset_pct",
               "length_offset_pct", "cards", "cards_from", "cards_min", "cards_tol_m",
               "duration_range_s", "pace_range", "cadence_range", "pace_amp"}
    if cfg.keys()-allowed:
        raise ValueError(f"未知路线配置项：{sorted(cfg.keys()-allowed)}")
    if cfg.get("coordinate_system") not in ("GCJ-02", "WGS84"):
        raise ValueError("必须明确 coordinate_system 为 GCJ-02 或 WGS84；本工具不转换坐标系")
    if cfg.get('seed') == 'auto':
        cfg = dict(cfg, seed=secrets.randbits(63))
    cfg = dict(cfg, seed=number(cfg, 'seed', 31337, 0, 2**63-1, True))
    profile = profile_of(cfg)
    source = cfg.get('source_json')
    if not isinstance(source, str) or not source:
        raise ValueError('必须提供 source_json：tasklist / GeoJSON / 坐标列表任一格式')
    source_path = resolve_resource(base_dir, source)
    points, metrics = load_source(source_path)
    interval = number(cfg, "sample_seconds", 1, 1, 5, True)
    # 基准值：cfg 里显式写了的优先（用于按学校规则固定配速/步频），没写的才取来源
    # 轨迹的实测总体值（这就是"和原轨迹偏移 ±10%"的基准）。
    base_pace = number(cfg, "pace_min_km", metrics["pace_min_km"] or 6.0, 2, 30)
    base_cadence = number(cfg, "cadence_spm", metrics["cadence_spm"] or 160, 1, 350)
    pace_offset = number(cfg, "pace_offset_pct", 0.10, 0, 0.5)
    cadence_offset = number(cfg, "cadence_offset_pct", 0.10, 0, 0.5)
    length_offset = number(cfg, "length_offset_pct", 0.10, 0, 0.5)
    exact = cfg.get("distance_m")
    if exact is not None:
        exact = number(cfg, "distance_m", 0, 10, 50000)
    low = cfg.get("min_distance_m")
    high = cfg.get("max_distance_m")
    low = number(cfg, "min_distance_m", 0, 10, 50000) if low is not None else None
    high = number(cfg, "max_distance_m", 0, 10, 50000) if high is not None else None
    if low is not None and high is not None and low > high:
        raise ValueError("min_distance_m 不能大于 max_distance_m")
    if exact is not None and ((low is not None and exact < low) or (high is not None and exact > high)):
        raise ValueError(f"distance_m={exact:.0f} 不在给定里程区间 [{low}, {high}] 内")
    polygon_path = cfg.get('allowed_polygon_geojson')
    ring = None
    if polygon_path:
        if not isinstance(polygon_path, str):
            raise ValueError('allowed_polygon_geojson 必须是文件路径')
        ring = allowed_polygon(resolve_resource(base_dir, polygon_path))
    cards = load_cards(cfg, base_dir)
    cards_tol = number(cfg, 'cards_tol_m', CARD_TOL_M, 1, 60)
    cards_min = number(cfg, 'cards_min', 0, 0, 50, True)
    pace_amp = number(cfg, 'pace_amp', 1.0, 0.0, 3.0)   # 配速起伏强度（0=匀速）
    if cards:
        if ring is None:
            raise ValueError('要求踩点的任务必须提供 allowed_polygon_geojson：'
                             '打卡点与围栏要一起校验，否则无法证明生成的路线能跑')
        cards_min = cards_min or len(cards)
        if cards_min > len(cards):
            raise ValueError(f'cards_min={cards_min} 超过配置的打卡点数 {len(cards)}')
    # 手动区间（配速 / 步频 / 时长）：给了就按区间均匀抽，不再用 ±offset 的随机偏移，
    # 这样"每次不一样"但永远落在你能接受的范围内（例如 时长 9–12 分钟、步频 168–176）。
    pace_range = pair_range(cfg, "pace_range", 1.5, 30, " min/km")
    cadence_range = pair_range(cfg, "cadence_range", 50, 250, " spm")
    duration_range = pair_range(cfg, "duration_range_s", 30, 7200, " 秒")
    if pace_range is not None and duration_range is not None:
        raise ValueError("pace_range 与 duration_range_s 只能二选一："
                         "时长由里程和配速算出，两个都写会互相矛盾")
    drawing = exact is None and (length_offset > 0 or low is not None or high is not None)
    attempts = MAX_ATTEMPTS if (drawing or cards) else 1
    if cards:
        attempts = max(attempts, 12)      # 来源贴边时形变容易出界：多给几次机会
    last = ""
    strength = 1.0                        # 出界一次就把形变幅度减半，再抽一次
    for attempt in range(1, attempts+1):
        # 每次重抽都要换几何种子与独立的抽签流：+1000003 保证相邻尝试不相似，
        # 抽签流用另一套混合常数，避免"换种子却抽到同一组偏移"。
        seed = cfg['seed'] + (attempt-1)*1000003
        rng = random.Random((cfg['seed'] ^ ((attempt*0x9E3779B1) & 0xFFFFFFFFFFFFFFFF)) & 0x7FFFFFFFFFFFFFFF)
        factor = 1.0 if exact is not None else 1+rng.uniform(-length_offset, length_offset)
        target = exact if exact is not None else round(metrics["length_m"]*factor, 3)
        # 时长区间优先：先定时长，再由 时长/里程 反推配速（保证 finish 的 duration 就是它）
        planned_n = None
        if duration_range is not None:
            want = rng.uniform(*duration_range)
            planned_n = max(2, int(round(want/interval)))
            pace = (planned_n*interval)/60.0/(target/1000.0)
        elif pace_range is not None:
            pace = rng.uniform(*pace_range)
        else:
            pace = base_pace*(1+rng.uniform(-pace_offset, pace_offset))
        if not 1.5 <= pace <= 30:
            raise ValueError(f"抽到配速 {pace:.2f} min/km 超出 1.5–30；请检查时长/配速区间与里程是否匹配")
        if cadence_range is not None:
            cadence = rng.uniform(*cadence_range)
        else:
            cadence = base_cadence*(1+rng.uniform(-cadence_offset, cadence_offset))
        # 长度靠形变自己的长度校准（length_gain_pct）实现：要变长就把校准抬到
        # factor-1（+1% 余量保证可用长度足够），要变短则保持校准并截断尾部。
        gain = min(0.5, max(0.0, factor-1.0) + (0.01 if factor > 1.0 else 0.0))
        profile_k = dict(profile, length_gain_pct=gain)
        if strength < 1.0:               # 缩振幅：绕围栏跑时别把线撑出去
            for _key in ("lane_sigma_m", "drift_sigma_m", "jump_sigma_m", "max_lane_m"):
                profile_k[_key] = profile_k.get(_key, DEMO_PROFILE[_key])*strength
        route = deform_points(points, seed, profile_k)
        if cards:
            # 形变后把最近的顶点拉回卡片（邻域跟随）；拉弯会吃弦长，末尾折返补回
            route = pull_cards(route, cards, tol_m=min(4.0, cards_tol/4))
            route = extend_to(route, target)
        lengths = cumulative(route)
        if target > lengths[-1]:
            last = f"目标 {target:.1f} 米超过生成几何可用长度 {lengths[-1]:.1f} 米（形变后可用长度）"
            continue
        if low is not None and target < low:
            last = f"抽到总里程 {target:.1f} 米低于下限 {low:.0f} 米"
            continue
        if high is not None and target > high:
            last = f"抽到总里程 {target:.1f} 米高于上限 {high:.0f} 米"
            continue
        # 等时间间隔 + 等里程增量：三个参数互相自洽（见函数 docstring）
        n = planned_n if planned_n is not None else max(1, int(round(target/(interval*1000.0/(pace*60.0)))))
        step_m = target/n
        duration = n*interval
        mileage = [i*step_m for i in range(n+1)]      # 等增量，不做逐点舍入
        sampled = [position_at(route, lengths, m) for m in mileage]
        if ring is not None:
            try:
                validate_polygon_route(sampled, ring)
            except ValueError as exc:
                if not cards:
                    raise
                # 拉卡片可能把局部弯出围栏：减小形变幅度后换种子重抽，而不是整体失败
                last = f"采样点越过可跑区域（{exc}）"
                strength = max(0.15, strength*0.5)
                continue
        manage = card_manage_list(sampled, cards, cards_tol) if cards else []
        hits = sum(1 for item in manage if item['marked'] == 'Y')
        if cards and hits < cards_min:
            last = f"只踩到 {hits}/{len(cards)} 个打卡点（要求至少 {cards_min} 个）"
            continue
        effective_pace = duration/60.0/(target/1000.0)
        # 逐点配速不再恒定：热身放慢 → 平滑波动 → 2~3 段减速 → 收尾略快，
        # 整条时间轴再等比缩放到 duration（均值仍是声明配速）。
        paces = pace_profile(len(sampled), rng, effective_pace, amp=pace_amp)
        timed = retime(mileage, paces, cadence, duration)
        rows = []
        for p, m, tm in zip(sampled, mileage, timed):
            rows.append({"point": f"{p[0]:.8f},{p[1]:.8f}", "runMileage": m,
                         "runTime": tm["runTime"], "runStep": tm["runStep"],
                         "cadence_spm": tm["cadence_spm"], "speed": tm["speed"],
                         "runStatus": "1", "isFence": "Y", "isMock": False, "ts": "0"})
        data = {"pointsList": rows, "duration": duration, "recordMileage": target/1000,
                "recodeCadence": rows[-1]["runStep"]*60/duration,
                "recodePace": effective_pace,
                "recodeDislikes": hits, "manageList": manage}
        validate_output_polygon(data, ring)
        chord_m = cumulative(sampled)[-1]
        return {"code": 200, "metadata": {
            "synthetic": True, "engine": "deform", "coordinate_system": cfg["coordinate_system"],
            "seed": seed, "source": source_path.name,
            "source_length_m": round(metrics["length_m"], 3),
            "source_pace_min_km": None if metrics["pace_min_km"] is None else round(metrics["pace_min_km"], 3),
            "source_cadence_spm": None if metrics["cadence_spm"] is None else round(metrics["cadence_spm"], 3),
            "pace_min_km": round(effective_pace, 3), "cadence_spm": cadence,
            "pace_point_range_min_km": [round(min(paces), 2), round(max(paces), 2)],
            "duration_s": duration,
            "duration_range_s": list(duration_range) if duration_range else None,
            "pace_range": list(pace_range) if pace_range else None,
            "cadence_range": list(cadence_range) if cadence_range else None,
            "interval_s": interval, "step_m": round(step_m, 6), "target_m": target,
            "geometry_chord_m": round(chord_m, 3), "attempt": attempt, "attempts_limit": MAX_ATTEMPTS,
            "distance_window_m": [low, high] if (low is not None or high is not None) else None,
            "geometry_spacing_m": profile["spacing_m"], "deform_profile": profile,
            "cards_total": len(cards), "cards_hit": hits,
            "cards_min": cards_min if cards else None, "cards_tol_m": cards_tol if cards else None,
            "server_verified": False}, "data": data}
    if attempts == 1:
        raise ValueError(last or "目标里程无法生成")
    span = metrics["length_m"]*length_offset
    hint = (f"来源总里程 {metrics['length_m']:.0f} 米 ±{length_offset:.0%} = "
            f"[{metrics['length_m']-span:.0f}, {metrics['length_m']+span:.0f}] 米")
    if low is not None and high is not None:
        window = max(0.0, min(high, metrics["length_m"]+span)-max(low, metrics["length_m"]-span))
        hint += f"，与区间交集约占 {window/(2*span)*100:.0f}%" if span > 0 else ""
    raise ValueError(f"连续 {attempts} 次重抽都没能落在给定里程区间 "
                     f"[{low if low is not None else '-'}, {high if high is not None else '-'}] 内（最后一次：{last}）。"
                     f"{hint}；可放宽区间、换来源表、改 seed，或用 distance_m 精确指定总里程")


def geojson(task):
    return {"type": "FeatureCollection", "metadata": task["metadata"], "features": [
        {"type": "Feature", "properties": {"duration_s": task["data"]["duration"]},
         "geometry": {"type": "LineString", "coordinates": [
             list(map(float, p["point"].split(','))) for p in task["data"]["pointsList"]]}}]}
