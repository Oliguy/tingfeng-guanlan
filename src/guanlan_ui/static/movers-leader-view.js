/* Separate anomaly leader page. The score is supplied by the existing read-only API. */
(()=>{'use strict';
const {esc,pct}=ObserverFormat;
function render(data,filter,leader,$){
 if(!leader){$('moversStats').textContent=data.leader_error?'五日评分暂不可用':'五日异动池读取中…';$('moversCount').textContent='评分截至 '+data.date;$('moversDirectory').innerHTML='';$('moversGroups').innerHTML=`<div class="movers-empty">${data.leader_error?'五日评分读取失败：'+esc(data.leader_error)+'。可点击重试评分。':'正在读取近5个交易日异动并计算评分，完成后显示完整范围。'}</div>`;return {groups:[],count:0,order:[]};}
 const display=ObserverMoversModel.visible(data,filter,leader),stats=data.stats;
 $('moversStats').innerHTML=`<span>五日异动 <b>${stats.total}</b></span><span>下限后保留 <b>${display.floor_count}</b></span><span>低于下限隐藏 <b>${display.hidden_count}</b></span><span>待核验 <b>${display.uncertain_count}</b></span>`;
 $('moversCount').textContent=`${data.dates?.[0]||data.date} 至 ${data.date} · 当前筛选 ${display.count} 只 / 五日池 ${stats.total} 只 · 评分截至 ${data.date}`+(data.complete?'':` · 窗口不完整${data.missing_dates?.length?'，缺 '+data.missing_dates.join('、'):''}`);
 $('moversDirectory').innerHTML=display.groups.map((g,i)=>`<button data-jump="movers-group-${i}"><span>${esc(g.name)}</span><b>${g.rows.length}</b></button>`).join('')||'<p>无匹配板块</p>';
 $('moversGroups').innerHTML=display.groups.map((g,index)=>{
  const key=`${filter.axis}:${g.id}`,group=leader?.groups?.[key];
  const heat=group?.heat==null?'待核验':group.heat.toFixed(1);
  const coverage=group?.score_coverage==null?'待核验':`${(group.score_coverage*100).toFixed(0)}%`;
  const candidate=group?.candidate?`短线候选 ${esc(group.candidate)}${group.candidate_close?'（领先接近）':''}`:'暂无达标短线候选';
  return `<section class="leader-group" id="movers-group-${index}" aria-label="${esc(g.name)}龙头评分"><header><div><h2>${esc(g.name)}</h2><span>${g.rows.length}只异动 · 板块热度 ${heat} · 评分覆盖 ${coverage}${group?.member_count!=null?` · ${group.member_count}只成员`:''}</span></div><small>${leader?candidate:'评分计算中…'}</small></header><div class="leader-rows">${g.rows.map(r=>{
   const info=leader?.scores?.[r.code],rank=leader?.ranks?.[key]?.[r.code];
   const scoreText=!info?'待核验':info.score==null?`待核验 · ${info.lower_display??info.lower.toFixed(1)}–${info.upper_display??info.upper.toFixed(1)}`:`${info.score_display??info.score.toFixed(1)}分${rank?' · 组内第'+rank:''}`;
   const scoreControl=info?`<button class="leader-score" data-leader="${esc(r.code)}" data-leader-group="${esc(key)}" aria-label="查看${esc(r.name)}评分依据">${scoreText}</button>`:`<span class="movers-muted">${scoreText}</span>`;
   const boardControl=info?.n_m_label?`<button class="leader-board" data-leader="${esc(r.code)}" data-leader-group="${esc(key)}" aria-label="查看${esc(r.name)}N天M板逐日依据">${esc(info.n_m_label)}</button>`:`<span class="movers-muted">${leader?'待核验或不足2板':'计算中…'}</span>`;
   const limit=r.limit.status==='confirmed_up'?'涨停':r.limit.status==='not_limit'?'未涨停':r.limit.label;
   const protection=r.first_board==='yes'&&info?.upper<filter.floor?'首板保留':r.first_board==='unknown'?'首板身份待核':'';
   return `<div class="leader-row"><button data-stock="${esc(r.code)}" class="movers-stock-button"><strong>${esc(r.name)}</strong><code>${esc(r.code)}</code><small>最近异动 ${esc(r.event_date||data.date)}</small><small class="${r.pct_chg>0?'positive':'negative'}">${pct(r.pct_chg/100)} · ${esc(limit)}</small></button><div><small>截至 ${esc(data.date)} 的观察分</small>${scoreControl}${protection?`<small>${esc(protection)}</small>`:''}</div><div><small>N天M板</small>${boardControl}</div><div class="leader-row-action"><button class="leader-open-chart" data-stock="${esc(r.code)}">查看K线</button></div></div>`;
  }).join('')}</div></section>`;
 }).join('')||'<div class="movers-empty">当前筛选无匹配异动。可调整分组、方向或搜索条件。</div>';
 $('clearTag').hidden=!filter.theme;
 if(filter.theme){const theme=(data.theme_catalog||data.rows.flatMap(r=>r.themes)).find(t=>t.id===filter.theme&&(!filter.tag||t.tags.some(l=>l.id===filter.tag)));const tag=theme?.tags.find(t=>t.id===filter.tag);$('clearTag').textContent=(theme?.name||'已移出目录的题材')+(tag?' / '+tag.path.join(' / '):'')+' ×';}
 document.querySelectorAll('[data-axis]').forEach(b=>b.classList.toggle('active',(filter.axis==='theme'?'theme':'industry')===b.dataset.axis));$('boardLevel').disabled=filter.axis==='theme';
 return display;
}
window.ObserverMoversLeaderView={render};
})();
