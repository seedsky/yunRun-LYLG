# -*- coding: utf-8 -*-
"""校园路线几何与任务表兼容核心。

输入：经纬度点列（GCJ-02）、目标距离（米）、平均配速（分钟/公里）、步频及种子。
输出：几何点列，或含 pointsList/时间轴/步数/汇总的任务表。

新工作台使用 synth、densify、trim_at、to_task 等纯计算函数；服务器规则、严格长度校验、
最终采样与打卡点行索引由 workbench.routes 统一编排。这里保留的旧 generate CLI 有本地
固定范围，不能作为新 UI 的规则来源。CARDS/FENCE 是兼容全局量，调用时须经过
Routes.geometry 的锁与恢复上下文，避免两个页面互相污染。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:              # 复用主工程的配速曲线/重定时
    sys.path.insert(0, str(ROOT))
from yun_route import pace_profile, retime   # noqa: E402


class TaskgenError(ValueError):
    """生成失败（参数不合规 / 围栏外 / 踩点不够 / 素材不可用）。"""


FENCE = [
    (112.413904, 34.611019),
    (112.416887, 34.607354),
    (112.424578, 34.611668),
    (112.422518, 34.62002),
]
CARDS = [
    (112.417622, 34.612324),
    (112.417504, 34.612702),
    (112.417381, 34.613091),
    (112.418457, 34.612537),
    (112.418401, 34.612969),
    (112.418265, 34.613424),
]
SCHOOL_ID = 197
STEP_M = 5.0
R_EARTH = 6371008.8
M_PER_DEG_LAT = 110574.0
M_PER_DEG_LNG = 111320.0 * math.cos(math.radians(34.613))


# ---------------- 基础几何 ----------------
def hav(a, b):
    lng1, lat1, lng2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dl, dp = lng2 - lng1, lat2 - lat1
    h = math.sin(dp / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def plen(pts):
    return sum(hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def inside(pt, poly):
    x, y = pt
    flag, n = False, len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            flag = not flag
        j = i
    return flag


def inset(poly, ratio):
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    return [(cx + (p[0] - cx) * ratio, cy + (p[1] - cy) * ratio) for p in poly]


def bezier(p0, c, p1, step=STEP_M):
    """二次贝塞尔，按约 step 米采样（不含 p0，含 p1）。"""
    approx = hav(p0, c) + hav(c, p1)
    n = max(4, int(approx / step))
    out = []
    for k in range(1, n + 1):
        t = k / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t ** 2 * p1[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t ** 2 * p1[1]
        out.append((x, y))
    return out


def leg(p0, p1, rng, fence, max_bow_m=130.0):
    """两点之间画一条随机弧线；弓高逐次收缩直到整条线都在围栏内。"""
    dx = (p1[0] - p0[0]) * M_PER_DEG_LNG
    dy = (p1[1] - p0[1]) * M_PER_DEG_LAT
    d = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / d, dx / d  # 单位法向
    for scale in (1.0, 0.7, 0.5, 0.3, 0.15, 0.0):
        bow = rng.uniform(0.25, 1.0) * max_bow_m * scale * rng.choice((-1, 1))
        cx = (p0[0] + p1[0]) / 2 + nx * bow / M_PER_DEG_LNG
        cy = (p0[1] + p1[1]) / 2 + ny * bow / M_PER_DEG_LAT
        pts = bezier(p0, (cx, cy), p1)
        if all(inside(p, fence) for p in pts):
            return pts
    return bezier(p0, ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2), p1)


def wobble(pts, rng, cards, sigma_m=2.2, damp=0.72):
    """给中间点加一点 OU 抖动（近打卡点不加抖动，保留点位）。"""
    out, off = [], 0.0
    for i, p in enumerate(pts):
        near_card = any(hav(p, c) < 12.0 for c in cards)
        if i == 0 or i == len(pts) - 1 or near_card:
            out.append(p)
            off = 0.0
            continue
        off = damp * off + rng.gauss(0, 1) * sigma_m
        ang = rng.uniform(0, 2 * math.pi)
        out.append((p[0] + math.cos(ang) * off / M_PER_DEG_LNG, p[1] + math.sin(ang) * off / M_PER_DEG_LAT))
    return out


def densify(path, step=STEP_M):
    out = [path[0]]
    for a, b in zip(path, path[1:]):
        seg = hav(a, b)
        if seg <= 1e-9:
            continue
        n = max(1, int(seg // step))
        for k in range(1, n + 1):
            t = 1.0 if k == n else k * step / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def trim_at(pts, target):
    acc, out = 0.0, [pts[0]]
    for a, b in zip(pts, pts[1:]):
        seg = hav(a, b)
        if acc + seg >= target:
            t = (target - acc) / seg if seg else 0.0
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out, target
        out.append(b)
        acc += seg
    return out, acc


# ---------------- 合成 ----------------
def synth(target_m, pace, cadence, seed, cards_min=6):
    rng = random.Random(seed)

    order = CARDS[:]
    rng.shuffle(order)                     # 每次打卡顺序不同
    if rng.random() < 0.4:
        order = order[:cards_min] + order[cards_min:]  # 全踩（保留 6 个）

    # 1) 打卡点之间用随机弧线相连
    path = [order[0]]
    for a, b in zip(order, order[1:]):
        path += leg(a, b, rng, FENCE)

    # 2) 最后一个打卡点之后，绕围栏内圈随机锚点，把里程补到目标
    ratio = rng.uniform(0.35, 0.8)
    for _ in range(4):                      # 若太短就逐步放大绕圈
        ring = inset(FENCE, ratio)
        ring = [(p[0] + rng.uniform(-18, 18) / M_PER_DEG_LNG,
                 p[1] + rng.uniform(-18, 18) / M_PER_DEG_LAT) for p in ring]
        if not all(inside(p, FENCE) for p in ring):
            ratio *= 0.9
            continue
        centre = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
        seq = [path[-1]] + ring[1:] + [ring[0]]
        path2 = list(path)
        for a, b in zip(seq, seq[1:]):
            path2 += leg(a, b, rng, FENCE)
        path2 += leg(path2[-1], order[0], rng, FENCE)   # 回到起点
        if plen(path2) >= target_m:
            path = path2
            break
        ratio = min(0.85, ratio * 1.25)

    pts = densify(wobble(path, rng, CARDS))
    if plen(pts) < target_m:
        raise TaskgenError(f"合成里程不足（{plen(pts):.0f} m < {target_m:.0f} m），换个种子或放大围栏绕行")

    # 3) 截到目标里程，但必须已经过完所有打卡点
    cut, total = trim_at(pts, target_m)
    visited = {i for i, c in enumerate(CARDS) if any(hav(p, c) < 6.0 for p in cut)}
    if len(visited) < cards_min:
        last_card_dist = max(
            next(d for d, p in _cum(pts) if hav(p, c) < 6.0) for c in CARDS if any(hav(p, c) < 6.0 for p in pts)
        )
        raise TaskgenError(
            f"目标 {target_m / 1000:.1f} km 太短：走完 6 个打卡点至少要 {last_card_dist / 1000:.2f} km，请加大 --km"
        )

    # 4) 围栏校验
    outside = [p for p in cut if not inside(p, FENCE)]
    if outside:
        raise TaskgenError(f"有 {len(outside)} 个点跑到围栏外（{outside[0]}），已中止")

    return cut, total, visited, order


def _cum(pts):
    acc = 0.0
    yield 0.0, pts[0]
    for a, b in zip(pts, pts[1:]):
        acc += hav(a, b)
        yield acc, b


def to_task(pts, total, pace, cadence, order, rng=None, amp=1.0, cad_lo=None, cad_hi=None):
    """逐点时间轴带真实快慢：等距点 + 配速曲线 → 每点 Δt 不同，均值仍等于 pace。"""
    duration = int(round(total / 1000 * pace * 60))
    mileage = [acc for acc, _ in _cum(pts)]
    paces = pace_profile(len(pts), rng or random.Random(0), pace, amp=amp)
    timed = retime(mileage, paces, cadence, duration)
    points = []
    for (lng, lat), m, tm in zip(pts, mileage, timed):
        points.append({
            "id": 0,
            "point": f"{lng:.14f},{lat:.14f}",
            "speed": tm["speed"],
            "runStatus": 1,
            "runRecordId": 0,
            "runTime": str(tm["runTime"]),
            "isFence": "Y",
            "runStep": str(tm["runStep"]),
            "runMileage": str(round(m, 6)),
        })
    manage = [{"point": f"{c[0]:.6f},{c[1]:.6f}", "marked": "Y", "index": i} for i, c in enumerate(order)]
    last_step = int(points[-1]["runStep"])
    declared = last_step * 60 / duration
    # 申报步频必须落在用户给的区间里：超了就整体缩放步数（时间轴不动）
    if cad_lo and cad_hi and not (cad_lo <= declared <= cad_hi):
        want = min(cad_hi, max(cad_lo, declared))
        factor = want / declared if declared else 1.0
        for p in points:
            p["runStep"] = str(int(round(int(p["runStep"]) * factor)))
        last_step = int(points[-1]["runStep"])
        declared = last_step * 60 / duration
    return {
        "msg": "操作成功",
        "code": 200,
        "data": {
            "recordMileage": round(total / 1000, 2),
            "recodePace": round(pace, 2),
            "recodeCadence": round(declared, 1),
            "recodeDislikes": len(manage),
            "duration": duration,
            "pointsList": points,
            "schoolId": SCHOOL_ID,
            "manageList": manage,
        },
    }


def geojson(pts, cards):
    feats = [
        {"type": "Feature", "properties": {"kind": "route"},
         "geometry": {"type": "LineString", "coordinates": [[p[0], p[1]] for p in pts]}},
        {"type": "Feature", "properties": {"kind": "fence"},
         "geometry": {"type": "Polygon", "coordinates": [[[p[0], p[1]] for p in FENCE + FENCE[:1]]]}},
    ]
    feats += [
        {"type": "Feature", "properties": {"kind": "card", "index": i},
         "geometry": {"type": "Point", "coordinates": [c[0], c[1]]}}
        for i, c in enumerate(cards)
    ]
    return {"type": "FeatureCollection", "features": feats}


def load_poly(path):
    """读折线：支持 geojson LineString / [lng,lat] 数组 / {"points":[...]} / 任务表 pointsList。"""
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows = None
    if isinstance(obj, dict):
        if obj.get("type") == "FeatureCollection":
            for f in obj.get("features") or []:
                g = f.get("geometry") or {}
                if g.get("type") == "LineString":
                    return [(float(x), float(y)) for x, y in g["coordinates"]]
            raise TaskgenError(f"{path}：找不到 LineString")
        if obj.get("type") == "LineString":
            return [(float(x), float(y)) for x, y in obj["coordinates"]]
        rows = obj.get("points") or obj.get("pointsList")
        if rows is None and isinstance(obj.get("data"), dict):
            rows = obj["data"].get("pointsList")
    elif isinstance(obj, list):
        rows = obj
    if not rows:
        raise TaskgenError(f"{path}：不认识的文件格式（要 geojson/坐标数组/任务表）")
    out = []
    for row in rows:
        p = row.get("point") if isinstance(row, dict) else row
        try:
            lng, lat = (float(x) for x in str(p).split(","))
        except ValueError:
            raise TaskgenError(f"{path}：坐标格式不对：{p!r}")
        out.append((lng, lat))
    return out


def load_history_polys(where):
    """历史路线：目录（含 route_*.json）或单个文件，返回 [(名字, 折线)]。"""
    p = Path(where)
    if p.is_dir():
        files = sorted(p.glob("route_*.json")) or sorted(p.glob("*.json"))
        files = [f for f in files if f.name != "index.json"]
    else:
        files = [p]
    out = []
    for f in files:
        try:
            poly = load_poly(f)
        except SystemExit:
            continue
        if len(poly) >= 10:
            out.append((f.stem, poly))
    if not out:
        raise TaskgenError(f"{where}：没找到可用轨迹（先跑 tools/fetch_history.py）")
    return out


def visited_cards(pts, tol=20.0):
    order, seen = [], set()
    for p in pts:
        for i, c in enumerate(CARDS):
            if i not in seen and hav(p, c) <= tol:
                seen.add(i)
                order.append(i)
    return [CARDS[i] for i in order]


def centroid(poly):
    return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))


def pull_inside(pt, poly, steps=40, frac=0.02):
    """贴边/出界的点往围栏中心方向轻推，最多推 steps 步（适合手绘压线的情况）。"""
    if inside(pt, poly):
        return pt
    cx, cy = centroid(poly)
    x, y = pt
    for _ in range(steps):
        x += (cx - x) * frac
        y += (cy - y) * frac
        if inside((x, y), poly):
            return (x, y)
    return (x, y)


def snap_cards(poly, tol=20.0):
    """手绘线常常离打卡点几十米：把没踩到的打卡点按"多绕最少"插进折线。"""
    pts = list(poly)
    for c in CARDS:
        if min(hav(p, c) for p in pts) <= tol:
            continue
        best = None
        for i in range(len(pts) - 1):
            extra = hav(pts[i], c) + hav(c, pts[i + 1]) - hav(pts[i], pts[i + 1])
            if best is None or extra < best[0]:
                best = (extra, i)
        if best is not None:
            pts.insert(best[1] + 1, c)
    return pts


def build_from_polyline(poly, target_m, cards_min=3, snap=False):
    """用一条真实/手绘折线出任务：补长（原路折返）→ 截到目标里程 → 保证踩够打卡点。"""
    if snap:
        poly = snap_cards(poly)
    poly = [pull_inside(p, FENCE) for p in poly]
    base = densify(poly)
    if plen(base) < target_m:                      # 不够长就原路折返跑
        for _ in range(6):
            base = base + densify(list(reversed(poly)))[1:]
            if plen(base) >= target_m:
                break
    if plen(base) < target_m:
        raise TaskgenError(f"这条线只有 {plen(base) / 1000:.2f} km，折返 6 次也不够 {target_m / 1000:.2f} km")

    target = target_m
    for _ in range(6):
        cut, total = trim_at(base, target)
        order = visited_cards(cut)
        if len(order) >= cards_min:
            cut = [pull_inside(q, FENCE) for q in cut]
            outside = [q for q in cut if not inside(q, FENCE)]
            if outside:
                raise TaskgenError(f"有 {len(outside)} 个点跑到围栏外（{outside[0]}），换一条或改手绘")
            return cut, total, order, len(order)
        target = min(target * 1.1, 5000.0)         # 太短没踩够，就放长一点
    raise TaskgenError(f"截到 {target / 1000:.2f} km 仍只踩到 {len(order)} 个打卡点，请换一条路线")


def emit(task, pts, order, out, idx, label, preview, args):
    path = out / f"tasklist_{idx}.json"
    path.write_text(json.dumps(task, ensure_ascii=False, indent=4), encoding="utf-8")
    km = task["data"]["recordMileage"]
    print(f"{path.name}: {len(pts)} 点 / {km:.3f} km / 配速 {task['data']['recodePace']} / "
          f"步频 {task['data']['recodeCadence']} / {task['data']['duration']} 秒 / "
          f"踩中 {len(order)}/6 个打卡点 / 来源={label}")
    if preview:
        Path(preview).write_text(json.dumps(geojson(pts, order), ensure_ascii=False), encoding="utf-8")
        print(f"预览：{preview}")



# ---------------- 供网页面板/命令行共用的编排入口 ----------------
def generate(count=2, km=2.0, pace_min=5.0, pace_max=5.0, cad_min=170.0, cad_max=170.0,
             seed=None, out=None, replace=True, source="synth", source_path=None,
             preview=None, max_minutes=20.0, amp=1.0):
    """生成 count 份任务表，返回摘要列表（不打印、不退出，异常抛 TaskgenError）。"""
    if not 2.0 <= float(km) <= 5.0:
        raise TaskgenError(f"距离必须在 2.0-5.0 公里之间（当前 {km}）")
    pace_min, pace_max = float(pace_min), float(pace_max)
    if not (3.0 <= pace_min <= pace_max <= 10.0):
        raise TaskgenError(f"配速 {pace_min:g}-{pace_max:g} 超出学校区间 3.0-10.0")
    if float(km) * pace_max > float(max_minutes):
        raise TaskgenError(f"{km} km × 最慢配速 {pace_max:g} = {float(km) * pace_max:.1f} 分钟，"
                           f"超过单次上限 {max_minutes:g} 分钟")
    cad_min, cad_max = float(cad_min), float(cad_max)
    if not (50 <= cad_min <= cad_max <= 250):
        raise TaskgenError(f"步频 {cad_min:g}-{cad_max:g} 超出 50-250")

    out_dir = Path(out or (ROOT / "tasks_lylg"))
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("tasklist_*.json"))
    start_idx = 0 if replace else len(existing)
    base_seed = int(seed) if seed not in (None, "") else random.randrange(1, 10 ** 9)

    pool = None
    if source == "history":
        pool = load_history_polys(source_path or (ROOT / "history_routes"))
    elif source == "file":
        if not source_path:
            raise TaskgenError("手绘模式需要 geojson 路径")
        pool = [(Path(source_path).stem, load_poly(source_path))]

    made = []
    for i in range(int(count)):
        s_i = base_seed + i
        rng = random.Random(s_i)
        pace = round(rng.uniform(pace_min, pace_max), 2)
        cadence = rng.uniform(cad_min, cad_max)
        if pool:
            label, poly = pool[i % len(pool)]
            pts, total, order, _visited = build_from_polyline(poly, float(km) * 1000,
                                                              snap=(source == "file"))
            label = f"{label}" if source == "history" else f"手绘 {label}"
        else:
            label = f"合成 seed={s_i}"
            pts, total, _visited, order = synth(float(km) * 1000, pace, cadence, s_i)
        task = to_task(pts, total, pace, cadence, order, rng, amp=amp,
                       cad_lo=cad_min, cad_hi=cad_max)
        dur = task["data"]["duration"]
        if dur > float(max_minutes) * 60:
            raise TaskgenError(f"用时 {dur} 秒超过上限 {float(max_minutes) * 60:.0f} 秒")
        idx = start_idx + i
        path = out_dir / f"tasklist_{idx}.json"
        path.write_text(json.dumps(task, ensure_ascii=False, indent=4), encoding="utf-8")
        made.append({
            "name": path.name, "points": len(pts), "km": task["data"]["recordMileage"],
            "pace": task["data"]["recodePace"], "cadence": task["data"]["recodeCadence"],
            "duration": dur, "cards": len(order), "source": label,
        })
        if preview and i == 0:
            Path(preview).write_text(json.dumps(geojson(pts, order), ensure_ascii=False),
                                     encoding="utf-8")
    return made


def generate_to_dir(**kwargs):
    """generate() 的别名，语义更清楚：写到 tasks_lylg 目录。"""
    return generate(**kwargs)

def main():
    try:
        import sys as _sys
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--km", type=float, default=2.0)
    ap.add_argument("--pace", type=float, default=5.0)
    ap.add_argument("--cadence", type=int, default=170)
    ap.add_argument("--seed", type=int, default=None, help="给第 1 份的种子，后面每份 +1")
    ap.add_argument("--amp", type=float, default=1.0, help="配速起伏强度 0.6 平缓 / 1.0 正常 / 1.5 明显")
    ap.add_argument("--out", default=str(ROOT / "tasks_lylg"))
    ap.add_argument("--preview", default=None, help="导出第 1 份的 geojson 路径")
    ap.add_argument("--max-minutes", type=float, default=20.0, help="该校规则：单次跑步上限（分钟）")
    ap.add_argument("--from-history", default=None, help="历史路线目录（tools/fetch_history.py 产物）")
    ap.add_argument("--from-file", default=None, help="手绘/自定义折线：geojson 或 [lng,lat] 数组")
    ap.add_argument("--dur-min", type=float, default=None, help="时长区间下限（分钟），与 --pace 互斥")
    ap.add_argument("--dur-max", type=float, default=None, help="时长区间上限（分钟）")
    ap.add_argument("--cadence-min", type=int, default=None, help="步频区间下限（spm）")
    ap.add_argument("--cadence-max", type=int, default=None, help="步频区间上限（spm）")
    ap.add_argument("--replace", action="store_true", help="从 tasklist_0 开始覆盖写（默认接在已有文件后面编号）")
    args = ap.parse_args()

    if args.dur_min is not None or args.dur_max is not None:
        lo = args.dur_min if args.dur_min is not None else args.dur_max
        hi = args.dur_max if args.dur_max is not None else args.dur_min
        if not (0 < lo <= hi <= args.max_minutes):
            raise TaskgenError(f"时长区间 {lo:g}–{hi:g} 分钟不合法（需 0 < 下限 ≤ 上限 ≤ {args.max_minutes}）")
        # 时长 ÷ 里程 = 配速区间；一致性交给后面统一校验
        args.pace_min = lo/args.km
        args.pace_max = hi/args.km
        if not (3.0 <= args.pace_min and args.pace_max <= 10.0):
            raise TaskgenError(f"换算出的配速区间 {args.pace_min:.2f}–{args.pace_max:.2f} 超出学校 3–10；"
                             f"请调整时长或里程")
    else:
        args.pace_min = args.pace
        args.pace_max = args.pace
    if (args.cadence_min is not None or args.cadence_max is not None):
        clo = args.cadence_min if args.cadence_min is not None else args.cadence_max
        chi = args.cadence_max if args.cadence_max is not None else args.cadence_min
        if not (50 <= clo <= chi <= 250):
            raise TaskgenError(f"步频区间 {clo}–{chi} 不合法（需 50 ≤ 下限 ≤ 上限 ≤ 250）")
        args.cad_min, args.cad_max = clo, chi
    else:
        args.cad_min = args.cad_max = args.cadence

    if not 2.0 <= args.km <= 5.0:
        raise TaskgenError(f"距离必须在 2.0-5.0 公里之间（当前 {args.km}）")
    if not 3.0 <= args.pace <= 10.0:
        raise TaskgenError(f"配速必须在 3.0-10.0 分/公里之间（当前 {args.pace}）")
    if args.km * args.pace > args.max_minutes:
        raise TaskgenError(
            f"{args.km} km × 配速 {args.pace} = {args.km * args.pace:.1f} 分钟，超过单次上限 {args.max_minutes} 分钟；"
            f"请降低里程或加快配速（≥{args.km / args.max_minutes:.1f}）"
        )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("tasklist_*.json"))
    start_idx = 0 if args.replace else len(existing)   # --replace：从头覆盖，避免每次多出一串表
    base_seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 9)

    if args.from_history or args.from_file:
        if args.from_history:
            pool = load_history_polys(args.from_history)
        else:
            pool = [(Path(args.from_file).stem, load_poly(args.from_file))]
        print(f"素材 {len(pool)} 条：" + "、".join(n for n, _ in pool))
        for i in range(args.count):
            seed = base_seed + i
            label, poly = pool[i % len(pool)]
            rng = random.Random(seed)
            pace = round(rng.uniform(args.pace_min, args.pace_max), 2)
            cadence = rng.uniform(args.cad_min, args.cad_max)
            pts, total, order, _ = build_from_polyline(poly, args.km * 1000, snap=bool(args.from_file))
            task = to_task(pts, total, pace, cadence, order, rng, amp=args.amp)
            if task["data"]["duration"] > args.max_minutes * 60:
                raise TaskgenError(f"用时 {task['data']['duration']} 秒超过上限，请加大 --pace")
            emit(task, pts, order, out, start_idx + i, label, args.preview if i == 0 else None, args)
        return

    for i in range(args.count):
        seed = base_seed + i
        rng = random.Random(seed)
        pace = round(rng.uniform(args.pace_min, args.pace_max), 2)
        cadence = rng.uniform(args.cad_min, args.cad_max)
        pts, total, visited, order = synth(args.km * 1000, pace, cadence, seed)
        task = to_task(pts, total, pace, cadence, order, rng, amp=args.amp)
        if task["data"]["duration"] > args.max_minutes * 60:
            raise TaskgenError(f"用时 {task['data']['duration']} 秒超过上限 {args.max_minutes * 60:.0f} 秒，请加大 --pace")
        emit(task, pts, order, out, start_idx + i, f"合成 seed={seed}", args.preview if i == 0 else None, args)


if __name__ == "__main__":
    main()