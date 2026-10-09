/* Compact directory and sibling navigation for normalized ReaderDisplay objects. */
(()=>{'use strict';
 const {esc,pct}=ObserverFormat;
 window.createObserverDirectory=({state:S,$,onSelect,onChange,badge})=>{
  function render(){
   if(!S.summary)return;
   const all=S.summary.objects,rows=all.filter(r=>r.section==='groups'),index=new Map(rows.map(r=>[r.target.id,r]));
   const roots=rows.filter(r=>!r.parentId).sort((a,b)=>a.target.id.localeCompare(b.target.id));
   const ancestors=r=>{const path=[r];let p=index.get(r.parentId);while(p&&!path.includes(p)){path.unshift(p);p=index.get(p.parentId);}return path;};
   const families=roots.filter(r=>rows.some(n=>n.parentId===r.target.id));
   const industry=S.module==='industry30',sub=industry&&S.directoryMode!=='industry',term=$('objectSearch').value.trim().toLowerCase();
   const match=r=>[...ancestors(r).map(n=>n.name),r.search].join(' ').toLowerCase().includes(term);
   const isSelected=r=>S.target?.kind==='stock'?r.target.id===S.parent:r.target.kind===S.target?.kind&&r.target.id===S.target?.id;
   const ordered=list=>industry?list.slice().sort((a,b)=>a.target.id.localeCompare(b.target.id)):list.slice().sort((a,b)=>{
    const mode=$('objectSort').value;if(mode==='default')return 0;const av=a.signal.strength_score,bv=b.signal.strength_score;
    return av==null?(bv==null?0:1):bv==null?-1:(mode==='weakness'?1:-1)*(av-bv||(a.signal.distance_30w||0)-(b.signal.distance_30w||0));
   });
   function row(r){const s=r.signal||{},n=s.total===5?s.above:null,streak=Number.isInteger(s.consecutive_above_weeks)?(s.streak_is_lower_bound?'≥':'')+s.consecutive_above_weeks+'周':'—';
    return `<button class="object-row ${isSelected(r)?'selected':''}" data-kind="${r.target.kind}" data-id="${esc(r.target.id)}" aria-pressed="${isSelected(r)}" title="${esc(ancestors(r).map(n=>n.name).join(' / '))} · ${esc(r.caption)}"><span class="object-name">${esc(r.name)}${badge(s)}</span><span class="strength-chip" data-strength="${n??'unknown'}">${n==null?'—':n+'/5'}</span><span class="streak-value" title="连续站上30周线；≥表示可确认的下限">${streak}</span><span class="row-change ${r.change>0?'positive':r.change<0?'negative':''}">${pct(r.change)}</span></button>`;
   }
   $('directoryModes').hidden=!industry;$('objectSort').hidden=industry;$('familyFilters').hidden=!sub;
   $('directoryModes').querySelectorAll('[data-directory]').forEach(b=>{b.setAttribute('aria-pressed',b.dataset.directory===(sub?'sub':'industry'));b.onclick=()=>{S.directoryMode=b.dataset.directory;render();$('objectList').scrollTop=0;onChange();};});
   $('familyFilters').innerHTML=[['all','全部'],...families.map(r=>[r.target.id,r.name==='电子元件与消费电子'?'PCB':r.name==='化工与新材料'?'材料':r.name])].map(([id,name])=>`<button data-family="${id}" aria-pressed="${!term&&S.family===id}">${esc(name)}</button>`).join('');
   $('familyFilters').querySelectorAll('[data-family]').forEach(b=>b.onclick=()=>{S.family=b.dataset.family;$('objectSearch').value='';render();$('objectList').scrollTop=0;onChange();});
   const scroll=$('objectList').scrollTop;let html='';
   if(sub){for(const family of families){if(!term&&S.family!=='all'&&S.family!==family.target.id)continue;const children=ordered(rows.filter(r=>r.parentId&&ancestors(r)[0]===family&&match(r)));if(!children.length)continue;html+=`<section class="directory-group"><h3><button data-kind="group" data-id="${family.target.id}">${esc(family.name)}</button><span>${children.length}个子板块</span></h3>${children.map(row).join('')}</section>`;}}
   else {const focus=all.filter(r=>r.section==='focus'&&match(r));if(focus.length)html+='<h3>重点ETF</h3>'+ordered(focus).map(row).join('')+'<h3>行业与主题</h3>';html+=ordered(roots.filter(match)).map(row).join('');}
   $('objectList').innerHTML=html||'<p class="empty">没有匹配的观察对象</p>';$('objectList').scrollTop=scroll;
   $('objectList').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>onSelect({kind:b.dataset.kind,id:b.dataset.id},{newParent:true}));
   $('directoryCount').textContent=$('objectList').querySelectorAll('.object-row').length+'项';
   const active=index.get(S.target?.kind==='stock'?S.parent:S.target?.id),parent=active&&(index.get(active.parentId)||active),siblings=parent?rows.filter(r=>r.parentId===parent.target.id):[];
   $('siblingNavigation').hidden=!industry||!siblings.length;
   $('siblingNavigation').innerHTML=parent?'<span>切换观察</span>'+[parent,...ordered(siblings)].map(r=>`<button data-id="${r.target.id}" aria-pressed="${r===active}">${r===parent?'整体 · '+esc(r.name):esc(r.name)}</button>`).join(''):'';
   $('siblingNavigation').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>onSelect({kind:'group',id:b.dataset.id},{newParent:true}));
  }
  return {render};
 };
})();
