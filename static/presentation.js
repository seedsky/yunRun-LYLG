import {$,escape} from './ui.js';
export const sections={Yun:'学校与通信协议',User:'设备与系统信息',Run:'兼容执行参数',Panel:'工作台兼容设置'};
export const fields={yun_host:['默认服务地址','学校业务服务的备用地址。'],school_host:['学校服务地址','登录、任务和跑步记录使用的服务器。'],publickey:['协议公钥','用于封装请求密钥，通常无需修改。'],privatekey:['兼容协议私钥','仅供旧协议兼容，不是账号密码。'],cipherkey:['固定加密密钥','旧登录链路的对称密钥，通常无需修改。'],cipherkeyencrypted:['已封装的加密密钥','与固定加密密钥配套的协议参数。'],md5key:['请求签名参数','用于请求签名，通常无需修改。'],platform:['客户端平台','android 表示安卓客户端。'],app_edition:['客户端版本','与当前官方 App 协议版本保持一致。'],school_login_url:['学校登录入口','登录接口的末段名称，例如 appLogin。'],school_id:['学校编号','洛阳理工学院当前配置为 197。'],school_name:['学校名称','用于展示和识别学校。'],device_id:['设备标识','登录与请求使用的设备身份。'],device_name:['设备名称','请求中报告的设备名称。'],map_key:['地图服务密钥','旧版路线功能的兼容项。'],utc:['请求时间戳','新工作台自动生成，无需手填。'],uuid:['请求唯一标识','新工作台自动生成，无需手填。'],sign:['请求签名','新工作台自动计算，无需手填。'],sys_edition:['安卓系统版本','兼容请求体中的系统版本信息。'],sys_version:['请求头系统版本','未指定时沿用安卓系统版本。'],legacy_uuid:['旧版固定标识开关','仅用于兼容旧调用方。'],exclude_points:['排除的点位','旧版引擎的排除点配置。'],min_distance:['旧版目标里程','仅供兼容 CLI；新工作台使用当前路线参数。'],allow_overflow_distance:['旧版里程容差','保留旧版兼容行为。'],single_mileage_min_offset:['旧版里程下限偏移','新工作台不使用此偏移收缩服务器范围。'],single_mileage_max_offset:['旧版里程上限偏移','新工作台直接采用服务器范围。'],cadence_min_offset:['旧版步频下限偏移','新工作台直接采用服务器范围。'],cadence_max_offset:['旧版步频上限偏移','新工作台直接采用服务器范围。'],split_count:['备用上传批次参数','服务器没有下发批次阈值时的兼容值。'],min_consume:['旧版最低配速','新工作台使用当前路线参数。'],max_consume:['旧版最高配速','新工作台使用当前路线参数。'],strides:['备用步幅（米）','仅用于缺少累计步数的兼容数据。'],max_minutes:['本地时长上限（分钟）','本地补充限制，不代表服务器下发值。'],campuses:['可见校区目录','旧版校区白名单。'],hide:['隐藏目录','旧版校区黑名单。'],distance_m:['默认距离（米）','旧版工作台兼容值。'],fence_geojson:['围栏文件','校内允许范围文件。'],cards_from:['打卡点来源文件','旧版兼容点位来源；新工作台优先服务器。'],pace_amp:['默认配速起伏','旧版工作台兼容值。'],fence_geojson:['围栏文件','校内允许范围。']};
const levelNames={info:'信息',success:'成功',warning:'注意',error:'错误'};
function human(value){return String(value||'').replace(/\bdry-run\b/g,'本地模拟').replace(/ValueError:/g,'参数错误：').replace(/BusinessException:/g,'服务器业务错误：').replace(/TransportOutcomeUnknown:/g,'网络传输结果未知：').replace(/FaceRunStopError:/g,'服务器校验未通过：');}
export function renderLogs(job){
 const entries=(job.logs||[]).map(message=>(job.log_entries||[]).find(e=>e.message===message)||({message,level:/失败|异常|错误/.test(message)?'error':/成功|通过/.test(message)?'success':/中断|停止|未知/.test(message)?'warning':'info'}));
 $('log-output').innerHTML=entries.length?entries.map(e=>{
  const level=levelNames[e.level]?e.level:'info',time=e.time?new Date(e.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  return `<div class="log-entry log-${level}"><time>${escape(time)}</time><b>${levelNames[level]}</b><span>${escape(human(e.message))}</span></div>`;
 }).join(''):'暂无执行日志。';
}
export function renderResult(job,other=false){
 const box=$('job-result');box.hidden=!['completed','failed','stopped','interrupted'].includes(job.status);
 if(box.hidden)return;
 const submit=job.submission||{},grade=job.grade||{};
 const success=job.status==='completed';box.dataset.state=success?'success':job.status==='failed'?'error':'warning';
 $('result-title').textContent=(other?'上次任务：':'')+(job.dry?(success?'本地演练成功':'本地演练未完成'):(submit.state==='accepted'?'提交成功，服务器已受理':success?'执行流程已结束，旧版未保存提交判定':job.status==='failed'?'执行失败，提交未确认':'任务已中断或停止'));
 $('result-grade').textContent=job.dry?'演练成绩为模拟结果，不代表真实成绩。':`服务器预检成绩：${grade.label||'未确认'}。${grade.message||''} 最终成绩可在跑步记录中核对。`;
 $('result-message').textContent=human(submit.message?`服务器返回：${submit.message}（业务码 ${submit.code}）`:(job.logs||[]).at(-1)||'');
}
