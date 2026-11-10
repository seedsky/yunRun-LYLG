# 共享服务接口速查

HTTP 路由在 workbench/server.py。请求必须带页面启动时获得的 X-Workbench-Token；X-Workbench-Mode 为 online 或 offline。只通过页面的 ui.api 调用，不在业务模块内复制 fetch。

## 路线

`POST /api/routes/generate`

```json
{"source":"synth","km":2,"pace":5.5,"cadence":170,"amp":1,"sample_seconds":2,"seed":"","deform":false}
```

source 可为 synth/library/history/draw。已有和历史来源附 source_id；手绘附 drawn，经纬度数组顺序为 `[经度,纬度]`。pace 单位为分钟/公里；sample_seconds 为 1～5 秒。seed 留空自动选择，固定种子保证复现。

输出包含 id、digest、task_id、metrics、coordinates、paces、times、params、estimated、issues、fence、cards。地图与统计直接使用这份输出，不再次随机生成。

`POST /api/routes/preview` 接收 source_id，或 uploaded 几何/任务对象，以及 params 供无时间轴的几何估算。只读取和规范化副本，不修改源文件。

`POST /api/routes/save` 接收 id/name；复制快照到个人库。`routes/import` 接收 content/name，`routes/export` 接收 source_id/geojson。浏览器不能提交任意磁盘路径。

参数冲突返回 HTTP 422，包含 `issues: [{field,message,suggested?}]`。UI 保留原输入；“采用建议值”只在用户点击后修改控件，并将旧预览标为过期。

## 任务与执行

`POST /api/tasks/select {id}` 只接受当前规则缓存中的任务；`POST /api/rules/refresh {}` 只读获取服务器规则。

`POST /api/jobs/start {id,dry,fast}` 接收快照 ID。dry 默认 true；offline 和 demo 拒绝正式执行。正式执行刷新规则、确认任务与点位匹配后，才启动 worker。

`GET /api/jobs` 返回 status、elapsed、duration、confirmed_m、record_id、digest、logs 等白名单状态。状态包括 idle/starting/running/stopping/completed/stopped/failed/interrupted。

`POST /api/jobs/stop {}` 在 UI 简短确认后调用。不能将 stopped 解释为服务端已撤销，也不能将 completed 解释为最终成绩有效。

## 登录和存储

`POST /api/auth/login {username,password}` 账号密码仅在请求内存和 DPAPI 文件中存在。成功后尝试同步规则，规则失败会返回 warning，避免将登录成功误报成密码失败。

`POST /api/auth/retry {}` 是用户明确触发的新一次尝试；`POST /api/auth/logout {}` 清除本机凭据。状态接口只返回掩码账号，不返回 token、密码或原始登录响应。

## 备份

`backup/export` 返回 version=1 的 yunrun-backup JSON。`backup/inspect {content}` 做格式与路线检查，返回短时导入 id 和重名列表；`backup/import {id,choices}` 执行选择。

choices 中 account/settings/routes 为布尔值，conflicts 是按字符串序号索引的 keep/overwrite/skip。导入中途失败会恢复本次导入前的数据；凭据恢复点仍是 DPAPI 密文，不导出明文密码。

## 扩展规则

新增路线来源仍输出标准快照；新增规则先明确字段单位；新增个人数据先决定能否备份。不要让前端提交 Python 参数列表、shell 命令、任意 URL 或任意路径。
