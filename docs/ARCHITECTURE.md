# 维护与扩展地图

## 从入口找代码

`web.py` 管理启动、单实例、端口和浏览器入口；`workbench/server.py` 把 HTTP 操作交给服务，不拼接页面、不接收命令行字符串。

| 模块 | 负责什么 | 扩展入口 |
|---|---|---|
| storage.py | 路径、原子文件写入、DPAPI、偏好、脱敏 | 新增持久化项时明确是否允许备份 |
| auth.py | token 复用、单次重新登录、只读规则同步 | 新登录方式放在此处，避免复制协议代码 |
| rules.py | 规则来源、任务选择、参数约束 | 新字段先明确单位与来源；未知值不要猜 |
| routes.py | 所有来源、预览快照、库、文件导入导出 | 新来源应输出同一种规范化任务 |
| jobs.py | 快照校验、worker、进度、停止、控制台进程族 | 上传异常不自动重放 |
| capture.py | LAN 测试页、动态证书、短时抓包进程 | 不将完整主面板绑定到 LAN |
| backup.py | 备份白名单、导入预览和重名策略 | 新数据种类须显式加入白名单 |
| static/app.js | 页面状态与事件 | 新页面复用 ui.js，不写独立请求封装 |
| static/map.js | 地图、轨迹、手绘与断网后备 | 所有坐标采用 GCJ-02 |
| static/style.css | B/C 双主题、响应式布局 | 使用颜色变量，不在组件中写死两套样式 |

`main.py`、`yun_http.py`、`yun_face.py`、`yun_route.py`、`taskgen.py` 保留协议与几何兼容实现。新工作台通过适配层调用它们；旧 taskgen.generate 的固定范围不作为新 UI 规则入口。不要把界面字段再次直接接到旧 CLI 的随机选表流程。

## 一条路线的数据流

来源与参数 → Routes.generate/preview → 规范化任务 → 最终采样 → 重算打卡点行索引 → 快照 SHA-256 → 地图/统计。

保存路线只复制当前快照到路线库，不是执行的前置条件。执行提交 preview_id；后台刷新最新任务，校验范围、时间、几何和命中点；worker 读取同一快照并验证摘要，只包含一个 tasklist 文件，不再次生成或随机挑选其他路线。

时间戳、记录 ID、网络实际耗时等运行时量由兼容引擎产生；几何与计划时间轴取自预览快照。服务端接受结束请求与最终成绩有效性分开显示。

## 生命周期

浏览器只是本地客户端。后台负责状态与子进程；页面刷新可恢复预览、进度和日志。Windows Job Object 设置 KILL_ON_JOB_CLOSE，后台进程句柄关闭会结束其执行/抓包子进程。重启发现未完成状态，只标记 interrupted，不自动恢复发送。

停止先落 stop 标记，worker 在下一次等待或发送前检查；阻塞中的请求最多等待短暂宽限期后结束进程，其结果可能未知，不能宣称已撤回。

## 数据边界

config.ini 是无个人账号的发行基线。本机数据包括 credentials.dpapi、settings.json、advanced.json、rules.json、routes/、history/、previews/、jobs/、capture-ca/。源码模式存放于 data/；便携包资源根名为“程序文件”且同级存在“启动入口”时，默认存放在同级“用户数据/”。环境变量 YUNRUN_DATA_DIR 可覆盖，两种入口与 worker 共用此解析。build_release.py 仅改变发行 ZIP 布局，不移动开发目录或个人数据。

凭据不通过 API 返回；日志只接受 worker 的白名单事件。LAN 手机页仅提供连通性、公开证书和 APK，主面板仅绑定 127.0.0.1。写 API 校验本次后台 token 与 Origin，防止其他网页跨站调用本地执行入口。

抓包桥只把候选 token 和设备头写入 captured-session.dpapi，不清空 credentials.dpapi。Auth.accept_capture 用 /login/getStudentInfo 的 userName、schoolId 及 /run/getHomeRunInfo 验证候选；资料响应的密码、姓名等不保存或回传。确认同学号后保留旧密码，换号不继承；验证前不触发密码重登，避免使待验证手机会话失效。退出同时清除候选文件。session API 仅返回 token／密码是否存在及入口来源，不返回值。代理只解密配置的学校主机，其他主机走隧道。

static/drawing.js 与 taskgen.hav 使用同一地球半径和球面公式，草稿统计不发学校请求。static/app.js 在增点、撤销、清空和参数变化时同步两个统计位置，正式结果仍由 Routes 生成和校验。完成绘制保留草稿，切换来源才隐藏；Leaflet 和后备 Canvas 行为一致。

## 扩展前验证

先运行标准库 unittest。测试中 requests 被禁用；执行演练还在子进程禁止 socket 连接。使用 `web.py --demo` 检查 UI，不使用个人 data/。新增上传链路应先用 FakeTransport 覆盖，再由用户决定实际验收。

## 单文件 EXE

workbench.storage.ROOT 始终指向只读资源目录：源码根或 PyInstaller _MEIPASS。APP_DIR 指向源码根或实际 EXE 所在目录；EXE 的默认数据是 APP_DIR/云运动用户数据。工作进程继承绝对 YUNRUN_DATA_DIR，不向临时解压目录写个人数据。build_exe.py 采用单文件 console 模式，保留控制台生命周期与 worker/capture-worker 两个子入口。verify_exe.py 在全新中文与空格目录验证资源、快照演练、代理及捕获桥，不连接学校。
