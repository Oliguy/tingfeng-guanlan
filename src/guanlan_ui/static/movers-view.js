/* Grouped table rendering, scoped label buttons, and quick-jump navigation. */
(()=>{'use strict';
window.createMoversView=({$,onStock,onTag,onJump,onLeader})=>{
 const {esc,money,pct}=ObserverFormat;
 const label=(theme,tag,text)=>`<button class="movers-tag ${tag?'movers-tag-path':'movers-tag-theme'}" data-theme="${esc(theme)}" data-tag="${esc(tag||'')}">${esc(text)}</button>`;
 function render(data,filter,leader){
  if(filter.page==='leader')return ObserverMoversLeaderView.render(data,filter,leader,$);
  const display=ObserverMoversModel.visible(data,filter,leader),stats=data.stats;
  $('moversStats').innerHTML=`<span>当日异动 <b>${stats.total}</b></span><span class="positive">上涨 <b>${stats.up}</b></span><span class="negative">下跌 <b>${stats.down}</b></span><span class="movers-gold">已核验涨停 <b>${stats.limit_up}</b></span><span>${stats.limit_unknown?`待核验 ${stats.limit_unknown} 只`:'涨停证据已齐'}</span>`;
  $('moversCount').textContent=`筛选 ${display.count} 只 / 当日 ${stats.total} 只`;
  $('moversDirectory').innerHTML=display.groups.map((g,i)=>`<button data-jump="movers-group-${i}"><span>${esc(g.name)}</span><b>${g.rows.length}</b></button>`).join('')||'<p>无匹配板块</p>';
  $('moversGroups').innerHTML=display.groups.length?`<table class="movers-table"><colgroup><col class="movers-board-col"><col class="movers-stock-col"><col class="movers-change-col"><col class="movers-limit-col"><col><col class="movers-amount-col"></colgroup><thead><tr><th>板块 / 题材</th><th>股票</th><th>当日涨跌</th><th>收盘标记</th><th>所属板块 · 题材标签</th><th>成交额</th></tr></thead><tbody>${display.groups.map((g,index)=>g.rows.map((r,i)=>{
   const status=r.limit.status,up=g.rows.filter(r=>r.pct_chg>7).length,confirmed=g.rows.filter(r=>r.limit.status==='confirmed_up').length;
   const info=leader?.scores?.[r.code],group=leader?.groups?.[`${filter.axis}:${g.id}`];
   const rank=leader?.ranks?.[`${filter.axis}:${g.id}`]?.[r.code];
   const groupKey=`${filter.axis}:${g.id}`;
   const scoreButton=info?`<button class="leader-score" data-leader="${esc(r.code)}" data-leader-group="${esc(groupKey)}" title="查看观察评分依据">${info.score==null?`${info.lower.toFixed(0)}–${info.upper.toFixed(0)}?`:info.score.toFixed(1)+'分'+(rank?' · 组内第'+rank:'')}${group?.candidate===r.code?' · 候选':''}</button>`:'';
   const boardButton=info?.n_m_label?`<button class="leader-board" data-leader="${esc(r.code)}" data-leader-group="${esc(groupKey)}" title="查看N天M板逐日依据">${esc(info.n_m_label)}</button>`:'';
   const tags=r.themes.map(t=>label(t.id,'',t.name)+t.paths.map(p=>{const leaf=t.tags.find(l=>l.path.length===p.length&&l.path.every((v,i)=>p[i]===v));return label(t.id,leaf?.id,p.join(' / '));}).join('')).join('');
   const boards=[...r.boards,...r.subboards].map(b=>`<span class="movers-board-label">${esc(b.path?.join(' / ')||b.name)}</span>`).join('');
   return `<tr${i===0?` id="movers-group-${index}" class="movers-group-start"`:''}>${i===0?`<td rowspan="${g.rows.length}" class="movers-board"><strong>${esc(g.name)}</strong><span>${g.rows.length}只异动</span><small><span class="positive">↑${up}</span> <span class="negative">↓${g.rows.length-up}</span></small><small class="movers-gold">已核验涨停 ${confirmed}</small>${group?`<small>板块热度 ${group.heat==null?'待核验':group.heat.toFixed(1)} · 评分覆盖 ${(group.score_coverage*100).toFixed(0)}% · ${group.member_count}只成员</small><small>${group.candidate?'短线候选 '+esc(group.candidate)+(group.candidate_close?'（领先接近）':''):'暂无达标短线候选'}</small>`:''}</td>`:''}<td><button data-stock="${esc(r.code)}" class="movers-stock-button"><strong>${esc(r.name)}</strong><code>${esc(r.code)}</code></button>${scoreButton}${r.quality!=='available'?'<small>行情异常，暂不可绘图</small>':''}</td><td class="${r.pct_chg>0?'positive':'negative'} movers-change">${pct(r.pct_chg/100)}</td><td><span class="movers-limit ${status==='confirmed_up'?'movers-limit-confirmed':''}" title="${esc(r.limit.sources.map(s=>s.endpoint+' '+s.trade_date).join(' · ')||'尚无对应日期证据')}">${status==='confirmed_up'?'涨停':status==='not_limit'?'—':esc(r.limit.label)}</span>${boardButton}</td><td class="movers-tags">${boards}${tags||'<span class="movers-muted">未录入题材</span>'}</td><td class="movers-amount">${money(r.amount)}</td></tr>`;
  }).join('')).join('')}</tbody></table>`:'<div class="movers-empty">当前筛选无匹配异动。可切换方向、清除标签或搜索条件。</div>';
  $('clearTag').hidden=!filter.theme;if(filter.theme){const theme=(data.theme_catalog||data.rows.flatMap(r=>r.themes)).find(t=>t.id===filter.theme&&(!filter.tag||t.tags.some(l=>l.id===filter.tag)));const tag=theme?.tags.find(t=>t.id===filter.tag);$('clearTag').textContent=(theme?.name||'已移出目录的题材')+(tag?' / '+tag.path.join(' / '):'')+' ×';}
  document.querySelectorAll('[data-axis]').forEach(b=>b.classList.toggle('active',(filter.axis==='theme'?'theme':'industry')===b.dataset.axis));$('boardLevel').disabled=filter.axis==='theme';
  return display;
 }
 $('moversGroups').addEventListener('click',event=>{const leader=event.target.closest('[data-leader]'),stock=event.target.closest('[data-stock]'),tag=event.target.closest('[data-theme]');if(leader)onLeader(leader.dataset.leader,leader.dataset.leaderGroup);else if(stock)onStock(stock.dataset.stock);else if(tag)onTag(tag.dataset.theme,tag.dataset.tag);});
 $('moversDirectory').addEventListener('click',event=>{const b=event.target.closest('[data-jump]');if(b)onJump(b.dataset.jump);});
 return {render};
};
})();
