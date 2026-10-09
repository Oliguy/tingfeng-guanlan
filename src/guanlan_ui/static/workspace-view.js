/* DOM rendering for ReaderDisplay v1. No source-specific fields or HTTP calls. */
(()=>{'use strict';
const {esc,pct}=ObserverFormat;
const targetKey=t=>t?`${t.kind}:${t.id}`:'';
const cell=c=>c?.type==='strength'?`<td><span class="strength-chip" data-strength="${c.signal?.total===5?c.signal.above:'unknown'}">${esc(c.value)}</span>${goldBadge(c.signal||{})}</td>`:c?.type==='products'?`<td class="product-cell" title="${esc(c.values.join('、')+(c.title?'\n'+c.title:''))}"><div class="product-copy">${esc(c.values.join('、')||'暂无产品资料')}</div></td>`:c?.type==='tags'?'<td class="tag-cell">'+c.values.map(v=>'<span class="member-tag">'+esc(v)+'</span>').join('')+'</td>':typeof c==='object'&&c!==null?`<td title="${esc(c.title||'')}" class="${c.tone>0?'positive':c.tone<0?'negative':''}">${esc(c.value)}</td>`:`<td>${esc(c)}</td>`;
function table(headers,rows,selected){return '<table><thead><tr>'+headers.map(h=>`<th${h==='主要产品'?' class="product-header"':''}>${esc(h)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>`<tr ${r.target?`tabindex="0" data-stock="${esc(r.target.id)}" aria-label="查看${esc(r.cells[1]||r.target.id)}K线" class="${targetKey(r.target)===targetKey(selected)?'selected':''}"`:''}>${r.cells.map(cell).join('')}</tr>`).join('')+'</tbody></table>';}
function blocks(values=[],term=''){
 return values.map(b=>{
  if(b.type==='table')return table(b.headers,b.rows.filter(r=>!r.search||r.search.includes(term)));
  if(b.type==='details')return `<details><summary>${esc(b.title)}</summary>${blocks(b.blocks,term)}</details>`;
  if(b.type==='heading')return `<h3>${esc(b.value)}</h3>`;
  if(b.type==='link'){try{const u=new URL(b.url);return ['https:','http:'].includes(u.protocol)?`<a href="${esc(u.href)}" target="_blank" rel="noopener">${esc(b.label)}</a>`:'';}catch{return '';}}
  return `<p>${esc(b.value).replace(/\n/g,'<br>')}</p>`;
 }).join('');
}
function goldBadge(s){if(s.two_weeks_above!==true)return '';return `<span class="gold-badge" role="img" aria-label="最近连续2周站上30周线" title="${esc((s.recent_weeks||[]).map(w=>w.trade_date).join('、'))} 两周均站上30周线；末周按已存行情计算"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m8 1.5 2 4.1 4.5.7-3.2 3.1.7 4.5-4-2.1-4 2.1.7-4.5L1.5 6.3 6 5.6Z"/></svg></span>`;}
function streakLabel(s){const n=s.consecutive_above_weeks,known=Number.isInteger(n)&&n>=0,lower=s.streak_is_lower_bound===true;const value=known?`${lower?'至少 ':''}${n} 周`:'暂不可算';const reason=known?(lower?'更早历史不足或缺周，显示可确认的连续周数下限。':'从最新一周向前连续计数，收盘价严格高于各自30周线。'):'最新周缺少有效收盘价或30周线。';return `<span class="weekly-streak" title="${reason}末周按已存行情计算。">连续站上30周线：<b>${value}</b></span>`;}
window.createWorkspaceView=({state:S,$,onSelect,onTreeChange})=>{
 const directory=createObserverDirectory({state:S,$,onSelect,onChange:onTreeChange,badge:goldBadge});
 const objects=directory.render;
 function members(){
  const focused=document.activeElement?.dataset?.stock,scroll=$('detailContent').scrollTop,term=$('memberSearch').value.trim().toLowerCase(),sort=$('memberSort').value;
  const m=S.detail?.members||S.parentDetail?.members;
  if(!m){$('memberTitle').textContent='';$('memberTable').innerHTML='<p>选择行业后查看成分股。</p>';return;}
  const filter=$('memberTag'),selectedTag=filter.value;
  filter.hidden=!m.tags?.length;filter.innerHTML='<option value="">全部标签</option>'+(m.tags||[]).map(t=>`<option value="${esc(t.id)}">${esc(t.path.join(' / '))}</option>`).join('');filter.value=selectedTag;
  const strengthFilter=$('memberStrengthFilter');strengthFilter.hidden=!m.signalsAvailable;const rule=strengthFilter.hidden?'':strengthFilter.value;
  const rows=m.rows.filter(r=>r.search.includes(term)&&(!filter.value||r.tags?.includes(filter.value))&&ObserverMemberStrength.matches(r.weeklySignal,rule));
  $('memberTitle').textContent=m.title+(filter.value||term||rule?` · 筛选 ${rows.length}只（不改变指数）`:'');
  if(/^(strength|streak|distance)_/.test(sort))rows.sort((a,b)=>ObserverMemberStrength.compare(a,b,sort));
  else if(sort==='name')rows.sort((a,b)=>(a.name||a.cells[1]).localeCompare(b.name||b.cells[1],'zh-CN'));
  else if(sort==='tagOrder')rows.sort((a,b)=>(a.tagOrder??9999)-(b.tagOrder??9999));
  else if(sort!=='default')rows.sort((a,b)=>(b[sort]??-Infinity)-(a[sort]??-Infinity));
  $('retryMemberSignals').hidden=m.signalState!=='error';
  $('memberSignalStatus').textContent=m.signalsAvailable?(m.signalState==='pending'?'正在读取成分股30周线强弱…':m.signalState==='error'?'强弱读取失败，请点重新读取':`30周线按前复权及已存行情判断；末周可能未结束 · ${rows.length}/${m.rows.length}只`):'';
  $('memberTable').innerHTML=(rows.length?table(m.headers,rows,S.target):'<p>没有匹配的成分股</p>')+blocks(m.extra,term);
  $('memberTable').querySelectorAll('[data-stock]').forEach(row=>{row.onclick=()=>onSelect({kind:'stock',id:row.dataset.stock});row.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();row.click();}else if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const next=e.key==='ArrowDown'?row.nextElementSibling:row.previousElementSibling;if(next){next.focus();next.click();}}};});
  if(focused)$('memberTable').querySelector(`[data-stock="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});$('detailContent').scrollTop=scroll;
 }
 function detail(renderMemberList=true){
  const d=S.detail;for(const [id,value] of Object.entries({instrumentName:d.title,instrumentCode:d.code,parentName:d.parentLabel,instrumentMeta:d.meta,dataDate:'行情截至 '+d.date,detailStatus:d.status,priceUnit:d.unitLabel}))$(id).textContent=value;
  $('signal').textContent=d.signal?.status&&d.signal.status!=='ready'?({stale:'个股行情滞后',insufficient:'30周历史不足',unavailable:'强弱不可用'}[d.signal.status]||''):d.signal?(d.signal.total===5?`近5周 ${d.signal.above}/5 站上30周线`:'30周历史不足'):'';
  $('chartEmpty').textContent='此对象暂无可用行情';$('chartEmpty').hidden=!!d.chart.bars.length;
  $('metricsPanel').innerHTML='<div class="metric-grid">'+d.metrics.map(([k,v])=>`<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')+'</div><div class="info">'+blocks(d.metricNotes)+'</div>';
  $('infoPanel').innerHTML='<div class="info">'+blocks(d.info)+'</div>';if(renderMemberList)members();
 }
 function loading(target,newParent){
  $('detailStatus').textContent='读取本地行情…';$('instrumentName').textContent=target.id;
  for(const id of ['instrumentCode','instrumentMeta','signal','dataDate','parentName','chartMethod'])$(id).textContent='';
  $('retryDetail').hidden=true;$('chartEmpty').hidden=true;$('metricsPanel').replaceChildren();$('infoPanel').replaceChildren();if(newParent)$('memberTable').replaceChildren();
 }
 function error(message){$('detailStatus').textContent=message;$('chartEmpty').textContent=message;$('chartEmpty').hidden=false;$('retryDetail').hidden=false;members();}
 return {objects,members,detail,loading,error};
};
})();
