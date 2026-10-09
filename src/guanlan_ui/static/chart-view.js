/* Workspace DOM adapter. Only this adapter knows quote labels and sliders. */
(()=>{'use strict';
window.createWorkspaceChart=({state:S,$,root,preferences,onViewChange,onSelection=()=>{},onInspect=()=>{}})=>{
 const {price,money,pct,tone}=ObserverFormat;
 let generation=ObserverCandleProtocol.id();
 const fields=['trade_date','period_start','open','high','low','close','volume','amount','change_pct','volume_ratio','change_reason','volume_ratio_reason','provisional','status','z_zhixing_short_trend','z_zhixing_bull_bear','thirty_week_ma','bbi'];
 function readSelection(){
  const d=S.detail,value=core.getSelection();
  return {status:d?(value?'ready':'empty'):['error','empty'].includes(S.loadState)?S.loadState:'loading',context:{title:d?.title||'读取中…',code:d?.code||d?.target?.id||S.target?.id||S.stockCode||'',target:d?.target||S.target,period:value?.period||S.barPeriod,priceMode:d?.priceMode||S.barMode,revision:d?.revision,generation,units:value?.units||{}},selection:value?{date:value.date,index:value.index,row:Object.fromEntries(fields.map(k=>[k,value.row[k]])),markers:value.markers}:null};
 }
 const inspector=createObserverInspector({read:readSelection,onStep:direction=>core.stepSelection(direction),notice:message=>{$('detailStatus').textContent=message;}});
 function updateRangeSelection(){root.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',String(S.barRange)===b.dataset.range));}
 function render(r){
  const unit=r.period==='weekly'?'周':'日',last=r.last,total=r.total,count=r.count,max=Math.max(0,total-count),min=Math.min(20,total||20);
  if(S.detail){S.barRange=r.view.range;S.panOffset=r.view.pan;}
  Object.assign($('timelineRange'),{max:String(max),value:String(max-r.view.pan),disabled:max===0});
  Object.assign($('zoomRange'),{min:String(min),max:String(Math.max(min,total)),value:String(count||min),disabled:total<=min});
  $('visibleDays').textContent=count?`${count}${unit}`:'—';$('viewWindow').textContent=count?`${r.first.period_start||r.first.trade_date} — ${last.trade_date}`:'—';
  $('quoteDateLabel').textContent=last?(r.view.pan===0?`最新${unit}线`:last.trade_date):'暂无行情';
  for(const [id,key] of [['latestClose','close'],['latestOpen','open'],['latestHigh','high'],['latestLow','low']])$(id).textContent=price(last?.[key]);
  $('latestAmount').textContent=money(last?.amount);$('barRange').textContent=count?`${count}${unit}`:'—';tone($('latestClose'),(last?.close||0)-(last?.open||0));updateRangeSelection();
 }
 function selection(value){
  const r=value?.row,period=value?.period==='weekly'?'周':'日',change=Number.isFinite(r?.change_pct)?r.change_pct:null,ratio=Number.isFinite(r?.volume_ratio)?r.volume_ratio:null;
  $('candleDate').textContent=r?(r.period_start?`${r.period_start} — ${r.trade_date}`:r.trade_date):'暂无所选K线';
  $('candleChangeLabel').textContent=period+'涨跌';$('candleRatioLabel').textContent=period+'量比';
  $('candleChange').textContent=change==null?'—':(change>0?'+':'')+pct(change);tone($('candleChange'),change||0);
  $('candleRatio').textContent=ratio==null?'—':ratio.toFixed(2)+'倍';
  $('candleChange').title=r?.change_reason||'日涨跌采用原始行情口径；周涨跌相对前一周收盘';
  $('candleRatio').title=r?.volume_ratio_reason||`本期成交量 ÷ 前5个交易${period}平均成交量`;
  $('candleNote').textContent=r?(r.provisional?'本周未结束':ratio==null?r.volume_ratio_reason||'量比历史不足':''):'';
  onSelection(value);inspector.update();
 }
 const core=createObserverChart({canvas:$('leaderChart'),overlay:$('chartOverlay'),onRender:render,onSelection:selection,onInspect:value=>{inspector.open();onInspect(value);},allowWheelZoom:preferences.permitsWheel,onViewChange:v=>{S.barRange=v.range;S.panOffset=v.pan;onViewChange?.();}});
 function drawCurrentBars(){
  const model=ObserverModel.chart(S.detail,S),overlays=[],chosen=preferences.read();
  if(S.showZhixing)overlays.push({key:'z_zhixing_bull_bear',color:'#dfb319',width:chosen.zhixingYellowWidth},{key:'z_zhixing_short_trend',color:'#f4f6f5',width:chosen.zhixingWhiteWidth});
  if(S.showThirtyWeek)overlays.push({key:'thirty_week_ma',color:'#42b8e8',width:chosen.thirtyWeekWidth});
  if(S.showBbi)overlays.push({key:'bbi',color:'#c08cff',width:chosen.bbiWidth});
  $('chartMethod').textContent=model.description||'';
  generation=ObserverCandleProtocol.id();core.setData({...model,identity:S.detail?JSON.stringify([S.detail.target,S.detail.period,S.detail.priceMode,S.detail.revision]):null,overlays,view:{range:S.barRange,pan:S.panOffset}});
 }
 return {drawCurrentBars,updateRangeSelection,clearCrosshair:core.clearCrosshair,setChartRange:core.setRange,panChart:core.pan,zoomChart:core.zoom,resetChart:core.reset,currentBars:core.currentBars,allBars:core.allBars,destroy:()=>{core.destroy();inspector.destroy();},core,inspector};
};
})();
