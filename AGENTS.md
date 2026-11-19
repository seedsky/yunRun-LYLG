# 给后续 AI 的扩展约束

先读 README.md、docs/DEVELOPMENT.md（交接文档）、docs/ARCHITECTURE.md、docs/DECISIONS.md 和 docs/VALIDATION.md。

- 项目是 Windows 本地工具。主入口 web.py；正式用户入口是两个中文启动脚本。
- 网页由 static/index.html、style.css、app.js、map.js、ui.js 构成，无前端构建步骤。
- 修改业务优先进入 workbench/，不要再把 HTML 拼回 Python，也不要另建一套路线生成流程。
- online 与 offline 共用 Routes；offline 不发学校请求。底图可联网，组件必须随包。
- 密码/token 只进内存和用户级 DPAPI 文件；普通配置、日志、源码与发行包不含个人凭据。
- 导出备份是显式白名单：账号/token/设备字段、偏好、规范化路线。不包含 password、CA 私钥、Python 环境。
- 执行只接受已生成快照 ID。必须校验哈希、当前任务、最新规则、打卡点与围栏摘要，禁止改成重新随机选择或再生成。
- 场地（打卡点、围栏）只有一个来源：当前所选任务（`rules.venue`）。不要再用固定文件或内置常量兜底；没有服务器任务时才允许用 `examples/routes/fence_lylg.geojson` 与 `taskgen.CARDS` 作为示例。
- 本地检查（`rules.limits`）只影响本机判断，按任务分别保存；关闭开关时只能降级为提示，必填字段缺失与人脸要求仍然拦截。
- 修改参数或本地检查后，浏览器必须标记预览过期并禁用执行；保存路线不是执行前置条件。
- 有效 token 优先，明确失效时自动登录至多一次；传输失败和上传结果未知都不能自动重放。
- 多任务必须明确选择，不能恢复 cralist[0] 的假定；切任务后旧预览不得演练或执行。
- 用户教程是根目录 `使用说明.md`（纯 Markdown），界面解释用控件 `title` 悬停提示，不再另开网页放文档。
- 演练使用 FakeTransport 并禁止 socket 连接。自动验收禁止创建真实记录或上传轨迹。
- 控制台关闭必须带走子进程。中断任务保留状态，不自动恢复发送。
- 新功能若改变产品行为，先向用户说明；常规修复按已有决策推进。
- 验证命令：runtime\python.exe -X utf8 -m unittest discover -s tests -v。
- 构建发行：runtime\python.exe -X utf8 build_release.py。发布前检查 distribution/release-manifest.json 与 ZIP 排除项。
- 不要读取并回显真实 data/ 中的账号、token、密码或原始登录响应；使用 --demo 和临时目录复现。

本文件不要求委派子代理，也不要求额外审批普通修复。网络验收范围以用户在当前会话的授权为准。
