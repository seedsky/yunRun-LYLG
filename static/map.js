import {$} from './ui.js';
// Leaflet 随包分发。只有底图需要网络，地图组件和绘制在断网时仍可使用。
export class RouteMap {
 constructor(onDraw) {
  this.onDraw=onDraw;this.drawMode=false;this.drawing=[];this.current=null;this.cards=[];this.showPreview=true;
  this.fence=[[112.413904,34.611019],[112.416887,34.607354],[112.424578,34.611668],[112.422518,34.62002]];
  if(window.L){
   this.map=L.map('map',{preferCanvas:true,zoomControl:false}).setView([34.613,112.419],16);
   L.control.zoom({position:'topright'}).addTo(this.map);
   this.tiles=L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    {subdomains:['1','2','3','4'],maxZoom:19,attribution:'© 高德地图 · GCJ-02'}).addTo(this.map);
   let failed=0;
   this.tiles.on('tileerror',()=>{if(++failed>=2)$('map-offline').hidden=false;});
   this.tiles.on('tileload',()=>{$('map-offline').hidden=true;});
   this.map.on('click',event=>{if(this.drawMode)this.add([event.latlng.lng,event.latlng.lat]);});
   this.markers=L.layerGroup().addTo(this.map);
   this.fenceLayer=L.polygon(this.fence.map(([x,y])=>[y,x]),{weight:1,dashArray:'5 5',fillOpacity:.025,color:'#729b87'}).addTo(this.map);
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
 vertexFill(){return getComputedStyle(document.documentElement).getPropertyValue('--surface').trim()||'#ffffff';}
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
 guide(cards){this.cards=cards||[];this.renderMarkers();}
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
  this.current=result;this.cards=result.cards||this.cards;this.fence=result.fence||this.fence;
  if(this.map){
   if(this.route)this.route.remove();
   this.route=L.polyline(result.coordinates.map(([x,y])=>[y,x]),{color:this.color(),weight:4,opacity:.95,lineCap:'round'});
   if(this.showPreview)this.route.addTo(this.map);
   this.renderMarkers();
  }
  this.fit();this.paint();
 }
 fit(){if(this.map){this.map.invalidateSize();if(this.route)this.map.fitBounds(this.route.getBounds(),{padding:[28,28],maxZoom:17});}else this.paint();}
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
  draw(this.fence,'#729b87',1,true);if(this.showPreview)draw(this.current?.coordinates||[],this.color(),3);
  if(document.getElementById('source').value==='draw'){
   ctx.setLineDash([8,5]);draw(this.drawing,this.drawColor(),3);ctx.setLineDash([]);
   this.drawing.forEach(p=>{const [x,y]=point(p);ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fillStyle=this.drawColor();ctx.fill();});
  }
  this.cards.forEach(p=>{const [x,y]=point(p);ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fillStyle=this.color();ctx.fill();});
 }
}
