// 与 Python taskgen.hav 一致的球面距离，仅用于绘制草稿的即时提示。
// 正式预览和执行仍以后台规范化、打卡点吸附与采样后的结果为准。
export function distance(a,b){
 const rad=Math.PI/180,dy=(b[1]-a[1])*rad,dx=(b[0]-a[0])*rad;
 const h=Math.sin(dy/2)**2+Math.cos(a[1]*rad)*Math.cos(b[1]*rad)*Math.sin(dx/2)**2;
 return 2*6371008.8*Math.asin(Math.sqrt(Math.min(1,Math.max(0,h))));
}
export function drawingStats(points,pace,targetKm){
 let meters=0,lastSegment=0;
 for(let i=1;i<points.length;i++){lastSegment=distance(points[i-1],points[i]);meters+=lastSegment;}
 return {points:points.length,meters,lastSegment,closingGap:points.length>1?distance(points.at(-1),points[0]):0,
  seconds:Number.isFinite(pace)&&pace>0?meters/1000*pace*60:null,
  remaining:Number.isFinite(targetKm)&&targetKm>0?targetKm*1000-meters:null};
}
