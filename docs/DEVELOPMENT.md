# 开发与交接文档（2.0.0）

写给接手这个项目的下一个人或 AI。先读这份，再读 `AGENTS.md`（硬约束）、`docs/ARCHITECTURE.md`（模块地图）、`docs/DECISIONS.md`（已拍板的产品决策）。用户教程在根目录 `使用说明.md`。

---

## 1. 项目是什么

Windows 本地工作台，登录洛阳理工学院「云运动」账号，读取账号当前的任务（打卡区域）、打卡点和运动范围（围栏），在本地生成一条符合要求的路线，可以先演练再提交。前端是原生 HTML/CSS/JS，没有构建步骤；后端是 Python 3.12 + 标准库 HTTP 服务。

2.0.0 的定位：**只适配洛阳理工的开元与王城两个校区**，不做多学校选择。学校/校区的区分完全来自账号登录后服务器返回的任务列表。

---

## 2. 目录与模块

```
web.py                 启动入口：参数、单实例、端口、浏览器
schooldesk.py          已删除（2.0.0 不再有独立的学校工作台）
config.ini             发行基线：学校地址、加密参数、跑步参数（不含个人账号）
workbench/
  storage.py           路径、原子写、DPAPI、凭据（多账号 v2）、偏好、脱敏
  auth.py              token 复用、密码登录、抓包会话验证、账号列表、退出/忘记
  rules.py             任务选择、场地解析（points/fence）、本地检查范围、校验
  routes.py            四种来源、预览快照、路线库、导入导出、场地快照
  jobs.py              快照校验、worker 子进程、进度、停止、进程族
  capture.py           手机 LAN 页、每机 CA、短时 mitmproxy
  records.py           线上跑步记录查询（只读）
  backup.py            备份白名单、导入预览与冲突处理
  server.py            HTTP 路由（主面板）
static/                index.html / app.js / map.js / ui.js / style.css / vendor
main.py                上游协议与执行引擎（split/finish、人脸、几何）
yun_http.py            请求信封（SM4 + SM2）与响应解码
yun_route.py           轨迹生成/形变/配速曲线（上游几何）
taskgen.py             合成路线、围栏/点位的兼容几何工具
history.py             旧历史工具
tools/                 抓包桥、环境检查、构建校验、源码导出
tests/                 标准库 unittest + 一个 Node 几何测试
docs/                  架构、决策、验收、抓包、API、字段说明
```

`main.py` / `yun_route.py` / `yun_http.py` / `yun_face.py` 保留上游实现，改动尽量小；新逻辑都放在 `workbench/`。

---

## 3. 一条路线的数据流

```
登录（密码或抓包）
  → Auth.refresh() 拉 /run/getHomeRunInfo，任务缓存到 data/rules.json
  → rules.venue(store) 取当前任务的 points / fence / 要求
  → Routes.geometry() 把场地注入 taskgen（上锁-注入-恢复）
  → Routes.generate/preview 生成并采样轨迹，写快照（含场地与校验结论）
  → 浏览器渲染地图/统计；用户点演练或执行
  → Jobs.start 校验快照哈希、当前任务、点位/围栏摘要 → 子进程 worker
  → main.Yun_For_New 按快照回放：start → splitPointCheating → isStandard → finish
```

### 3.1 场地只有一个来源

`rules.venue(store)` 返回：

```python
{
  'source': 'server' | 'sample' | 'unselected',
  'task_id', 'name', 'campus',
  'cards': [[lon, lat], ...],       # 打卡点
  'fence': [[lon, lat], ...],       # 围栏（已闭合去重、做过面积/自相交检查）
  'points_status', 'fence_status',  # available / missing / invalid / ambiguous / sample
  'warnings', 'requirements', 'task',
}
```

- 任务里 `points` / `fence` 两种编码都接受：`"lon,lat|lon,lat"` 文本 或 `[[lon,lat],...]` 数组。
- 有点落在围栏外 → `points_status='ambiguous'`，点位不参与生成。
- **没有任何服务器任务**时才用 `examples/routes/fence_lylg.geojson` + `taskgen.CARDS` 作为示例几何（独立工具、`--demo`）。
- 有任务但没选 → `source='unselected'`，生成入口直接报错。

### 3.2 越界规则（2.0.0 收紧）

- 任务没下发围栏：主页红条提示 + 生成按钮禁用 + 后端 `Routes.generate` 拒绝。
- 生成或导入的路线只要有点越界：停止并报「路线有 N 个点落在当前区域之外」。
- 执行前 `Jobs.start` 再校验一次围栏/点位摘要；切任务后旧预览不能演练或执行。

### 3.3 本地检查（rules.limits）

- 存储在 `settings.json` 的 `checks`，按 `task_id` 分开：`{enabled: bool, values: {km_min, ...}}`。
- 只保存用户改过的字段；没改的继续跟服务器值走；服务器没下发的字段不参与检查。
- 默认开关为开；关闭后越界只产生 `notice`，不拦截。**缺必填字段、任务要求人脸**始终是 `error`。
- 校验结果格式：`[{field, message, suggested?, level: 'error'|'notice'}]`；前端只用 `error` 禁用按钮。

---

## 4. 2.0.0 相对 1.0.0 改了什么

### 场地与执行
- `Routes.geometry()` 不再读死 `fence_lylg.geojson`，改为 `rules.venue`；预览快照记录当次场地与校验结论。
- 多任务必须选择；切任务后旧预览作废（`jobs` 比对点位与围栏摘要）。
- `main.py` 打卡点支持数组编码；必填字段缺失给中文错误；`raDislikes` 可缺省。
- 删除 `tasks_lylg/`（6 份与真实路线差别大的开元打表），清理 `config.ini`、`build_exe.py`、`build_release.py`、`tools/export_source.py`、`examples/routes/deform_lylg.json`、CLI 默认目录等引用。

### 本地检查
- 新增 `rules.save_limits` / `limits` / `view()['limits']` 与 `POST /api/rules/limits`。
- 账号页「当前任务规则」：服务器值 + 学校要求原文 + 服务器原始字段（可折叠、不含身份与坐标）+ 可编辑检查范围。
- `settings.json` 新增 `checks`；`save_settings` 做结构校验。

### 账号
- 凭据从单账号升级为多账号 v2：`{schema_version:2, current, accounts:{学号:{password, token, device...}}}`，最多 10 个，旧文件自动迁移。
- 登录页列出已保存账号，点学号一键登录（`/api/auth/login-saved`）；账号页可单独「忘记」某账号（`/api/auth/forget`）。
- 三个入口区分：切换账号（不结束会话）/ 退出登录（结束会话、保留密码，`clear:false`）/ 退出并清除凭据（删除全部账号）。
- `settings.json` 新增 `autologin`：退出后不会被下次刷新自动登回去。

### 地图与界面
- 运动范围改为**紫色虚线 + 半透明遮罩**，与路线绿/点位绿/手绘橙区分；工具栏「范围遮罩」开关（`settings.defaults.fence_mask`，默认开），地图、Canvas 后备渲染、导出的 PNG 三处一致。
- 地图下方图例写明每种颜色；关掉遮罩时图例文字同步变化。
- 生成路线不再重置用户的缩放/拖拽：`map.fit()` 只在切任务、首次出图或点「适应路线」时执行。
- 新增地图 PNG 导出（围栏、打卡点编号、起终点、路线、100 m 网格、比例尺、标题，不含底图）。
- 下拉框统一自绘箭头（修复 `background` 简写把箭头平铺的问题）；关键控件加 `title` 悬停说明。
- 左上角品牌图标重画为跑步小人；新增图片图标。
- 读取线上历史路线的入口：路线库右上角「读取线上记录」+ 来源为历史记录时的同名按钮；下载的路线带来源学号。

### 其他
- 高级设置、快速模式、范围遮罩改完立即保存；生成时合并默认项，不再冲掉其他开关。
- 文档：根目录 `使用说明.md`（纯 Markdown）；`docs/` 保留开发记录。

---

## 5. 数据与隐私边界

- 凭据只在内存与 `credentials.dpapi`（Windows 当前用户 DPAPI）；普通配置、日志、源码、发行包都不含个人凭据。
- `data/`（源码）或 `用户数据/`（便携包）含账号、规则缓存、路线、预览、抓包 CA；**不得提交、不得打包**。
- 备份 JSON 显式白名单：账号/token/设备字段、偏好、规范化路线；不含登录密码、CA 私钥、Python 环境。
- 抓包桥只写候选 token 与设备头（DPAPI），不保存正文。
- 后端日志走 `store.safe_message` 脱敏；页面只返回掩码账号。

---

## 6. 调试与验证

```powershell
# 全量测试（需要 runtime 或任意 3.12 环境装好 requirements.txt）
runtime\python.exe -X utf8 -m unittest discover -s tests -v
node --test tests/test_drawing.mjs

# 浏览器 UI 验收（隔离数据、禁止网络请求）
runtime\python.exe -X utf8 web.py --demo --no-browser

# 本地起一个「两个打卡区域」的假数据面板（不联网）
#   临时目录里写 rules.json（两条任务，各自 points/fence），
#   构造 Application(store, demo=False)，把 auth.authenticated 设为 True，再 serve。
```

真机数据核对（只读、不提交）建议：复制 `data/rules.json` + `data/settings.json` 到临时目录，用上面的假登录态起面板，检查围栏/点位/检查范围显示是否与官方 App 一致。

已知的真实数据结构（2026-11-19 实测，王城账号）：

- 任务字段：`id / raName / campusId / points / fence / raSingleMileageMin|Max / raPaceMin|Max / raCadenceMin|Max / dayStartTime|EndTime / raDislikes / raMinDislikes / cardRange / raRunArea / raScalingRatio / raStartTime / raEndTime / raStatus ...`
- `points`、`fence` 都是 `"lon,lat|..."` 文本；王城两个任务的围栏几乎是同一块校区四边形，切换任务主要换打卡点。
- `cardRange=20` 是打卡判定半径；`raRunArea` 是区域标识字符串，不是坐标。

---

## 7. 已知限制与下一步

1. **只支持洛阳理工**。多学校需要：抓包识别服务地址与学校编号、学校资料目录、不同任务结构/字段别名的方言层。抓包侧目前只取 token 与设备头，不做自动建档。
2. 王城/开元真实数据只在本地只读核对过，**没有做真实提交验收**；成绩判定以官方 App 为准。
3. 围栏是校区级四边形，路线可能在打卡点之外的校区范围跑；若要限制在场地附近，需要本地规则（服务器没给这块数据）。
4. 打卡点数量/半径等字段（`raDislikes`、`raMinDislikes`、`cardRange`）的语义尚未跨校对完，代码按「有值就用，缺省不猜」处理。
5. 地图底图依赖高德瓦片，断网只剩示意图；坐标固定 GCJ-02，不做 WGS84 转换。
6. 单账号在线：切换账号会清掉上一个账号的本机规则与记录缓存（服务器记录不受影响）。

---

## 8. 发版流程

1. 改 `workbench/__init__.py` 的 `__version__`（EXE 文件名与 manifest 都取这个值）。
2. 跑全量测试。
3. 构建 EXE（需要装了 PyInstaller 的 Python 3.12）：

   ```powershell
   python build_exe.py
   # 产物：distribution/exe/yunRun-LYLG-v<版本>.exe
   ```

4. 构建免安装 ZIP（需要 `runtime/python.exe` 存在）：

   ```powershell
   runtime\python.exe -X utf8 build_release.py
   # 产物：distribution/yunRun-LYLG-v<版本>-内置环境.zip
   ```

5. 提交并打 tag：`git tag -a v<版本> -m "..."`。
6. 发布前检查 `distribution/release-manifest.json`，确认 `personal_data_included=false`，且 ZIP 不含 `data/`、`用户数据/`、CA、APK。

---

## 9. 容易踩的坑

- `runtime` 是便携 Python，和系统 Python 不是一回事；装依赖用 `runtime\python.exe -m pip`。
- PowerShell 里 `[IO.File]::ReadAllText` 不带编码参数时别拿来做中文批量替换，容易踩编码坑；优先用编辑工具或显式 UTF-8。
- `main.py` 的几何/时间轴来自快照，worker 不允许重新生成；改执行链时先看 `jobs.py` 的校验顺序。
- 前端没有构建步骤，改完直接刷新；`static/map.js` 的围栏样式要同时改 Leaflet、Canvas 和 `exportPng` 三处。
- 浏览器缓存：`server.py` 对所有响应加了 `Cache-Control: no-store`，但独立打开 `file://` 页面会失效。
- 提交前确认工作区没有 `data/`、`.test-data/`、`distribution/`（都在 `.gitignore`）。
