import {$} from './ui.js';
// Leaflet 随包分发。只有底图需要网络，地图组件和绘制在断网时仍可使用。
// 围栏与打卡点来自当前所选任务（服务器下发），切换任务时整块场地一起换。
export class RouteMap {
 constructor(onDraw) {
  this.onDraw=onDraw;this.drawMode=false;this.drawing=[];this.current=null;this.cards=[];
  this.showPreview=true;this.fence=[];this.venueTask=undefined;this.fenceLayer=null;
  this.userView=false;this.autoMoving=false;this.maskOn=true;
  if(window.L){
   this.map=L.map('map',{preferCanvas:true,zoomControl:false}).setView([34.613,112.419],16);
   L.control.zoom({position:'topright'}).addTo(this.map);
   this.tiles=L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    {subdomains:['1','2','3','4'],maxZoom:19,attribution:'© 高德地图 · GCJ-02'}).addTo(this.map);
   let failed=0;
   this.tiles.on('tileerror',()=>{if(++failed>=2)$('map-offline').hidden=false;});
   this.tiles.on('tileload',()=>{$('map-offline').hidden=true;});
   this.map.on('click',event=>{if(this.drawMode)this.add([event.latlng.lng,event.latlng.lat]);});
   // 用户自己缩放/拖动过，就不再自动抢视野；只有切任务或点“适应路线”才重新框。
   this.map.on('zoomstart',()=>{if(!this.autoMoving)this.userView=true;});
   this.map.on('dragstart',()=>{this.userView=true;});
   this.map.on('moveend',()=>{this.autoMoving=false;});
   this.markers=L.layerGroup().addTo(this.map);
  }else{
   $('map').hidden=true;$('fallback-map').hidden=false;$('map-offline').hidden=false;
   this.canvas=$('fallback-map');
   this.canvas.addEventListener('click',event=>{
    if(!this.drawMode)return;
    const box=this.canvas.getBoundingClientRect();const b=this.bounds();
    this.add([b.minX+(event.clientX-box.left)/box.width*(b.maxX-b.minX),b.maxY-(event.clientY-box.top)/box.height*(b.maxY-b.minY)]);
   });
   new ResizeObserver(()=>this.paint()).observe(this.canvas);
  }
  $('basemap').addEventListener('change',()=>{if(this.map){$('basemap').checked?this.tiles.addTo(this.map):this.tiles.remove();}$('map-offline').hidden=$('basemap').checked;});
  this.repaint();
 }
 color(){return document.documentElement.dataset.theme==='dark'?'#25bce9':'#0ca56b';}
 drawColor(){return document.documentElement.dataset.theme==='dark'?'#fbbf60':'#c76412';}
 // 运动范围用紫罗兰色，和路线绿、点位绿、手绘橙都区分开。
 fenceColor(){return document.documentElement.dataset.theme==='dark'?'#a99cff':'#7a6cf0';}
 maskFill(){return document.documentElement.dataset.theme==='dark'?'rgba(169,156,255,.18)':'rgba(122,108,240,.14)';}
 vertexFill(){return getComputedStyle(document.documentElement).getPropertyValue('--surface').trim()||'#ffffff';}
 setMask(enabled){
  this.maskOn=!!enabled;
  this.applyFenceStyle();
  this.paint();
  return this.maskOn;
 }
 applyFenceStyle(){
  if(!this.fenceLayer)return;
  this.fenceLayer.setStyle({color:this.fenceColor(),fillColor:this.fenceColor(),
   fillOpacity:this.maskOn?0.16:0,weight:1.6,dashArray:'7 5'});
 }
 setFence(fence){
  this.fence=(fence||[]).map(p=>[Number(p[0]),Number(p[1])]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
  if(!this.map)return this.fence;
  if(!this.fence.length){if(this.fenceLayer){this.fenceLayer.remove();this.fenceLayer=null;}return this.fence;}
  const ring=this.fence.map(([x,y])=>[y,x]);
  if(this.fenceLayer){this.fenceLayer.setLatLngs(ring);this.applyFenceStyle();}
  else{
   this.fenceLayer=L.polygon(ring,{weight:1.6,dashArray:'7 5',color:this.fenceColor(),
    fillColor:this.fenceColor(),fillOpacity:this.maskOn?0.16:0}).addTo(this.map);
  }
  return this.fence;
 }
 // 同步场地：围栏与打卡点一起换。任务变了，旧预览属于另一个场地，先收回再回到新围栏。
 venue(fence,cards,taskId){
  const changed=taskId!==undefined&&this.venueTask!==undefined&&String(taskId)!==String(this.venueTask);
  const first=taskId!==undefined&&this.venueTask===undefined;
  if(taskId!==undefined)this.venueTask=taskId;
  this.setFence(fence);
  if(cards)this.cards=cards;
  if(changed){this.userView=false;this.current=null;if(this.route){this.route.remove();this.route=null;}}
  $('map-empty').hidden=!!(this.fence.length||this.cards.length);
  this.renderMarkers();
  if(changed||first)this.fit();
  this.paint();
 }
 guide(cards,fence,taskId){this.venue(fence,cards,taskId);}
 showRoute(enabled){
  this.showPreview=enabled;
  if(this.route&&this.map){enabled?this.route.addTo(this.map):this.route.remove();this.renderMarkers();}
  this.paint();
 }
 repaint(){
  const color=this.color();
  if(this.route)this.route.setStyle({color});if(this.drawLine)this.drawLine.setStyle({color:this.drawColor()});
  if(this.drawVertices)this.drawVertices.eachLayer(layer=>layer.setStyle({color:this.drawColor(),fillColor:this.vertexFill()}));
  if(this.canvas)this.paint();
 }
 renderMarkers(){
  if(!this.map)return this.paint();
  this.markers.clearLayers();
  this.cards.forEach(([x,y],index)=>{
   const marker=L.marker([y,x],{icon:L.divIcon({className:'card-number',html:String(index+1),iconSize:[21,21],iconAnchor:[10,10]})});
   marker.bindTooltip(`打卡点 ${index+1}`);marker.on('click',()=>{if(this.drawMode)this.add([x,y]);});marker.addTo(this.markers);
  });
  if(this.current&&this.showPreview){
   const start=this.current.coordinates[0],end=this.current.coordinates.at(-1);
   L.circleMarker([start[1],start[0]],{radius:6,color:'#fff',weight:3,fillColor:'#0ca56b',fillOpacity:1}).bindTooltip('起点').addTo(this.markers);
   L.circleMarker([end[1],end[0]],{radius:5,color:'#fff',weight:2,fillColor:'#ed9255',fillOpacity:1}).bindTooltip('终点').addTo(this.markers);
  }
 }
 preview(result){
  // 预览自带生成时的场地：先对齐 venueTask，避免把同一次生成误判成切换任务。
  const hadRoute=!!this.route;
  this.venueTask=result.venue?.task_id??this.venueTask;
  this.venue(result.fence||[],result.cards||[],this.venueTask);
  this.current=result;
  if(this.map){
   if(this.route)this.route.remove();
   this.route=L.polyline(result.coordinates.map(([x,y])=>[y,x]),{color:this.color(),weight:4,opacity:.95,lineCap:'round'});
   if(this.showPreview)this.route.addTo(this.map);
   this.renderMarkers();
  }
  // 第一次出图时框住围栏和路线；之后重新生成不再打断用户自己的缩放。
  if(!hadRoute)this.fit();
  this.paint();
 }
 fit(force=false){
  if(!this.map)return this.paint();
  if(this.userView&&!force)return this.paint();
  this.map.invalidateSize();
  // 同时框住围栏和路线：只看路线会贴得太近，看不出它落在哪个场地的哪个位置。
  const boxes=[];
  if(this.fence.length)boxes.push(L.latLngBounds(this.fence.map(([x,y])=>[y,x])));
  if(this.route)boxes.push(this.route.getBounds());
  if(!boxes.length)return;
  const target=boxes.reduce((whole,box)=>whole.extend(box));
  if(!target.isValid())return;
  this.userView=false;this.autoMoving=true;
  this.map.fitBounds(target,{padding:[12,12],maxZoom:17});
  setTimeout(()=>{this.autoMoving=false;},700);
 }
 setDrawing(enabled){
  this.drawMode=enabled;$('draw-tools').hidden=!enabled;
  if(this.map){this.map.getContainer().style.cursor=enabled?'crosshair':'';}
  // 完成绘制仅停止加点，保留草稿可见；切换来源才隐藏草稿。
  const visible=document.getElementById('source').value==='draw';
  if(this.drawLine){visible?this.drawLine.addTo(this.map):this.drawLine.remove();}
  if(this.drawVertices){visible?this.drawVertices.addTo(this.map):this.drawVertices.remove();}
  this.paint();
 }
 add(point){this.drawing.push(point);this.updateDraw();this.onDraw(this.drawing);}
 undo(){this.drawing.pop();this.updateDraw();this.onDraw(this.drawing);}
 clear(){this.drawing=[];this.updateDraw();this.onDraw(this.drawing);}
 updateDraw(){
  if(this.map){
   if(this.drawLine)this.drawLine.remove();if(this.drawVertices)this.drawVertices.remove();
   this.drawLine=L.polyline(this.drawing.map(([x,y])=>[y,x]),{color:this.drawColor(),weight:4,dashArray:'8 5'});
   this.drawVertices=L.layerGroup(this.drawing.map(([x,y],i)=>L.circleMarker([y,x],{radius:i===0?5:3,color:this.drawColor(),weight:2,fillColor:this.vertexFill(),fillOpacity:1,interactive:false})));
   if(document.getElementById('source').value==='draw'){this.drawLine.addTo(this.map);this.drawVertices.addTo(this.map);}
  }
  this.paint();
 }
 bounds(){const p=[...this.fence,...(this.current?.coordinates||[]),...this.drawing];
  if(!p.length)return {minX:112.40,maxX:112.42,minY:34.60,maxY:34.62};
  let minX=Math.min(...p.map(v=>v[0])),maxX=Math.max(...p.map(v=>v[0])),minY=Math.min(...p.map(v=>v[1])),maxY=Math.max(...p.map(v=>v[1]));
  const padX=Math.max(.0004,(maxX-minX)*.08),padY=Math.max(.0004,(maxY-minY)*.08);
  return {minX:minX-padX,maxX:maxX+padX,minY:minY-padY,maxY:maxY+padY};}
 paint(){
  if(!this.canvas)return;
  const b=this.bounds(),box=this.canvas.getBoundingClientRect(),dpi=window.devicePixelRatio||1;
  if(!box.width)return;
  this.canvas.width=box.width*dpi;this.canvas.height=box.height*dpi;
  const ctx=this.canvas.getContext('2d');ctx.scale(dpi,dpi);ctx.clearRect(0,0,box.width,box.height);
  const point=([x,y])=>[(x-b.minX)/(b.maxX-b.minX)*box.width,(b.maxY-y)/(b.maxY-b.minY)*box.height];
  const draw=(pts,color,width,close=false)=>{if(!pts.length)return;ctx.beginPath();pts.forEach((p,i)=>{const [x,y]=point(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});if(close)ctx.closePath();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();};
  // 运动范围：半透明遮罩（可关）+ 紫色虚线；与路线绿、手绘橙区分。
  if(this.fence.length){
   if(this.maskOn){
    ctx.beginPath();this.fence.forEach((p,i)=>{const [x,y]=point(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.closePath();
    ctx.fillStyle=this.maskFill();ctx.fill();
   }
   ctx.setLineDash([7,5]);draw(this.fence,this.fenceColor(),1.6,true);ctx.setLineDash([]);
  }
  if(this.showPreview)draw(this.current?.coordinates||[],this.color(),3);
  if(document.getElementById('source').value==='draw'){
   ctx.setLineDash([8,5]);draw(this.drawing,this.drawColor(),3);ctx.setLineDash([]);
   this.drawing.forEach(p=>{const [x,y]=point(p);ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fillStyle=this.drawColor();ctx.fill();});
  }
  this.cards.forEach(p=>{const [x,y]=point(p);ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fillStyle=this.color();ctx.fill();});
 }
 // 导出当前场地+路线的示意图（不含底图，离线可用）。坐标按中心纬度等比投影，形状不拉伸。
 exportPng({title='当前场地',subtitle=''}={}){
  const fence=this.fence, route=(this.current?.coordinates||[]).map(p=>[p[0],p[1]]), cards=this.cards;
  const all=[...fence,...route,...cards];
  if(!all.length)throw new Error('当前没有可导出的场地或路线。');
  const lat0=(Math.min(...all.map(p=>p[1]))+Math.max(...all.map(p=>p[1])))/2;
  const mLat=110574, mLon=111320*Math.cos(lat0*Math.PI/180);
  const xy=all.map(([lon,lat])=>[lon*mLon,lat*mLat]);
  const minX=Math.min(...xy.map(p=>p[0])),maxX=Math.max(...xy.map(p=>p[0]));
  const minY=Math.min(...xy.map(p=>p[1])),maxY=Math.max(...xy.map(p=>p[1]));
  const spanX=Math.max(1,maxX-minX),spanY=Math.max(1,maxY-minY);
  const width=1200, pad=64, head=104, foot=64;
  const inner=width-2*pad;
  let scale=inner/spanX, height=Math.round(head+foot+spanY*scale+2*pad);
  if(height<680){scale=(680-head-foot-2*pad)/spanY;height=680;}
  if(height>1800){height=1800;scale=Math.min(scale,(height-head-foot-2*pad)/spanY);}
  const plotW=spanX*scale, plotH=spanY*scale;
  const left=pad+(inner-plotW)/2, top=head+pad+( (height-head-foot-2*pad)-plotH)/2;
  const toPx=([lon,lat])=>[left+(lon*mLon-minX)*scale, top+plotH-(lat*mLat-minY)*scale];
  const dark=document.documentElement.dataset.theme==='dark';
  const surface=dark?'#101a16':'#ffffff', ink=dark?'#e7f0ec':'#14342a', muted=dark?'#9fb4aa':'#5c7168';
  const accent=this.color(), fenceColor=this.fenceColor();
  const canvas=document.createElement('canvas');
  canvas.width=width*2;canvas.height=height*2;
  const ctx=canvas.getContext('2d');ctx.scale(2,2);
  ctx.fillStyle=surface;ctx.fillRect(0,0,width,height);
  // 网格（100 m），让示意有比例参照
  const grid=100;
  ctx.strokeStyle=dark?'rgba(255,255,255,.06)':'rgba(20,52,42,.06)';ctx.lineWidth=1;
  for(let x=Math.ceil(minX/grid)*grid;x<maxX;x+=grid){const px=left+(x-minX)*scale;ctx.beginPath();ctx.moveTo(px,top);ctx.lineTo(px,top+plotH);ctx.stroke();}
  for(let y=Math.ceil(minY/grid)*grid;y<maxY;y+=grid){const py=top+plotH-(y-minY)*scale;ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(left+plotW,py);ctx.stroke();}
  if(fence.length){
   ctx.beginPath();fence.forEach((p,i)=>{const [x,y]=toPx(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.closePath();
   if(this.maskOn){ctx.fillStyle=this.maskFill();ctx.fill();}
   ctx.strokeStyle=fenceColor;ctx.lineWidth=2;ctx.setLineDash([7,5]);ctx.stroke();ctx.setLineDash([]);
  }
  if(route.length){
   ctx.beginPath();route.forEach((p,i)=>{const [x,y]=toPx(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
   ctx.strokeStyle=accent;ctx.lineWidth=3.2;ctx.lineJoin='round';ctx.lineCap='round';ctx.stroke();
   const [sx,sy]=toPx(route[0]),[ex,ey]=toPx(route.at(-1));
   const dot=(x,y,color,label)=>{ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();ctx.strokeStyle=surface;ctx.lineWidth=2.5;ctx.stroke();ctx.fillStyle=surface;ctx.font='600 10px system-ui,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(label,x,y+.5);};
   dot(sx,sy,'#0ca56b','起');dot(ex,ey,'#ed9255','终');
  }
  cards.forEach((p,index)=>{
   const [x,y]=toPx(p);
   ctx.beginPath();ctx.arc(x,y,11,0,Math.PI*2);ctx.fillStyle=dark?'#1b2b24':'#ffffff';ctx.fill();
   ctx.strokeStyle=accent;ctx.lineWidth=2;ctx.stroke();
   ctx.fillStyle=accent;ctx.font='700 11px system-ui,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(index+1),x,y+.5);
  });
  // 比例尺：挑一个能放进 140 px 的整数米
  const candidates=[50,100,200,300,500,1000,2000];
  const barM=candidates.find(m=>m*scale<=150)||candidates.at(-1);
  const barPx=barM*scale;
  const bx=left, by=top+plotH+34;
  ctx.strokeStyle=muted;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(bx,by);ctx.lineTo(bx+barPx,by);ctx.moveTo(bx,by-5);ctx.lineTo(bx,by+5);ctx.moveTo(bx+barPx,by-5);ctx.lineTo(bx+barPx,by+5);ctx.stroke();
  ctx.fillStyle=muted;ctx.font='12px system-ui,sans-serif';ctx.textAlign='left';ctx.textBaseline='top';
  ctx.fillText(barM>=1000?`${barM/1000} 公里`:`${barM} 米`,bx,by+8);
  // 标题与页脚
  ctx.fillStyle=ink;ctx.font='700 24px system-ui,sans-serif';ctx.textAlign='left';ctx.textBaseline='alphabetic';
  ctx.fillText(title,pad,52);
  ctx.fillStyle=muted;ctx.font='13px system-ui,sans-serif';
  ctx.fillText(subtitle,pad,78);
  ctx.fillText(`云运动 · 洛阳理工工作台 · 示意图（无底图）· ${new Date().toLocaleString('zh-CN',{hour12:false})}`,pad,height-26);
  return {url:canvas.toDataURL('image/png'),width,height};
 }
}
