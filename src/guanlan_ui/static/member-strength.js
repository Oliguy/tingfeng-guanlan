/* Pure display and comparison rules; all financial calculations stay in Python. */
(()=>{'use strict';
 const current=s=>s?.status==='ready',known=v=>typeof v==='number'&&Number.isFinite(v);
 const labels={pending:'计算中',insufficient:'历史不足',stale:'行情滞后',unavailable:'不可用'};
 function cells(s={status:'pending'}){
  const valid=current(s),streak=valid&&known(s.consecutive_above_weeks)&&s.consecutive_above_weeks>=0;
  return [
   {type:'strength',signal:valid?s:{},value:valid&&s.total===5?s.above+'/5':'—'},
   {value:streak?(s.streak_is_lower_bound?'≥':'')+s.consecutive_above_weeks+'周':labels[s.status]||'—',tone:streak?(s.consecutive_above_weeks>0?1:-1):0,
    title:s.reason||(s.status==='stale'?`最近行情 ${s.quote_date}；集合截至 ${s.expected_date}`:'连续站上30周线：大于0周为红色，0周为绿色；末周按已存行情计算')},
   {value:valid?ObserverFormat.pct(s.distance_30w):'—',tone:valid?s.distance_30w:0}
  ];
 }
 function ratioCell(s={}){return {value:known(s.volume_ratio)?s.volume_ratio.toFixed(2)+'倍':'—',title:'日量比＝当日成交量÷前5个交易日平均成交量'+(s.volume_ratio_date?'；截至 '+s.volume_ratio_date:'')+(s.volume_ratio_reason?'；'+s.volume_ratio_reason:'')};}
 function matches(s,filter){
  if(!filter)return true;
  if(filter==='insufficient'||filter==='stale'||filter==='unavailable')return s?.status===filter;
  if(!current(s))return false;
  return filter==='above'?s.above_now===true:filter==='below'?s.above_now===false:filter==='two'?s.two_weeks_above===true:filter==='four'?s.total===5&&s.above>=4:true;
 }
 function compare(a,b,sort){
  const key=sort.startsWith('strength')?'strength_score':sort.startsWith('streak')?'consecutive_above_weeks':'distance_30w';
  const x=current(a.weeklySignal)?a.weeklySignal[key]:null,y=current(b.weeklySignal)?b.weeklySignal[key]:null;
  if(!known(x))return known(y)?1:0;if(!known(y))return -1;
  return (sort.endsWith('_asc')?1:-1)*(x-y);
 }
 window.ObserverMemberStrength={cells,ratioCell,matches,compare};
})();
