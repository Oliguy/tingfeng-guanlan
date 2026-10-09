/* Pure anomaly display adapter. Group references never become new events. */
(()=>{'use strict';
function visible(data,filter,leader){
 const pool=data.rows, floor=filter.page==='leader'&&leader?Number(filter.floor??35):0;
 const aboveFloor=r=>{const info=leader?.scores?.[r.code];return !(Number.isFinite(info?.upper)&&info.upper<floor&&(r.first_board??info?.first_board)==='no');};
 const floorRows=pool.filter(aboveFloor);
 const selected=new Map(floorRows.filter(r=>{
  if(filter.direction==='up'&&r.pct_chg<=7||filter.direction==='down'&&r.pct_chg>=-7)return false;
  if(filter.direction==='limit'&&r.limit.status!=='confirmed_up'||filter.direction==='unknown'&&!['unknown','conflict'].includes(r.limit.status))return false;
 if(filter.theme&&!r.themes.some(t=>t.id===filter.theme&&(!filter.tag||t.tags.some(l=>l.id===filter.tag))))return false;
  if(filter.complete_only&&leader&&leader.scores?.[r.code]?.score==null)return false;
  const haystack=[r.code,r.name,...r.boards.map(b=>b.name),...r.subboards.map(b=>b.name),...r.themes.flatMap(t=>[t.name,...t.paths.map(p=>p.join(' / '))])].join(' ').toLowerCase();
  return haystack.includes(filter.search.toLowerCase().trim());
 }).map(r=>[r.code,r]));
 const tagOrder=r=>Math.min(...r.themes.filter(t=>!filter.theme||t.id===filter.theme).flatMap(t=>t.tags.filter(l=>r.themes.find(v=>v.id===t.id).paths.some(p=>p.join('/')===l.path.join('/'))).map(l=>l.order)),99999);
 const boardCount=r=>leader?.scores?.[r.code]?.boards?.known_boards??-1;
 const score=r=>leader?.scores?.[r.code]?.score??leader?.scores?.[r.code]?.lower??-1;
 const comparators={absolute:(a,b)=>Math.abs(b.pct_chg)-Math.abs(a.pct_chg),change:(a,b)=>b.pct_chg-a.pct_chg,drop:(a,b)=>a.pct_chg-b.pct_chg,amount:(a,b)=>(b.amount??-1)-(a.amount??-1),name:(a,b)=>a.name.localeCompare(b.name,'zh-CN'),tag:(a,b)=>tagOrder(a)-tagOrder(b),leader:(a,b)=>score(b)-score(a),boards:(a,b)=>boardCount(b)-boardCount(a)};
 const groups=data.groups[filter.axis].map(g=>({...g,rows:g.codes.map(c=>selected.get(c)).filter(Boolean).sort((a,b)=>(comparators[filter.sort]||comparators.absolute)(a,b)||a.code.localeCompare(b.code))})).filter(g=>g.rows.length);
 return {groups,count:selected.size,pool_count:pool.length,floor_count:floorRows.length,hidden_count:pool.length-floorRows.length,
         uncertain_count:pool.filter(r=>leader?.scores?.[r.code]?.score==null).length,
         order:[...new Set(groups.flatMap(g=>g.rows.map(r=>r.code)))]};
}
function stock(raw){
 const result=ObserverModel.detail(raw);result.meta=`${raw.event.date} 异动 · ${ObserverFormat.pct(raw.event.pct_chg/100)} · ${raw.event.limit.label}`;
 result.parentLabel=raw.event.date+' 异动';result.chart.markers=[{date:raw.event.date,label:'异动',color:'#eac777'}];
 result.status=result.status.replace('个停牌日 · ','个已确认停牌日 · ');result.metrics.at(-1)[0]='观察日期';
 return result;
}
window.ObserverMoversModel={visible,stock};
})();
