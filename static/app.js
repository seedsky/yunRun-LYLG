// 页面编排；业务规则以 Python 服务返回结果为准，浏览器只维护当前编辑状态。
import {$,icons,escape,api,toast,download,modal,pickFile,pace,duration} from './ui.js';
import {RouteMap} from './map.js';
import {renderLogs,renderResult,sections,fields} from './presentation.js';
import {RecordsUI} from './records.js';
import {drawingStats} from './drawing.js';

const boot=window.__BOOT__;
const S={data:null,preview:null,dirty:false,page:'work',filter:'all',busy:false,inputMode:'pace',forceLogin:false,job:{status:'idle'},capture:null};
icons();
const map=new RouteMap(()=>markDirty());
const media=matchMedia('(prefers-color-scheme: dark)');
const titles={work:['路线工作台','调整参数，查看路线，再开始执行。'],library:['路线库','保存满意的路线，下次从这里继续。'],account:['账号与设置','管理本机登录、界面偏好与任务规则。'],backup:['数据备份','选择性迁移账号、设置与本地路线。'],capture:['抓包助手','先检测连通性，再完成手机代理与证书配置。']};
const active=()=>['starting','running','stopping'].includes(S.job.status);
titles.records=['跑步记录','查看当前与历史跑步的时间、用时、成绩和服务器说明。'];
const recordsUI=new RecordsUI(run);

function setTheme(choice){
 document.documentElement.dataset.theme=choice==='system'?(media.matches?'dark':'light'):choice;
 document.querySelectorAll('[data-theme-choice]').forEach(el=>{el.classList.toggle('selected',el.dataset.themeChoice===choice);el.setAttribute('aria-pressed',el.dataset.themeChoice===choice);});
 $('theme-setting').value=choice;map.repaint();if(S.preview)renderChart(S.preview);
}
media.addEventListener('change',()=>{if((S.data?.settings.theme||'system')==='system')setTheme('system');});
async function chooseTheme(choice){
 setTheme(choice);
 try{await api('/api/settings',{theme:choice});if(S.data)S.data.settings.theme=choice;}
 catch(error){toast(error.message);}
}
document.querySelectorAll('[data-theme-choice]').forEach(el=>el.onclick=()=>chooseTheme(el.dataset.themeChoice));
$('theme-setting').onchange=()=>chooseTheme($('theme-setting').value);

function showPage(page){
 S.page=page;document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!=='page-'+page);
 document.querySelectorAll('[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
 $('page-title').textContent=titles[page][0];$('page-subtitle').textContent=titles[page][1];
 const login=page==='work'&&boot.mode==='online'&&(!S.data?.authenticated||S.forceLogin);
 $('login-panel').hidden=!login;if(login)$('page-work').hidden=true;
 if(page==='work')setTimeout(()=>map.fit(),30);
 if(page==='account')loadAdvanced();
 if(page==='capture')loadCapture();
 if(page==='library')renderLibrary();
}
document.querySelectorAll('[data-page]').forEach(el=>el.onclick=()=>showPage(el.dataset.page));
$('account-pill').onclick=()=>showPage('account');
if(boot.mode==='offline'){
 $('mode-label').textContent='独立工具 / 本地路线处理';$('execute').hidden=true;$('refresh-rules').hidden=true;
 document.querySelector('[data-page="capture"]').hidden=true;
 document.querySelector('[data-page="records"]').hidden=true;
 $('check-records').hidden=true;
 $('mode-link').href='/';$('mode-link').querySelector('span:nth-child(2)').textContent='打开联网面板';
 $('login-capture').hidden=true;
 document.querySelector('.controls .with-icon span:last-child').textContent='临时预览无需保存，即可本地演练。';
}
$('demo-badge').hidden=!boot.demo;

async function run(button,operation){
 if(S.busy){toast('正在处理当前操作，请稍候。');return;}
 S.busy=true;button?.classList.add('busy');renderLock();
 try{return await operation();}
 catch(error){if(error.issues)renderIssues(error.issues);toast(error.message);}
 finally{S.busy=false;button?.classList.remove('busy');renderLock();}
}

async function loadState(initial=false){
 const old=S.data;S.data=await api('/api/state');S.job=S.data.job;
 setTheme(S.data.settings.theme||'system');
 $('account-label').textContent=boot.mode==='offline'?'独立工具':(S.data.authenticated?S.data.account:'未登录');
 $('account-pill').querySelector('.status-dot').style.opacity=S.data.authenticated?'1':'.3';
 $('settings-account').textContent=S.data.authenticated?S.data.account:'未登录';
 const session=S.data.session||{};
 $('settings-auth-state').textContent=boot.mode==='offline'?'独立工具不连接学校服务。':(S.data.auth_error||`${session.has_token?'Token 已加密保存':'尚未获得 token'} · ${session.has_password?'登录密码已保存':'未保存登录密码'} · 来源：${{password:'账号密码登录',capture:'手机抓包',existing:'已有会话'}[session.source]||'已有会话'}`);
 $('login-error').textContent=S.data.auth_error||'';$('login-error').hidden=!S.data.auth_error;
 $('login-user').value=S.data.settings.account_name||'';$('max-minutes').value=S.data.settings.max_minutes;
 $('library-count').textContent=S.data.sources.filter(v=>v.kind==='library').length;
 if(initial){
  const p=S.data.preview;
  hydrate(p?{...S.data.settings.defaults,...p.params,km:p.metrics.km,pace:p.metrics.pace,cadence:p.metrics.cadence,
              seed:S.data.settings.defaults.seed||''}:S.data.settings.defaults);
  if(p)renderPreview(p);
 }
 renderRules();fillSources();renderLibrary();renderJob(S.job);showPage(S.page);
 if(old&&S.preview&&JSON.stringify([old.rules.fields,old.rules.points,old.rules.selected_id])!==JSON.stringify([S.data.rules.fields,S.data.rules.points,S.data.rules.selected_id]))markDirty();
 if(!S.data.rules.tasks.length&&S.data.authenticated&&boot.mode==='online')notice('服务器当前没有可用任务。你仍可在独立工具中处理本地路线。');
 else if(boot.demo)notice('本地演示模式：使用模拟任务与本地演练，不连接学校服务，不上传记录。');
 else if(S.job.status==='interrupted')notice('上次任务已中断，不自动续跑。请先读取线上历史记录，核对最后确认的位置。');
 else notice('');
}
function notice(text){$('global-notice').textContent=text;$('global-notice').hidden=!text;}

function hydrate(params={}){
 $('source').value=params.source||'synth';$('km').value=params.km??2;
 setPace(params.pace??5.5);$('cadence').value=params.cadence??170;
 $('amp').value=String(params.amp??1);$('seed').value=params.seed??'';$('interval').value=String(params.sample_seconds??2);
 $('deform').checked=!!params.deform;sourceUI();derived();
 if(params.source_id)$('source-id').value=params.source_id;
 if(params.source==='draw'&&params.drawn){map.drawing=params.drawn;map.updateDraw();renderDrawing();}
}
function setPace(value){let total=Math.round(Number(value)*60);$('pace-min').value=Math.floor(total/60);$('pace-sec').value=total%60;}
function parameters(){
 const km=Number($('km').value),minutes=Number($('pace-min').value),seconds=Number($('pace-sec').value);
 if(seconds<0||seconds>=60)throw new Error('配速的秒数需为 0～59。');
 const value=S.inputMode==='duration'?Number($('duration').value)/km:minutes+seconds/60;
 return {source:$('source').value,source_id:$('source-id').value,km,pace:value,cadence:Number($('cadence').value),
  amp:Number($('amp').value),sample_seconds:Number($('interval').value),seed:$('seed').value,
  deform:$('deform').checked,drawn:map.drawing};
}
function derived(){
 try{const p=parameters();const seconds=p.km*p.pace*60;
  $('derived-label').textContent=S.inputMode==='pace'?'预计时长':'预计平均配速';
  const rounded=Math.round(seconds);
  $('derived-value').textContent=S.inputMode==='pace'?(Number.isFinite(seconds)?`${Math.floor(rounded/60)} 分 ${String(rounded%60).padStart(2,'0')} 秒`:'—'):pace(p.pace);
 }catch{}
 $('fluctuation-label').textContent=Number($('amp').value)?'沿途配速起伏已开启':'沿途配速起伏已关闭';
 renderDrawing();
}
function renderDrawing(){
 let p;try{p=parameters();}catch{p={pace:NaN,km:NaN};}
 const stats=drawingStats(map.drawing,p.pace,p.km),km=stats.meters/1000;
 $('draw-count').textContent=`已绘制 ${stats.points} 个点`;
 $('draw-distance').textContent=`${km.toFixed(3)} 公里`;
 $('draw-time').textContent=stats.seconds===null?'配速无效':duration(stats.seconds);
 $('draw-gap').textContent=`${Math.round(stats.closingGap)} 米`;
 $('draw-live-distance').textContent=`已绘 ${km.toFixed(3)} 公里`;
 $('draw-live-points').textContent=`${stats.points} 个点 · 最近一段 ${Math.round(stats.lastSegment)} 米`;
 $('draw-live-time').textContent=`按当前配速约 ${stats.seconds===null?'—':duration(stats.seconds)}`;
 $('draw-use-distance').disabled=active()||S.busy||stats.meters<1;
 $('draw-target-note').textContent=stats.points<2?'至少添加两个点，地图内会实时累计距离。':stats.remaining===null?'请填写有效目标里程。':Math.abs(stats.remaining)<1?'草稿长度已接近目标；预览时还会进行点位吸附和采样。':stats.remaining>0?`距目标还差 ${(stats.remaining/1000).toFixed(3)} 公里；生成时沿草稿往返补足。`:`比目标多 ${(-stats.remaining/1000).toFixed(3)} 公里；生成时按目标截取。`;
}
function markDirty(){if(S.preview){S.dirty=true;$('dirty-notice').hidden=false;$('preview-tag').textContent='参数待更新';}derived();renderLock();}
$('parameter-fields').addEventListener('input',event=>{if(event.target.id!=='fast')markDirty();});
$('parameter-fields').addEventListener('change',event=>{if(event.target.id!=='fast')markDirty();});
$('input-mode').onclick=()=>{
 if(S.inputMode==='pace'){$('duration').value=(Number($('km').value)*(Number($('pace-min').value)+Number($('pace-sec').value)/60)).toFixed(2);S.inputMode='duration';}
 else{setPace(Number($('duration').value)/Number($('km').value));S.inputMode='pace';}
 $('pace-group').hidden=S.inputMode!=='pace';$('duration-group').hidden=S.inputMode!=='duration';
 $('input-mode').textContent=S.inputMode==='pace'?'改用时长填写':'改用配速填写';markDirty();
};
function fillSources(){
 if(!S.data)return;
 const select=$('source-id'),previous=select.value;
 const chosen=$('source').value;
 const items=S.data.sources.filter(item=>chosen==='history'?item.kind==='history':item.kind!=='history');
 select.replaceChildren(...items.map(item=>new Option(item.name,item.id)));
 if(items.some(item=>item.id===previous))select.value=previous;
}
function sourceUI(){
 const source=$('source').value;
 $('source-options').hidden=!['library','history'].includes(source);$('fetch-history').hidden=source!=='history'||boot.mode==='offline';
 $('draw-hint').hidden=source!=='draw';$('source-tag').textContent={synth:'随机生成',library:'已有路线',history:'历史记录',draw:'手绘路线'}[source];
 $('draw-live').hidden=source!=='draw';renderDrawing();
 $('map-empty').hidden=source==='draw'||!!S.preview;
 map.setDrawing(source==='draw'&&!active()&&!S.busy);fillSources();renderLock();
}
$('source').onchange=sourceUI;
$('fit-map').onclick=()=>map.fit();$('undo-draw').onclick=()=>map.undo();$('clear-draw').onclick=()=>map.clear();
$('show-preview').onchange=()=>map.showRoute($('show-preview').checked);
$('blank-map').onclick=()=>{$('show-preview').checked=false;map.showRoute(false);toast('已隐藏参考路线，保留地图与手绘草稿；勾选“参考路线”即可恢复。');};
$('finish-draw').onclick=()=>{map.setDrawing(false);toast('绘制已完成，点击“生成并预览”应用当前参数。');};
$('resume-draw').onclick=()=>{map.setDrawing(true);toast('继续点击地图添加点，或使用撤销、清空。');};
$('draw-use-distance').onclick=()=>{
 const km=drawingStats(map.drawing,0,0).meters/1000;if(km<=0)return;
 $('km').value=km.toFixed(3);markDirty();toast('目标里程已填写为草稿长度；生成时仍需通过规则校验。');
};

function renderIssues(issues=[]){
 $('route-errors').replaceChildren();$('route-errors').hidden=!issues.length;
 issues.forEach(issue=>{
  const row=document.createElement('div');const text=document.createElement('div');text.textContent=issue.message;row.append(text);
  if(Number.isFinite(issue.suggested)){
   const button=document.createElement('button');button.className='text-button';button.textContent='采用建议值';
   button.onclick=()=>{if(issue.field==='pace'){setPace(issue.suggested);if(S.inputMode==='duration')$('duration').value=(Number($('km').value)*issue.suggested).toFixed(2);}else if($(issue.field))$(issue.field).value=issue.suggested;markDirty();};row.append(button);
  }
  $('route-errors').append(row);
 });
}
function renderPreview(preview){
 $('show-preview').checked=true;map.showRoute(true);
 S.preview=preview;S.dirty=false;$('dirty-notice').hidden=true;
 const m=preview.metrics;$('metric-km').innerHTML=`${m.km.toFixed(2)}<small>km</small>`;
 $('metric-pace').textContent=pace(m.pace);$('metric-duration').textContent=duration(m.duration);$('metric-cadence').textContent=Math.round(m.cadence);
 $('preview-tag').textContent=preview.estimated?'节奏为本地估算':'临时预览';
 $('point-summary').textContent=`${m.points} 个轨迹点 · 命中 ${m.cards} 个打卡点`;
 $('map-empty').hidden=true;map.preview(preview);renderChart(preview);renderIssues(preview.issues||[]);renderLock();
}
function renderChart(preview){
 const raw=preview.paces.filter(v=>Number.isFinite(v)&&v>0),values=raw.filter((_,i)=>i%Math.max(1,Math.floor(raw.length/180))===0);
 if(values.length<2)return;
 const min=Math.max(0,Math.min(...values)-.12),max=Math.max(...values)+.12;
 const points=values.map((value,i)=>[42+i/(values.length-1)*840,15+(value-min)/(max-min)*78]);
 const path=points.map((v,i)=>`${i?'L':'M'}${v[0].toFixed(1)},${v[1].toFixed(1)}`).join(' ');
 const ticks=[0,.5,1].map(f=>{const y=15+f*78;return `<line x1="42" y1="${y}" x2="882" y2="${y}" stroke="var(--line)" stroke-dasharray="4 4"/><text x="1" y="${y+3}">${pace(min+f*(max-min))}</text>`;}).join('');
 $('pace-chart').innerHTML=`<svg viewBox="0 0 900 120" role="img" aria-label="当前预览的逐点配速变化"><title>配速变化，平均 ${pace(preview.metrics.pace)}</title><defs><linearGradient id="pace-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="var(--accent)" stop-opacity=".18"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>${ticks}<path d="${path} L882,94 L42,94Z" fill="url(#pace-fill)"/><path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/><text x="42" y="116">0.0 km</text><text x="442" y="116">${(preview.metrics.km/2).toFixed(1)} km</text><text x="850" y="116">${preview.metrics.km.toFixed(1)} km</text></svg>`;
 $('chart-summary').textContent=`平均 ${pace(preview.metrics.pace)} / 公里`;
}
$('generate').onclick=()=>run($('generate'),async()=>{const result=await api('/api/routes/generate',parameters());renderPreview(result);toast('路线已生成，可直接演练或执行，无需先保存。');});
async function onlyPreview(sourceId){
 const p=parameters();
 if(!sourceId&&p.source==='synth'){
  if(!S.preview)throw new Error('请先生成路线，或切换到“已有路线”选择文件。');
  map.fit();toast(S.dirty?'当前显示上次预览；修改的参数需重新生成才能应用。':'当前显示的是这条临时路线，未重新生成。');return;
 }
 const request=p.source==='draw'&&!sourceId?{uploaded:{type:'LineString',coordinates:map.drawing},params:p}:{source_id:sourceId||p.source_id,params:p};
 const result=await api('/api/routes/preview',request);
 $('km').value=result.metrics.km.toFixed(3);setPace(result.metrics.pace);$('cadence').value=Math.round(result.metrics.cadence);
 if(S.inputMode==='duration')$('duration').value=(result.metrics.duration/60).toFixed(2);
 derived();renderPreview(result);toast('已读取原路线预览，原文件未修改。');
}
$('preview-only').onclick=()=>run($('preview-only'),()=>onlyPreview());
$('save-route').onclick=()=>run($('save-route'),async()=>{
 const yes=await modal('保存到路线库','<label>路线名称<input id="route-name" maxlength="100" placeholder="例如：校园晚跑 2 公里"></label>','保存路线');
 if(!yes)return;
 const result=await api('/api/routes/save',{id:S.preview.id,name:$('route-name').value});await loadState();toast(`已保存：${result.name}`);
});
$('export-preview').onclick=()=>run($('export-preview'),async()=>download(await api('/api/previews/export',{id:S.preview.id,geojson:true}),'当前预览.geojson'));

function renderRules(){
 if(!S.data)return;const r=S.data.rules;const selected=r.selected_id;
 $('task-select').replaceChildren(...(r.tasks.length?(r.tasks.length>1?[new Option('请选择任务',''),...r.tasks.map(t=>new Option(t.name+' · '+t.type,t.id))]:r.tasks.map(t=>new Option(t.name,t.id))):[new Option('本地参考规则','')]));
 $('task-select').value=selected;$('task-select').disabled=active()||r.tasks.length<=1;
 let stamp='本地配置 · 尚未同步';if(r.fetched_at){const time=new Date(r.fetched_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});stamp=`${boot.demo?'演示数据':(boot.mode==='offline'?'缓存':'服务器')} · ${time}`;}
 $('rule-stamp').textContent=stamp;map.guide(r.points);
 const fields=[['km_min','里程下限','公里'],['km_max','里程上限','公里'],['pace_min','配速下限','分钟/公里'],['pace_max','配速上限','分钟/公里'],['cadence_min','步频下限','步/分'],['cadence_max','步频上限','步/分'],['start','每日开始时间',''],['end','每日结束时间',''],['max_minutes','单次时长上限','分钟'],['face','人脸核验开关',''],['batch','上传批次阈值','']];
 $('rules-grid').innerHTML=fields.map(([key,name,unit])=>{const v=r.fields[key];return `<div class="rule-cell"><small>${name}</small><strong>${escape(v.value??'未确认')} <small style="display:inline">${unit}</small></strong><span class="tag">${escape(v.source)}</span></div>`;}).join('');
}
$('task-select').onchange=()=>run($('task-select'),async()=>{await api('/api/tasks/select',{id:$('task-select').value});markDirty();await loadState();});
async function refreshRules(){await api('/api/rules/refresh',{});await loadState();toast('已获取当前服务器规则。');}
$('refresh-rules').onclick=()=>run($('refresh-rules'),refreshRules);$('settings-refresh').onclick=()=>run($('settings-refresh'),refreshRules);

const statusLabels={idle:'尚未执行',starting:'准备中',running:'执行中',stopping:'正在停止',completed:'已完成',stopped:'已停止',failed:'执行失败',interrupted:'上次已中断'};
function renderJob(job){
 S.job=job;$('job-badge').textContent=statusLabels[job.status]||job.status;
 renderLogs(job);
 $('stop-job').hidden=!active();$('run-progress').hidden=!active()&&job.status!=='interrupted';
 const ratio=job.status==='completed'?100:Math.min(99,Math.round((job.elapsed||0)/Math.max(1,job.duration||1)*100));
 $('progress').value=ratio;$('run-percent').textContent=ratio+'%';
 $('run-stage').textContent=`已用时 ${duration(job.elapsed||0)} / 预计 ${duration(job.duration||0)} · 最后确认 ${((job.confirmed_m||0)/1000).toFixed(2)} 公里`;
 renderLock();
}
function renderLock(){
 const locked=active()||S.busy;
 $('parameter-fields').disabled=locked;$('generate').disabled=locked;$('preview-only').disabled=locked;
 $('save-route').disabled=locked||!S.preview||S.dirty;$('export-preview').disabled=!S.preview;
 const usable=!!S.preview&&!S.dirty&&!S.preview.issues?.length;
 $('drill').disabled=locked||!usable;
 $('execute').disabled=locked||!usable||!S.data?.authenticated||!S.data?.rules.selected_id||boot.demo;
 $('stop-job').disabled=S.busy||S.job.status==='stopping';
 $('task-select').disabled=locked||(S.data?.rules.tasks.length||0)<=1;
 $('draw-use-distance').disabled=locked||drawingStats(map.drawing,0,0).meters<1;
 ['undo-draw','clear-draw','finish-draw','resume-draw'].forEach(id=>$(id).disabled=locked);
 if(locked)map.setDrawing(false);
 const other=!!(S.job.preview_id&&S.preview&&S.job.preview_id!==S.preview.id);
 $('job-badge').textContent=(other?'上次 · ':'')+(statusLabels[S.job.status]||S.job.status);
 $('log-relation').textContent=other?'以下日志属于上一次执行，不代表当前预览已执行。':'中断后保留最后确认位置，不自动续跑。';
 renderResult(S.job,other);
 if(active()){$('execution-label').textContent=S.job.dry?'本地演练中':'任务执行中';$('execution-detail').textContent='当前路线与参数已锁定，关闭网页仍会继续运行。';map.setDrawing(false);}
 else if(S.dirty){$('execution-label').textContent='等待更新预览';$('execution-detail').textContent='参数已修改，请重新生成预览；无需保存路线。';}
 else if(S.preview){$('execution-label').textContent=S.preview.issues?.length?'路线需要调整':'路线已就绪';$('execution-detail').textContent=boot.mode==='offline'?'可进行本地演练，正式执行请打开联网面板。':(boot.demo?'演示模式只支持本地演练。':'本地演练可选，临时路线可直接正式执行。');}
 else{$('execution-label').textContent='等待路线预览';$('execution-detail').textContent='生成或选择一条路线后，即可演练或执行。';}
}
async function startJob(dry){const job=await api('/api/jobs/start',{id:S.preview.id,dry,fast:$('fast').checked});renderJob(job);$('logs').open=true;}
$('drill').onclick=()=>run($('drill'),()=>startJob(true));$('execute').onclick=()=>run($('execute'),()=>startJob(false));
$('stop-job').onclick=()=>run($('stop-job'),async()=>{
 if(await modal('停止当前任务？','<p class="modal-description">停止后不会继续发送，但正在进行的请求可能已经到达服务器，已发送的数据不会自动撤销。</p>','确认停止'))renderJob(await api('/api/jobs/stop',{}));
});
$('export-log').onclick=()=>download(S.job,'执行日志-脱敏.json');
$('check-records').onclick=()=>{showPage('records');run($('records-refresh'),()=>recordsUI.refresh());};

function renderLibrary(){
 if(!S.data)return;
 const items=S.data.sources.filter(item=>S.filter==='all'||item.kind===S.filter),container=$('route-list');container.replaceChildren();
 if(!items.length){container.innerHTML='<div class="route-empty">这里还没有路线。生成满意的结果后，点击“保存路线”。</div>';return;}
 items.forEach(item=>{
  const row=document.createElement('div');row.className='route-row';
  row.innerHTML=`<span class="route-avatar" data-icon="route"></span><div><div class="route-name">${escape(item.name)}</div><div class="route-meta">${{sample:'内置示例',library:'已保存路线',history:'已下载历史记录'}[item.kind]} · ${item.points} 个轨迹点</div></div><div class="route-buttons"><button class="btn secondary" data-action="preview">预览</button><button class="icon-button" data-action="export" title="导出 GeoJSON" aria-label="导出路线"><span data-icon="download"></span></button>${item.kind!=='sample'?'<button class="text-button" data-action="delete">删除</button>':''}</div>`;
  row.querySelector('[data-action=preview]').onclick=event=>run(event.currentTarget,async()=>{$('source').value=item.kind==='history'?'history':'library';sourceUI();$('source-id').value=item.id;await onlyPreview(item.id);showPage('work');});
  row.querySelector('[data-action=export]').onclick=event=>run(event.currentTarget,async()=>download(await api('/api/routes/export',{source_id:item.id,geojson:true}),item.name+'.geojson'));
  row.querySelector('[data-action=delete]')?.addEventListener('click',event=>run(event.currentTarget,async()=>{if(await modal('删除这条路线？',`<p class="modal-description">${escape(item.name)} 将移至本机 data/trash，内置示例不受影响。</p>`,'删除')){await api('/api/routes/delete',{id:item.id});await loadState();}}));
  container.append(row);
 });icons(container);
}
document.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{S.filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('active',el===button));renderLibrary();});
async function importRoute(){const file=await pickFile();if(!file)return;const result=await api('/api/routes/import',file);await loadState();toast('已导入：'+result.name);}
$('import-route').onclick=()=>run($('import-route'),importRoute);$('library-import').onclick=()=>run($('library-import'),importRoute);
$('fetch-history').onclick=()=>run($('fetch-history'),async()=>{const result=await api('/api/history/pull',{limit:20});await loadState();toast(`已读取 ${result.count} 条历史路线。`);});

$('login-form').onsubmit=event=>{event.preventDefault();run($('login-form').querySelector('[type=submit]'),async()=>{
 $('login-error').hidden=true;
 try{const result=await api('/api/auth/login',{username:$('login-user').value,password:$('login-password').value});$('login-password').value='';S.forceLogin=false;await loadState();toast(result.warning?'已获得并保存 token，规则同步需要重试：'+result.warning:'登录成功，已获得并加密保存 token，任务已同步。');}
 catch(error){$('login-error').textContent=error.message;$('login-error').hidden=false;throw error;}
});};
$('password-toggle').onclick=()=>{const input=$('login-password');input.type=input.type==='password'?'text':'password';$('password-toggle').setAttribute('aria-label',input.type==='password'?'显示密码':'隐藏密码');};
$('retry-login').onclick=()=>run($('retry-login'),async()=>{if(S.forceLogin&&S.data?.authenticated){S.forceLogin=false;showPage('work');return;}await api('/api/auth/retry',{});await loadState();});
$('login-capture').onclick=()=>showPage('capture');
$('settings-login').onclick=()=>{if(boot.mode==='offline'){location.href='/';return;}S.forceLogin=true;$('retry-login').textContent=S.data.authenticated?'返回当前账号':'重试自动登录';showPage('work');};
$('logout').onclick=()=>run($('logout'),async()=>{await api('/api/auth/logout',{});S.forceLogin=false;await loadState();showPage('work');toast('已清除本机密码和 token，路线与设置保留。');});
$('save-preferences').onclick=()=>run($('save-preferences'),async()=>{await api('/api/settings',{max_minutes:Number($('max-minutes').value)});await loadState();markDirty();toast('本地设置已保存。');});
async function loadAdvanced(){
 try{const config=await api('/api/advanced');const box=$('advanced-config');box.replaceChildren();
  Object.entries(config).forEach(([section,items])=>{
   const block=document.createElement('section');block.innerHTML=`<h3>${escape(sections[section]||'扩展设置')}</h3><div class="advanced-grid"></div>`;
   Object.entries(items).forEach(([key,item])=>{
    const label=document.createElement('label');label.textContent=(fields[key]||['扩展参数'])[0];
    const explanation=document.createElement('span');explanation.className='fine';explanation.textContent=(fields[key]?.[1]||'扩展兼容配置。')+'（字段：'+key+'）';label.append(explanation);
    const input=document.createElement('input');input.dataset.section=section;input.dataset.key=key;input.dataset.hidden=String(item.hidden);
    input.value=item.value;input.placeholder=item.hidden?(item.configured?'已配置，留空保留':'尚未配置'):'';if(item.hidden)input.type='password';
    label.append(input);block.querySelector('.advanced-grid').append(label);
   });box.append(block);
  });
 }catch(error){toast(error.message);}
}
$('save-advanced').onclick=()=>run($('save-advanced'),async()=>{
 const obj={};document.querySelectorAll('#advanced-config input').forEach(input=>{
  (obj[input.dataset.section]??={})[input.dataset.key]=input.dataset.hidden==='true'&&!input.value?'__KEEP__':input.value;
 });await api('/api/advanced/save',obj);toast('高级配置已保存，下次请求使用新配置。');
});

$('export-backup').onclick=()=>run($('export-backup'),async()=>download(await api('/api/backup/export',{}),'云运动备份-'+new Date().toLocaleDateString('sv-SE')+'.json'));
$('import-backup').onclick=()=>run($('import-backup'),async()=>{
 const file=await pickFile();if(!file)return;const preview=await api('/api/backup/inspect',{content:file.content});
 const html=`<p class="modal-description">备份账号：${escape(preview.account)}${preview.has_token?' · 包含 token':''}。不会导入登录密码。</p><div class="row wrap"><label class="checkbox"><input id="restore-account" type="checkbox" ${preview.has_token?'checked':'disabled'}>账号与 token</label><label class="checkbox"><input id="restore-settings" type="checkbox" checked>个人设置</label><label class="checkbox"><input id="restore-routes" type="checkbox" checked>本地路线</label></div><div class="modal-routes">${preview.routes.map(item=>`<div class="modal-conflict"><span>${escape(item.name)}${item.conflict?' <span class="tag">同名</span>':''}</span><select data-restore-index="${item.index}" aria-label="${escape(item.name)} 的导入方式"><option value="keep">${item.conflict?'保留两份，自动改名':'导入'}</option>${item.conflict?'<option value="overwrite">覆盖已有路线</option>':''}<option value="skip">跳过</option></select></div>`).join('')}</div>`;
 if(!await modal('选择要恢复的数据',html,'导入所选内容'))return;
 const choices={account:$('restore-account').checked,settings:$('restore-settings').checked,routes:$('restore-routes').checked,conflicts:{}};
 document.querySelectorAll('[data-restore-index]').forEach(input=>choices.conflicts[input.dataset.restoreIndex]=input.value);
 const result=await api('/api/backup/import',{id:preview.id,choices});await loadState();toast(`导入完成，恢复 ${result.imported_routes} 条路线。`);
});

async function loadCapture(){
 if(boot.mode==='offline')return;
 if(boot.demo){$('capture-status').textContent='演示模式';$('network-status').textContent='实际连接检测、证书生成与抓包需使用正常启动入口。';$('capture-start').disabled=true;$('capture-complete').disabled=true;return;}
 try{S.capture=await api('/api/capture');renderCapture();}catch(error){toast(error.message);}
}
function renderCapture(){
 const c=S.capture;if(!c)return;const select=$('capture-address'),previous=select.value;
 select.replaceChildren(...c.addresses.map(address=>new Option(address,address)));
 if(c.addresses.includes(previous))select.value=previous;
 const address=select.value||'请先连接可用网络';
 const url=`http://${address}:${c.gateway_port}${c.prefix}`;
 $('capture-url').textContent=url;$('capture-url').href=url;
 $('apk-url').href=url+'/apk';$('certificate-url').href=url+'/certificate';
 $('proxy-values').textContent=`电脑地址：${address}　 SOCKS5：${c.socks_port}　 HTTP：${c.http_port}`;
 $('network-status').textContent=c.network_seen?'✓ 已检测到设备访问电脑，请确认来自你的手机。':'等待手机打开测试页；扫描二维码后观察此处状态。';
 $('capture-status').textContent={idle:'尚未启动',waiting:'等待捕获',captured:'捕获成功',failed:'代理已退出',stopped:'已停止'}[c.phase]||c.phase;
 $('capture-start').disabled=!c.network_seen||c.phase==='waiting'||active()||S.busy;
 $('token-capture-status').textContent=c.phase==='captured'?'✓ 已捕获候选 token，代理已停止。请关闭手机代理，再验证账号归属；验证前保留当前账号。':'打开官方 App 进入首页，等待目标请求。捕获的候选会话先加密隔离保存，验证后才启用。';
 $('capture-complete').disabled=c.phase!=='captured';
 if(window.qrcode&&c.addresses.length){const qr=qrcode(0,'M');qr.addData(url);qr.make();$('capture-qr').innerHTML=qr.createSvgTag({scalable:true,margin:0});}
 else $('capture-qr').textContent=c.addresses.length?'请手动打开测试地址':'未发现可用地址';
}
$('capture-address').onchange=renderCapture;
$('capture-start').onclick=()=>run($('capture-start'),async()=>{S.capture=await api('/api/capture/start',{});renderCapture();toast('代理已启动，证书首次生成可能需要几秒。');});
$('capture-stop').onclick=()=>run($('capture-stop'),async()=>{S.capture=await api('/api/capture/stop',{});renderCapture();});
$('capture-complete').onclick=()=>run($('capture-complete'),async()=>{const result=await api('/api/capture/complete',{});await loadState();showPage('work');toast(result.password_retained?'抓包会话验证成功，同一账号的登录密码已保留。':'抓包会话验证成功；当前账号未保存登录密码。');});

setInterval(async()=>{
 try{const was=active();const job=await api('/api/jobs');renderJob(job);if(was&&!active())toast(job.status==='completed'?'任务流程已完成，请查看执行日志。':'任务已停止，请查看日志并核对记录。');}catch{}
},1200);
setInterval(()=>{if(S.page==='capture'&&!boot.demo)loadCapture();},2200);

try{await loadState(true);if(boot.capture)showPage('capture');}
catch(error){notice('读取本地状态失败：'+error.message);$('account-label').textContent='读取失败';}
