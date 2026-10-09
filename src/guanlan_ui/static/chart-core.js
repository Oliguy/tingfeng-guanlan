/* Chart v1: injected canvases + bars/units/overlays/view, structured callbacks.
   No workspace state, fixed element IDs, data-source types or date cutoffs. */
'use strict';
window.createObserverChart=({canvas,overlay,onViewChange=()=>{},onRender=()=>{},onSelection=()=>{},onInspect=()=>{},allowWheelZoom=()=>true})=>{
 let data={bars:[],units:{},period:'daily',overlays:[]},view={range:150,pan:0},dead=false,hoverFrame=null,hoverEvent=null,wheel=0,drag=null,pinch=null,selectedDate=null,indices=new Map(),plot=null;
 const pointers=new Map(),lifetime=new AbortController();
 const rangeValue=value=>value==='all'?'all':Math.max(20,Number(value)||150);
 function setupCanvas(element){const rect=element.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,Math.round(rect.width*dpr)),h=Math.max(1,Math.round(rect.height*dpr));if(element.width!==w)element.width=w;if(element.height!==h)element.height=h;const context=element.getContext('2d');context.setTransform(dpr,0,0,dpr,0,0);return {x:context,w:rect.width,h:rect.height};}
 const allBars=()=>data.bars;
 const containsDate=(row,date)=>date>=(row.period_start||row.trade_date)&&date<=row.trade_date;
 const containsTrade=(row,trade)=>trade.barDate?row.trade_date===trade.barDate:containsDate(row,trade.date);
 function tradeColumn(rows,trade,opening){const index=rows.findIndex(row=>containsTrade(row,trade));return index>=0?index:opening&&trade.atOpening?rows.length:-1;}
 const visibleCount=()=>view.range==='all'?data.bars.length:Math.min(Math.max(20,Number(view.range)||150)-(data.stableViewport&&view.pan===0&&data.opening?1:0),data.bars.length);
 const openingSlot=()=>data.stableViewport&&data.opening?1:0;
 const endIndex=()=>data.bars.length-Math.max(0,view.pan-openingSlot());
 function clampPan(){const maximum=data.stableViewport&&view.range!=='all'?data.bars.length+openingSlot()-Number(view.range):data.bars.length-visibleCount();view.pan=Math.max(0,Math.min(Math.max(0,maximum),Math.round(Number(view.pan)||0)));return view.pan;}
 function currentBars(){clampPan();const end=endIndex();return data.bars.slice(Math.max(0,end-visibleCount()),end);}
 function clearCrosshair(){if(hoverFrame!==null)cancelAnimationFrame(hoverFrame);hoverFrame=null;hoverEvent=null;overlay.getContext('2d').clearRect(0,0,overlay.width,overlay.height);}
 function normalizeSelection(rows){if(!rows.length){selectedDate=null;return;}const end=endIndex(),start=end-rows.length,index=indices.get(selectedDate);selectedDate=data.bars[index==null?end-1:Math.max(start,Math.min(end-1,index))].trade_date;}
 function getSelection(){const index=indices.get(selectedDate),row=data.bars[index];return row?{row,index,date:selectedDate,period:data.period,units:data.units,markers:(data.markers||[]).filter(m=>containsDate(row,m.date)),trades:(data.trades||[]).filter(t=>containsTrade(row,t))}:null;}
 function publishSelection(reason){const value=getSelection();onSelection(value?{...value,reason}:null);}
 function draw(){if(dead)return;const rows=currentBars();normalizeSelection(rows);drawLeader(rows);paintSelection();onRender({view:{...view},count:rows.length,total:data.bars.length,totalSlots:data.bars.length+openingSlot(),visibleSlots:rows.length+(view.pan===0?openingSlot():0),first:rows[0]||null,last:rows.at(-1)||null,period:data.period,units:data.units});publishSelection('view');}
 function changed(){draw();onViewChange({...view});}
 function setData(input){if(dead)return;if(input.identity!==data.identity)selectedDate=null;data={...input,bars:(input.bars||[]).map(r=>({...r})),units:{...input.units},overlays:(input.overlays||[]).map(r=>({...r}))};indices=new Map(data.bars.map((r,i)=>[r.trade_date,i]));view={...input.view,range:rangeValue(input.view?.range??view.range),pan:input.view?.pan??view.pan};clearCrosshair();wheel=0;drag=null;pinch=null;pointers.clear();overlay.classList.remove('dragging');draw();}
 function refit(){delete view.priceRange;delete view.volumeMax;}
 function setView(value){if(dead)return;view={...view,...value};view.range=rangeValue(view.range);refit();changed();}
 function patchData(input){
  if(dead)return;if(input.identity!==data.identity){setData(input);return;}
  const anchor=view.pan>0?currentBars().at(-1)?.trade_date:null;
  const delta=input.barDelta;
  if(delta){data.bars.splice(delta.remove_from,data.bars.length-delta.remove_from,...delta.append.map(r=>({...r})));indices=new Map(data.bars.map((r,i)=>[r.trade_date,i]));}
  const layers=data.overlays.map(line=>line.key).join('|');
  const {barDelta,...values}=input;data={...data,...values,bars:data.bars};
  if(layers!==data.overlays.map(line=>line.key).join('|'))refit();
  if(anchor&&indices.has(anchor))view.pan=data.bars.length-1-indices.get(anchor)+openingSlot();
  draw();
 }
 // Resize only to the left of the current right edge. At the oldest bar,
 // cap the range instead of letting clampPan move that right edge forward.
 function resizeRange(range,pan=view.pan){
  if(dead)return;
  if(data.stableViewport){
   const total=data.bars.length+openingSlot(),offset=Math.max(0,Math.min(Math.max(0,total-20),Math.round(Number(pan)||0))),available=total-offset,maximum=offset?available:Math.max(available,Number(view.range)||0);
   setView({range:range==='all'?'all':Math.min(maximum,Math.max(Math.min(20,total),Math.round(Number(range)||150))),pan:offset});return;
  }
  const total=data.bars.length,offset=Math.max(0,Math.min(Math.max(0,total-20),Math.round(Number(pan)||0))),available=total-offset;
  const requested=range==='all'?available:Math.round(Number(range)||150),count=Math.min(available,Math.max(Math.min(20,total),requested));
  setView({range:count===total?'all':count,pan:offset});
 }
 function setRange(range){resizeRange(range);}
 function pan(direction){setView({pan:view.pan+direction*Math.max(1,Math.round(visibleCount()*.12))});}
 function zoom(direction){const total=data.bars.length,old=data.stableViewport&&view.range!=='all'?Number(view.range):visibleCount();if(dead||total<=20||!old)return;resizeRange(Math.round(old*(direction>0?1.12:.89)));}
 function reset(){selectedDate=null;setView({range:data.period==='weekly'?'all':150,pan:0});}
 function selectDate(date,reason='select'){
  const index=indices.get(date);if(dead||index==null)return false;
  const oldPan=view.pan;clampPan();const end=endIndex(),count=visibleCount(),start=end-count;
  selectedDate=date;
  if(index<start)view.pan=data.bars.length+openingSlot()-count-index;else if(index>=end)view.pan=data.bars.length+openingSlot()-1-index;
  if(view.pan!==oldPan)changed();else{paintSelection();publishSelection(reason);}return true;
 }
 function stepSelection(direction){const index=indices.get(selectedDate);if(dead||index==null||![-1,1].includes(direction))return false;const next=Math.max(0,Math.min(data.bars.length-1,index+direction));if(next===index)return false;return selectDate(data.bars[next].trade_date,'keyboard');}
 function inspect(){const value=getSelection();if(!dead&&value)onInspect(value);}
function paintIndicatorLine(context,rows,key,scale,pad,step,color,width){
  context.strokeStyle=color;context.lineWidth=width;context.lineJoin="round";context.lineCap="round";context.beginPath();let started=false;
  rows.forEach((row,index)=>{const raw=row[key],value=raw==null?NaN:Number(raw);if(!Number.isFinite(value)){started=false;return}const px=pad.l+(index+.5)*step,py=scale(value);if(started)context.lineTo(px,py);else{context.moveTo(px,py);started=true}});context.stroke();
}

function drawLeader(rows){
  plot=null;const{x,w,h}=setupCanvas(canvas);x.clearRect(0,0,w,h);if(!rows.length||w<20||h<20)return;
  const pad={l:12,r:66,t:15,b:25},innerWidth=w-pad.l-pad.r,innerHeight=h-pad.t-pad.b,priceHeight=innerHeight*.76,volumeTop=pad.t+priceHeight+18,volumeHeight=Math.max(30,h-volumeTop-pad.b);
  const opening=view.pan===0?data.opening:null;
  const priceValues=rows.flatMap(row=>[row.high,row.low]).filter(Number.isFinite);
  if(opening&&Number.isFinite(opening.price))priceValues.push(opening.price);
  const trades=(data.trades||[]).filter(t=>Number.isFinite(t.price)&&tradeColumn(rows,t,opening)>=0),priceLines=(data.priceLines||[]).filter(line=>Number.isFinite(line.price));
  priceValues.push(...trades.map(t=>t.price),...priceLines.map(line=>line.price));
  for(const line of data.overlays)priceValues.push(...rows.map(row=>row[line.key]).filter(Number.isFinite));
  if(!priceValues.length){drawLeader([]);return;}
  const rawHigh=Math.max(...priceValues),rawLow=Math.min(...priceValues),pricePadding=Math.max(.001,(rawHigh-rawLow)*.04);
  let high=rawHigh+pricePadding,low=rawLow-pricePadding,maxVolume=Math.max(...rows.map(row=>row.volume||0),1);
  // Live training keeps its chosen scale. Rolling off an old extreme must not
  // magnify the entire chart; only newly known values outside it extend it.
  if(data.stableViewport){
   const prior=view.priceRange;
   if(prior&&Number.isFinite(prior.low)&&Number.isFinite(prior.high)&&prior.high>prior.low){low=rawLow<prior.low?Math.min(low,prior.low):prior.low;high=rawHigh>prior.high?Math.max(high,prior.high):prior.high;}
   view.priceRange={low,high};maxVolume=Math.max(maxVolume,Number(view.volumeMax)||1);view.volumeMax=maxVolume;
  }
  const span=Math.max(.001,high-low),columns=data.stableViewport&&view.range!=='all'?Number(view.range):rows.length+(opening?1:0),step=innerWidth/columns,barLeft=pad.l+(columns-rows.length-(opening?1:0))*step,candleWidth=Math.max(2,Math.min(9,step*.76)),scale=value=>pad.t+(high-value)/span*priceHeight;
  plot={left:pad.l,barLeft,right:w-pad.r,top:pad.t,bottom:h-pad.b,step,scale,start:endIndex()-rows.length};
  x.font="10px Consolas";
  for(const marker of data.markers||[]){const index=rows.findIndex(r=>marker.date>=(r.period_start||r.trade_date)&&marker.date<=r.trade_date);if(index<0)continue;const px=barLeft+(index+.5)*step;x.fillStyle=marker.color||'#eac777';x.globalAlpha=.10;x.fillRect(px-step/2,pad.t,step,h-pad.t-pad.b);x.globalAlpha=1;x.strokeStyle=marker.color||'#eac777';x.lineWidth=1;x.setLineDash([2,3]);x.beginPath();x.moveTo(px,pad.t);x.lineTo(px,h-pad.b);x.stroke();x.setLineDash([]);x.fillText(marker.label||'',Math.min(w-pad.r-28,Math.max(pad.l,px+4)),pad.t+12);}
  for(let index=0;index<5;index++){const value=high-span*index/4,y=scale(value);x.strokeStyle="#29312e";x.lineWidth=1;x.beginPath();x.moveTo(pad.l,y);x.lineTo(w-pad.r,y);x.stroke();x.fillStyle="#8d9892";x.fillText(value.toFixed(3),w-pad.r+8,y+3)}
  let previousMonth="";
  rows.forEach((row,index)=>{const month=data.anonymous?String(Math.floor(index/Math.ceil(rows.length/5))):row.trade_date.slice(0,7),px=barLeft+(index+.5)*step;if(month!==previousMonth){x.strokeStyle="#343d39";x.setLineDash([3,4]);x.beginPath();x.moveTo(px,pad.t);x.lineTo(px,h-pad.b);x.stroke();x.setLineDash([]);x.fillStyle="#7f8a84";if(px>pad.l+72&&px<w-pad.r-72)x.fillText(data.anonymous?row.trade_date:month.slice(5),px+3,h-7);previousMonth=month}if(!row.open||!row.close)return;const up=row.close>=row.open,color=up?"#f05b5b":"#24b787";x.strokeStyle=color;x.fillStyle=color;x.lineWidth=1;x.beginPath();x.moveTo(px,scale(row.high));x.lineTo(px,scale(row.low));x.stroke();const bodyTop=Math.min(scale(row.open),scale(row.close)),bodyHeight=Math.max(1.3,Math.abs(scale(row.open)-scale(row.close)));x.fillRect(px-candleWidth/2,bodyTop,candleWidth,bodyHeight);const barHeight=(row.volume||0)/maxVolume*volumeHeight;x.globalAlpha=.42;x.fillRect(px-candleWidth/2,h-pad.b-barHeight,candleWidth,barHeight);x.globalAlpha=1});
  for(const line of data.overlays)paintIndicatorLine(x,rows,line.key,scale,{...pad,l:barLeft},step,line.color,line.width||1.8);
  if(opening){const px=barLeft+(rows.length+.5)*step,py=scale(opening.price);x.strokeStyle='#c9dbef';x.lineWidth=2;x.setLineDash([3,6]);x.beginPath();x.moveTo(px,pad.t);x.lineTo(px,h-pad.b);x.stroke();x.setLineDash([]);x.beginPath();x.moveTo(px-6,py);x.lineTo(px+6,py);x.stroke();x.beginPath();x.arc(px,py,3,0,Math.PI*2);x.fillStyle='#c9dbef';x.fill();x.textAlign='right';x.fillText('今日开盘 '+opening.price.toFixed(2),px-10,Math.max(pad.t+12,py-12));x.textAlign='left';}
  for(const line of priceLines){const py=scale(line.price);x.strokeStyle=line.color||'#eac777';x.lineWidth=1.3;x.setLineDash([7,4]);x.beginPath();x.moveTo(pad.l,py);x.lineTo(w-pad.r,py);x.stroke();x.setLineDash([]);x.font='11px "Microsoft YaHei",sans-serif';x.fillStyle=x.strokeStyle;x.textAlign='left';x.fillText((line.label||'')+' '+line.price.toFixed(2),pad.l+8,Math.max(pad.t+12,py-6));}
  for(const trade of trades){const index=tradeColumn(rows,trade,opening),px=barLeft+(index+.5)*step,py=scale(trade.price),buy=trade.side==='BUY',direction=buy?1:-1,color=trade.color||(buy?'#fa8183':'#59c69f');x.fillStyle=color;x.strokeStyle=color;x.lineWidth=1.4;x.beginPath();x.arc(px,py,3,0,Math.PI*2);x.fill();x.beginPath();x.moveTo(px,py+direction*5);x.lineTo(px-5,py+direction*14);x.lineTo(px+5,py+direction*14);x.closePath();x.fill();x.font='bold 11px "Microsoft YaHei",sans-serif';x.textAlign=px>w-pad.r-50?'right':'left';x.fillText(trade.label||(buy?'B 买入':'S 卖出'),px+(x.textAlign==='right'?-8:8),Math.max(pad.t+12,Math.min(pad.t+priceHeight-4,py+direction*23)));x.textAlign='left';}
  x.font='10px Consolas';
  x.strokeStyle="#343d39";x.lineWidth=1;x.beginPath();x.moveTo(pad.l,volumeTop-8);x.lineTo(w-pad.r,volumeTop-8);x.stroke();
  const last=rows.at(-1);if(last?.close){const y=scale(last.close);x.strokeStyle=last.close>=last.open?"#f05b5b":"#24b787";x.setLineDash([4,3]);x.beginPath();x.moveTo(pad.l,y);x.lineTo(w-pad.r,y);x.stroke();x.setLineDash([]);x.fillStyle=x.strokeStyle;x.fillRect(w-pad.r+4,y-9,58,18);x.fillStyle="#fff";x.fillText(last.close.toFixed(3),w-pad.r+10,y+3)}
  const firstDate=rows[0].period_start||rows[0].trade_date,lastDate=last.trade_date;x.fillStyle="#7f8a84";x.fillText(firstDate,pad.l,h-7);x.fillText(lastDate,w-pad.r-x.measureText(lastDate).width,h-7);

}


 function paintSelection(){
  clearCrosshair();const value=getSelection();if(!plot||!value)return;
  const {x,w,h}=setupCanvas(overlay),{row,index}=value,px=plot.barLeft+(index-plot.start+.5)*plot.step,py=Math.max(plot.top,Math.min(plot.bottom,plot.scale(row.close)));
  x.clearRect(0,0,w,h);x.strokeStyle='rgba(166,178,171,.55)';x.lineWidth=1;x.setLineDash([4,4]);x.beginPath();x.moveTo(px,plot.top);x.lineTo(px,plot.bottom);x.moveTo(plot.left,py);x.lineTo(plot.right,py);x.stroke();x.setLineDash([]);x.font='9px Consolas';
  const label=row.period_start?(data.anonymous?`${row.period_start}~${row.trade_date}`:`${row.period_start.slice(5)}~${row.trade_date.slice(5)}`):row.trade_date,labelWidth=x.measureText(label).width+10,labelX=Math.max(plot.left,Math.min(plot.right-labelWidth,px-labelWidth/2));x.fillStyle='#3f4a45';x.fillRect(labelX,h-22,labelWidth,16);x.fillStyle='#fff';x.fillText(label,labelX+5,h-11);
 }
 function crosshair(event){
  if(!plot)return false;const rect=canvas.getBoundingClientRect(),localX=event.clientX-rect.left,localY=event.clientY-rect.top;
  if(localX<plot.barLeft||localX>plot.right||localY<plot.top||localY>plot.bottom)return false;
  const index=Math.min(endIndex()-1,plot.start+Math.floor((localX-plot.barLeft)/plot.step)),date=data.bars[index]?.trade_date;
  if(date!==selectedDate)selectDate(date,'pointer');return true;
 }
 const listen=(type,handler,options={})=>overlay.addEventListener(type,handler,{...options,signal:lifetime.signal});
 const distance=()=>{const [a,b]=[...pointers.values()];return Math.hypot(a.x-b.x,a.y-b.y);};
 listen('wheel',e=>{if(!allowWheelZoom(e)){wheel=0;return;}e.preventDefault();wheel+=e.deltaY;if(Math.abs(wheel)<60)return;zoom(wheel>0?1:-1);wheel=0;},{passive:false});
 listen('dblclick',e=>{e.preventDefault();if(crosshair(e))inspect();});
 listen('keydown',e=>{const actions={ArrowLeft:()=>stepSelection(-1),ArrowRight:()=>stepSelection(1),Enter:inspect,'+':()=>zoom(-1),'=':()=>zoom(-1),'-':()=>zoom(1),Home:reset,'0':reset};if(actions[e.key]){e.preventDefault();actions[e.key]();}});
 listen('pointerdown',e=>{e.preventDefault();overlay.focus({preventScroll:true});crosshair(e);overlay.setPointerCapture?.(e.pointerId);pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});overlay.classList.add('dragging');if(pointers.size===1)drag={id:e.pointerId,x:e.clientX,pan:view.pan,last:view.pan};else if(pointers.size===2){pinch={distance:distance(),count:visibleCount(),pan:view.pan};drag=null;}});
 listen('pointermove',e=>{if(!pointers.has(e.pointerId)){hoverEvent={clientX:e.clientX,clientY:e.clientY};if(hoverFrame===null)hoverFrame=requestAnimationFrame(()=>{hoverFrame=null;if(hoverEvent)crosshair(hoverEvent);});return;}pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});if(pointers.size===2&&pinch){const d=distance();if(d>8)resizeRange(Math.round(pinch.count*pinch.distance/d),pinch.pan);return;}if(drag?.id===e.pointerId){view.pan=drag.pan+Math.round((e.clientX-drag.x)/Math.max(2,plot?.step||1));clampPan();if(view.pan!==drag.last){drag.last=view.pan;refit();changed();}}});
 const finish=e=>{pointers.delete(e.pointerId);if(pointers.size<2)pinch=null;if(!pointers.size){drag=null;overlay.classList.remove('dragging');}};
 listen('pointerup',finish);listen('pointercancel',finish);listen('lostpointercapture',finish);listen('pointerleave',()=>{if(hoverFrame!==null)cancelAnimationFrame(hoverFrame);hoverFrame=null;hoverEvent=null;});
 const observer=new ResizeObserver(draw);observer.observe(canvas);
 function destroy(){if(dead)return;dead=true;lifetime.abort();observer.disconnect();clearCrosshair();pointers.clear();selectedDate=null;onSelection(null);overlay.classList.remove('dragging');}
 return {setData,patchData,setView,setRange,pan,zoom,reset,draw,currentBars,allBars,clearCrosshair,selectDate,stepSelection,getSelection,inspect,destroy,getView:()=>({...view})};
};
