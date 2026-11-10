import test from 'node:test';
import assert from 'node:assert/strict';
import {distance,drawingStats} from '../static/drawing.js';

test('距离与后端球面公式一致，并支持撤销、闭合和目标差值',()=>{
 const points=[[112.4176,34.6123],[112.4176,34.6133],[112.4176,34.6123]];
 const segment=111.1950802335;
 assert.ok(Math.abs(distance(points[0],points[1])-segment)<.0001);
 const stats=drawingStats(points,5.5,2);
 assert.ok(Math.abs(stats.meters-segment*2)<.0001);
 assert.equal(stats.closingGap,0);
 assert.ok(Math.abs(stats.seconds-stats.meters*.33)<.0001);
 assert.ok(Math.abs(stats.remaining-(2000-segment*2))<.0001);
 assert.ok(drawingStats(points.slice(0,-1),5.5,2).meters<stats.meters);
 assert.equal(points.length,3);
});
test('清空或首点时为零；无效配速不伪造预计用时',()=>{
 assert.equal(drawingStats([],5.5,2).meters,0);
 assert.equal(drawingStats([[0,0]],5.5,2).meters,0);
 assert.equal(drawingStats([[0,0],[0,.001]],NaN,NaN).seconds,null);
 assert.equal(drawingStats([[0,0],[0,.001]],5.5,NaN).remaining,null);
});
