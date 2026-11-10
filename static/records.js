import {$,api,escape,toast,modal,duration,pace} from './ui.js';
export class RecordsUI{
 constructor(run){
  this.run=run;this.data=null;
  $('records-refresh').onclick=()=>run($('records-refresh'),()=>this.refresh());
  $('record-term').onchange=()=>run($('record-term'),()=>this.refresh($('record-term').value));
  $('record-filter').onchange=()=>this.render();
  $('record-code-settings').onclick=()=>run($('record-code-settings'),()=>this.configure());
 }
 async refresh(term){this.data=await api('/api/records/query',{term});this.render();}
 render(){
  if(!this.data)return;
  $('record-term').replaceChildren(...this.data.terms.map(t=>new Option(t.key,t.value)));$('record-term').value=this.data.table;
  $('records-updated').textContent='查询时间：'+new Date(this.data.fetched_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
  const filter=$('record-filter').value,rows=this.data.records.filter(r=>!filter||r.grade.state===filter);
  $('records-table').replaceChildren();
  for(const r of rows){
   const row=document.createElement('tr');
   const time=r.recordStartTime||r.recordEndTime||r.endTime;
   const timeLabel=r.recordStartTime?'开始时间':r.recordEndTime?'结束时间（服务器）':r.endTime?'列表时间（服务器）':'';
   row.innerHTML=`<td>${escape(time||'待详情确认')}${time?`<br><small class="fine">${timeLabel}</small>`:''}</td><td><span class="grade-badge grade-${r.grade.state}">${escape(r.grade.label)}</span></td><td>${escape(r.recordMileage??'—')} 公里</td><td>${Number(r.duration)>0?duration(r.duration):'待详情确认'}</td><td>${Number(r.recodePace)>0?pace(r.recodePace):'待详情确认'}</td><td><button class="btn secondary">查看详情</button></td>`;
   row.querySelector('button').onclick=e=>this.run(e.currentTarget,()=>this.detail(r));$('records-table').append(row);
  }
  $('records-empty').hidden=rows.length>0;
  $('records-count').textContent=`共 ${this.data.records.length} 条 · 当前显示 ${rows.length} 条`;
 }
 async detail(row){
  const d=await api('/api/records/detail',{id:row.id,table:row.table});Object.assign(row,d);this.render();
  const names={recordStartTime:'开始时间',recordEndTime:'结束时间（服务器）',recordMileage:'里程（公里）',duration:'跑步用时',recodePace:'平均配速',recodeCadence:'平均步频（步/分）',recodeDislikes:'命中点数',isQualified:'合格标记原值',isStandard:'服务器合格标记',isCheat:'服务器异常标记',remark:'服务器说明',remake:'服务器备注／编码',warnContent:'服务器提示',appealStatus:'申诉状态码',msg:'服务器消息',points_count:'轨迹点数',raName:'任务名称'};
  const reason=d.noQualifiedReason?`<p class="modal-description"><b>服务器判定原因：</b>${escape(d.noQualifiedReason)}</p>`:d.grade.state==='failed'?'<p class="fine">服务器未提供具体不达标原因，不能仅凭备注编码推断。</p>':'';
  const html=`<p class="modal-description"><span class="grade-badge grade-${d.grade.state}">${escape(d.grade.label)}</span></p>${reason}<dl class="record-detail">${Object.entries(names).filter(([k])=>d[k]!==undefined).map(([k,n])=>`<dt>${n}</dt><dd>${escape(k==='duration'?(Number(d[k])>0?duration(d[k]):'未提供有效时长'):k==='recodePace'?(Number(d[k])>0?pace(d[k]):'未提供有效配速'):d[k])}</dd>`).join('')}</dl><p class="fine">开始时间、结束时间和用时均按服务器字段分别展示，不相互推算。列表用时为零时，请以详情返回为准。未确认的状态码保留原值。</p>`;
  await modal('跑步记录详情',html,'关闭');
 }
 async configure(){
  const settings=await api('/api/state');const mapping={'1':'passed','2':'failed',...settings.settings.record_status_map};
  const codes=[...new Set(['1','2',...(this.data?.records||[]).map(r=>r.grade.raw).filter(Boolean)])];
  const opts={unknown:'未确认',passed:'有效（达标）',failed:'无效（不达标）',pending:'待审核',running:'进行中'};
  if(!await modal('对照官方 App 设置状态码含义',`<p class="modal-description">仅在你已确认官方 App 对应含义后修改。原始状态码会保留，未知码不会自动判成功。</p>${codes.map((c,i)=>`<label>状态码 ${escape(c)}<select data-status-code="${i}">${Object.entries(opts).map(([v,n])=>`<option value="${v}" ${mapping[c]===v?'selected':''}>${n}</option>`).join('')}</select></label>`).join('')}`,'保存解释'))return;
  const value={};document.querySelectorAll('[data-status-code]').forEach(el=>value[codes[Number(el.dataset.statusCode)]]=el.value);
  await api('/api/settings',{record_status_map:value});if(this.data)await this.refresh(this.data.table);toast('状态码解释已保存，原始返回值未改动。');
 }
}
