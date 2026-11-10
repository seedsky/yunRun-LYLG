// 小型公共 UI 层。服务端消息与用户文件名始终作为文本处理。
export const $ = id => document.getElementById(id);
const paths = {
 route:'M4 17a3 3 0 1 0 0 .1M20 7a3 3 0 1 0 0 .1M7 17h7a4 4 0 0 0 0-8H9a3 3 0 0 1 0-6h7',
 library:'m3 7 9-4 9 4-9 4-9-4Zm0 5 9 4 9-4M3 17l9 4 9-4',
 user:'M20 21v-2a6 6 0 0 0-12 0v2M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
 backup:'M3 7c0-4 18-4 18 0s-18 4-18 0Zm0 0v10c0 4 18 4 18 0V7M3 12c0 4 18 4 18 0',
 signal:'M4 20v-3m5 3v-7m5 7V9m5 11V4',
 sun:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5',
 monitor:'M3 4h18v13H3V4Zm5 17h8m-4-4v4',
 moon:'M21 13a9 9 0 1 1-10-10 7 7 0 0 0 10 10Z',
 tool:'M15 6a5 5 0 0 0-6 6L3 18a2 2 0 0 0 3 3l6-6a5 5 0 0 0 6-6l-3 3-3-3 3-3Z',
 refresh:'M20 7V3l-4 4a8 8 0 1 0 4 9M20 3h-5',
 pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Zm-5 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
 timer:'M9 2h6m-3 0v3M19 5l2 2M12 9v5l3 2M21 14a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
 clock:'M12 8v5l4 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
 steps:'M7 3c-3 0-4 4-3 7s5 3 6 1 0-8-3-8Zm11 6c3 0 4 4 3 7s-5 3-6 1 0-8 3-8ZM5 15v4a2 2 0 0 0 4 0v-3m7 4v1h4v-1',
 chart:'M2 13h5l3-9 4 16 3-8h5',
 sliders:'M5 3v6m0 4v8m7-18v12m0 4v2m7-18v2m0 4v12M2 9h6m1 6h6m1-10h6',
 eye:'M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
 bookmark:'M5 3h14v19l-7-5-7 5V3Z',
 info:'M12 11v6m0-10v.1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
 spark:'m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3 3-6Z',
 check:'m8 12 3 3 6-7M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
 play:'m8 4 12 8-12 8V4Z',
 terminal:'m5 7 5 5-5 5m8 0h6M2 3h20v18H2V3Z',
 expand:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
 download:'M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5',
 upload:'M12 16V4m-5 5 5-5 5 5M3 16v5h18v-5',
 plus:'M12 4v16M4 12h16',
 pen:'m4 16 12-12 4 4L8 20H4v-4Zm10-10 4 4',
 shield:'m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6l9-4Zm-4 10 3 3 5-6'
};
export function icons(root=document) {
 root.querySelectorAll('[data-icon]').forEach(el => {
  const path=paths[el.dataset.icon] || paths.info;
  el.innerHTML=`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
 });
}
export function escape(value) { return String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
export async function api(path, body) {
 const response=await fetch(path, {method:body===undefined?'GET':'POST',
  headers:{'Content-Type':'application/json','X-Workbench-Token':window.__BOOT__.token,'X-Workbench-Mode':window.__BOOT__.mode},
  ...(body===undefined?{}:{body:JSON.stringify(body)})});
 const value=await response.json();
 if(!response.ok){const error=new Error(value.error||'操作失败');error.issues=value.issues;throw error;}
 return value;
}
let toastTimer;
export function toast(message) {
 $('toast').textContent=message;$('toast').hidden=false;
 clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);
}
export function download(value,name) {
 const blob=new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'});
 const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=name;
 link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function modal(title,html,ok='确认') {
 $('modal-title').textContent=title;$('modal-body').innerHTML=html;$('modal-ok').textContent=ok;
 const dialog=$('modal');dialog.returnValue='cancel';dialog.showModal();
 return new Promise(resolve=>{
  dialog.addEventListener('cancel',()=>dialog.returnValue='cancel',{once:true});
  dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true});
 });
}
export async function pickFile() {
 const picker=$('file-picker');picker.value='';
 return new Promise(resolve=>{
  picker.onchange=async()=>{
   const file=picker.files[0];if(!file)return resolve(null);
   if(file.size>50*1024*1024){toast('文件超过 50 MB。');return resolve(null);}
   try{resolve({name:file.name.replace(/\.(geojson|json)$/i,''),content:JSON.parse(await file.text())});}
   catch{toast('文件不是有效的 JSON / GeoJSON。');resolve(null);}
  };
  picker.oncancel=()=>resolve(null);picker.click();
 });
}
export function pace(value) {const total=Math.round(Number(value)*60);return `${Math.floor(total/60)}′${String(total%60).padStart(2,'0')}″`;}
export function duration(value) {const total=Math.round(Number(value));return `${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`;}

