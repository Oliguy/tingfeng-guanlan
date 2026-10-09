(()=>{'use strict';
const root=document.getElementById('workspace'),$=id=>document.getElementById(id),module=document.body.dataset.observerModule,board=module!=='etf';
const requestedThemeManager=new URLSearchParams(location.search).get('manage')==='1';
const model=ObserverModel,transport=createObserverTransport(module);
const key=`stockObserver:${module}:workspace:v2`;let prefs={};try{prefs=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}
const preferences=window.ObserverPreferences,initialPreferences=preferences.read(prefs.views?.[prefs.target?`${prefs.target.kind}:${prefs.target.id}`:'']);
const S={directoryMode:prefs.directoryMode==='industry'?'industry':'sub',family:prefs.family||'all',expanded:prefs.expanded||{},memberViews:prefs.memberViews||{},module,target:null,parent:null,parentDetail:null,summary:null,detail:null,views:prefs.views||{},tab:['members','metrics','info'].includes(prefs.tab)?prefs.tab:'members',
 barPeriod:'daily',barMode:'adjusted',barRange:150,panOffset:0,volumeMode:'mean_volume',showZhixing:true,showThirtyWeek:true,showBbi:true,
 seq:0,loadState:'loading'};
for(const field of preferences.layers)S[field]=initialPreferences[field];
const chart=createWorkspaceChart({state:S,$,root,preferences,onViewChange:()=>save()});
const view=createWorkspaceView({state:S,$,onSelect:(...args)=>select(...args),onTreeChange:()=>save()});
const renderObjects=view.objects,renderMembers=view.members;
const memberSignals=createWorkspaceSignals({transport,model,onChanged:()=>renderMembers()});
let toastTimer=null,detailRequest=null,navigation=0;
function toast(text){$('toast').textContent=text;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),4200);}
const viewFields=['barPeriod','barMode','barRange','panOffset','volumeMode'];
function targetKey(t=S.target){return t?`${t.kind}:${t.id}`:'';}
function storeView(){if(S.target)S.views[targetKey()]=Object.fromEntries(viewFields.map(k=>[k,S[k]]));}
function memberState(){return {search:$('memberSearch').value,sort:$('memberSort').value,tag:$('memberTag').value,strength:$('memberStrengthFilter').value,scroll:$('detailContent').scrollTop};}
function save(){storeView();if(S.parent)S.memberViews[S.parent]=memberState();try{localStorage.setItem(key,JSON.stringify({directoryMode:S.directoryMode,family:S.family,expanded:S.expanded,memberViews:S.memberViews,views:S.views,target:S.target,parent:S.parent,tab:S.tab,objectSearch:$('objectSearch').value,sort:$('objectSort').value,objectScroll:$('objectList').scrollTop,memberSearch:$('memberSearch').value,memberSort:$('memberSort').value,memberScroll:$('detailContent').scrollTop,detailsHeight:parseInt($('details').style.getPropertyValue('--details-height'))||240,collapsed:$('details').classList.contains('collapsed')}));}catch{}}
function restoreView(target){const v=S.views[targetKey(target)]||{};S.barPeriod=['daily','weekly'].includes(v.barPeriod)?v.barPeriod:'daily';S.barMode=v.barMode==='raw'?'raw':'adjusted';S.barRange=v.barRange==='all'?'all':Number.isFinite(+v.barRange)&&+v.barRange>=20?Math.min(1000,+v.barRange):150;S.panOffset=Number.isFinite(+v.panOffset)?Math.max(0,+v.panOffset):0;S.volumeMode=['volume','amount','relative_volume_20'].includes(v.volumeMode)?v.volumeMode:'mean_volume';const shared=preferences.read();for(const k of preferences.layers)S[k]=shared[k];if(board&&target.kind==='group')S.barMode='adjusted';}
function setHistory(replace=false){const u=new URL(location.href),fromHome=u.searchParams.get('from')==='home';u.search='';if(fromHome)u.searchParams.set('from','home');u.searchParams.set('kind',S.target.kind);u.searchParams.set('id',S.target.id);if(S.parent)u.searchParams.set('industry',S.parent);history[replace?'replaceState':'pushState']({target:S.target,parent:S.parent},'',u);}
function urlTarget(){const q=new URLSearchParams(location.search);return ['group','etf','stock'].includes(q.get('kind'))&&q.get('id')?{target:{kind:q.get('kind'),id:q.get('id')},parent:q.get('industry')}:null;}
function controls(){
 const index=board&&S.target?.kind==='group';$('priceControls').hidden=index;$('volumeMode').hidden=!index;$('volumeMode').value=S.volumeMode;
 $('backIndustry').hidden=!(board&&S.target?.kind==='stock');$('backIndustry').textContent='返回'+(S.parentDetail?.title||(module==='theme'?'题材':'行业'));
 root.querySelectorAll('[data-period]').forEach(b=>b.classList.toggle('active',b.dataset.period===S.barPeriod));root.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===S.barMode));
 root.querySelectorAll('[data-range]').forEach(b=>{if(b.dataset.range!=='all')b.textContent=b.dataset.range+(S.barPeriod==='weekly'?'周':'日');});
 for(const k of ['showZhixing','showThirtyWeek','showBbi'])$(k).checked=S[k];chart.updateRangeSelection();
}
function renderDetail(){
 const current=S.detail,m=current?.members||S.parentDetail?.members,large=(m?.rows.length||0)>200;
 view.detail(!large);controls();chart.drawCurrentBars();
 if(large){
  $('memberTitle').textContent=m.title;
  // Let the chart paint before creating a large table. Navigation fences the
  // deferred render, so an old group cannot replace a newly selected object.
  requestAnimationFrame(()=>setTimeout(()=>{if(S.detail===current)renderMembers();},0));
 }
}
async function select(target,{newParent=false,push=true,keepView=false,prepared=null}={}){
 navigation++;storeView();if(S.parent)S.memberViews[S.parent]=memberState();detailRequest?.abort();detailRequest=new AbortController();const request=detailRequest,seq=++S.seq;S.target={...target};if(newParent){S.parent=board&&target.kind==='group'?target.id:null;S.parentDetail=null;const mv=S.memberViews[S.parent]||{};$('memberSearch').value=mv.search||'';$('memberSort').value=mv.sort==='amount'?'volumeRatio':mv.sort||'default';$('memberStrengthFilter').value=mv.strength||'';$('memberTag').replaceChildren(new Option('全部标签',''));if(mv.tag)$('memberTag').add(new Option(mv.tag,mv.tag));$('memberTag').value=mv.tag||'';$('detailContent').scrollTop=0;}
 if(!keepView)restoreView(target);S.loadState='loading';S.detail=null;chart.drawCurrentBars();chart.clearCrosshair();controls();renderObjects();
 view.loading(target,newParent);
 if(push)setHistory();
 try{
  const params=model.request(module,target,S,S.summary,S.parent);
  let raw=prepared?await prepared:null;if(seq!==S.seq)return;
  // A concurrent menu read pins the publication. Discard a prefetch from any
  // other publication/period instead of mixing the list and main chart.
  if(!model.matchesRequest(raw,params))raw=await transport.query(params,{signal:request.signal});if(seq!==S.seq)return;
  const data=model.detail(raw);
  if(data.target?.kind!==target.kind||data.target?.id!==target.id)throw Error('返回行情与当前对象不一致');
  S.detail=data;if(data.capabilities.memberChart)S.parentDetail=data;S.loadState=data.chart.bars.length?'ready':'empty';renderDetail();if(data.members?.signalsAvailable)memberSignals.load(data);if(newParent)$('detailContent').scrollTop=S.memberViews[S.parent]?.scroll||0;
  if(innerWidth<=820&&newParent)setObjects(false);save();ObserverHomeNavigation.remember({module,kind:S.target.kind,id:S.target.id,industry:S.parent,name:data.title});
 }catch(e){if(seq!==S.seq)return;S.loadState='error';chart.drawCurrentBars();view.error(e.message);}
}
function tab(name){S.tab=name;root.querySelectorAll('[data-tab]').forEach(b=>{const selected=b.dataset.tab===name;b.setAttribute('aria-selected',selected);b.tabIndex=selected?0:-1;});for(const n of ['member','metrics','info'])$(n+'Panel').hidden=(n==='member'?'members':n)!==name;}
function setObjects(open){root.classList.toggle('objects-hidden',!open);$('showObjects').setAttribute('aria-expanded',open);}
function detailHeight(value){const h=Math.max(140,Math.min(500,innerHeight*.48,value));$('details').style.setProperty('--details-height',h+'px');$('detailResizer').setAttribute('aria-valuenow',Math.round(h));}
async function load(){
 const turn=++navigation;
 const chosen=urlTarget()||{target:prefs.target,parent:prefs.parent},known=chosen.target;
 const prefetchController=new AbortController();
 const prior=known?S.views[targetKey(known)]||{}:{};
 const prepared=board&&known?.kind==='group'?transport.query({view:'detail',target:known,period:prior.barPeriod==='weekly'?'weekly':'daily',price_mode:'adjusted',limit:1000},{signal:prefetchController.signal}).catch(()=>null):null;
 try{
  const summary=await transport.query({view:'summary'});if(turn!==navigation)return;
  S.summary=model.summary(summary,module);$('connection').textContent='已连接';$('freshness').textContent=(module==='theme'?'题材':board?'行业':'ETF')+'行情截至 '+(S.summary.asOf||'暂无')+' · 浏览不采集';
  let t=chosen.target;S.parent=chosen.parent;
  const objects=S.summary.objects;
  if(new URLSearchParams(location.search).get('from')==='home'&&known&&!objects.some(r=>targetKey(r.target)===targetKey(known)||(known.kind==='stock'&&r.target.id===chosen.parent))){location.replace('/home/?unavailable=1');return;}
  if(board&&t?.kind==='stock'&&objects.some(r=>r.target.id===S.parent)){
   const parent=model.detail(await transport.query(model.request(module,{kind:'group',id:S.parent},{barPeriod:'daily',barMode:'adjusted'},S.summary)));
   if(turn!==navigation)return;
   if(parent.members.rows.some(m=>m.target?.id===t.id))S.parentDetail=parent;else if(new URLSearchParams(location.search).get('from')==='home'){location.replace('/home/?unavailable=1');return;}else t={kind:'group',id:S.parent};
  }else if(!objects.some(r=>targetKey(r.target)===targetKey(t)))t=objects[0]?.target||null;
  if(board&&t?.kind==='group')S.parent=t.id;const mv=S.memberViews[S.parent];if(mv){$('memberSearch').value=mv.search||'';$('memberSort').value=mv.sort==='amount'?'volumeRatio':mv.sort||'default';$('memberStrengthFilter').value=mv.strength||'';$('memberTag').replaceChildren(new Option('全部标签',''));if(mv.tag)$('memberTag').add(new Option(mv.tag,mv.tag));$('memberTag').value=mv.tag||'';}renderObjects();if(t){if(board&&t.kind==='group')S.parent=t.id;await select(t,{push:false,prepared});setHistory(true);}else{S.target=null;S.parent=null;S.detail=null;S.parentDetail=null;S.loadState='empty';view.loading({id:'暂无观察对象'},true);$('detailStatus').textContent=module==='theme'?'从“管理题材”新建或恢复题材':'';chart.drawCurrentBars();controls();}
  $('objectList').scrollTop=Number(prefs.objectScroll)||0;$('detailContent').scrollTop=Number(prefs.memberScroll)||0;
 }catch(e){if(turn!==navigation)return;S.loadState='error';chart.drawCurrentBars();$('connection').textContent='读取异常';view.error(e.message);}
 finally{prefetchController.abort();}
}
const updates=createObserverUpdates({module,$,invoke:transport.invoke,getTarget:()=>S.target,toast,onChanged:async()=>{save();try{prefs=JSON.parse(localStorage.getItem(key)||'{}');}catch{}await load();}});
document.querySelectorAll('[data-observer]').forEach(a=>{if(a.dataset.observer===module)a.setAttribute('aria-current','page');a.onclick=save;});
$('memberSort').hidden=!board;$('objectsTitle').textContent=module==='theme'?'题材库':board?'行业目录':'ETF观察';$('showObjects').textContent=module==='theme'?'题材列表':board?'行业列表':'ETF列表';$('tabMembers').textContent=board?'成分股':'成员 / 持仓';
$('objectSearch').value=prefs.objectSearch||'';$('objectSort').value=prefs.sort||'strength';$('memberSearch').value=prefs.memberSearch||'';$('memberSort').value=prefs.memberSort==='amount'?'volumeRatio':prefs.memberSort||'default';
$('objectSearch').oninput=renderObjects;$('objectSort').onchange=()=>{renderObjects();save();};$('memberSearch').oninput=renderMembers;$('memberSort').onchange=()=>{renderMembers();save();};$('memberTag').onchange=()=>{renderMembers();save();};$('retryMemberSignals').onclick=()=>{const d=S.detail?.members?S.detail:S.parentDetail;if(d){d.members.signalState='pending';renderMembers();memberSignals.load(d,{force:true});}};$('memberStrengthFilter').onchange=()=>{renderMembers();save();};
$('showObjects').onclick=()=>setObjects(root.classList.contains('objects-hidden'));$('hideObjects').onclick=()=>setObjects(false);setObjects(innerWidth>820);
root.querySelectorAll('[data-tab]').forEach((b,i,all)=>{b.onclick=()=>tab(b.dataset.tab);b.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const next=all[(i+(e.key==='ArrowRight'?1:all.length-1))%all.length];next.focus();next.click();}};});tab(S.tab);
$('backIndustry').onclick=()=>select({kind:'group',id:S.parent});$('retryDetail').onclick=()=>S.summary&&S.target?select(S.target,{push:false,keepView:true}):load();
root.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>{S.barPeriod=b.dataset.period;S.panOffset=0;select(S.target,{push:false,keepView:true});});
root.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{S.barMode=b.dataset.mode;select(S.target,{push:false,keepView:true});});
root.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>chart.setChartRange(b.dataset.range));
for(const k of preferences.layers)$(k).onchange=()=>{S[k]=$(k).checked;preferences.set({[k]:S[k]});chart.drawCurrentBars();save();};
$('volumeMode').onchange=()=>{S.volumeMode=$('volumeMode').value;if(S.detail){chart.drawCurrentBars();save();}};
$('panOlder').onclick=()=>chart.panChart(1);$('panNewer').onclick=()=>chart.panChart(-1);$('resetView').onclick=()=>chart.resetChart();$('timelineRange').oninput=e=>{S.panOffset=Number(e.target.max)-Number(e.target.value);chart.drawCurrentBars();};$('zoomRange').oninput=e=>chart.setChartRange(Number(e.target.value));
function collapse(value){$('details').classList.toggle('collapsed',value);$('detailContent').hidden=value;$('toggleDetails').textContent=value?'展开副栏':'收起副栏';$('toggleDetails').setAttribute('aria-expanded',!value);}
$('toggleDetails').onclick=()=>collapse(!$('details').classList.contains('collapsed'));detailHeight(prefs.detailsHeight||280);collapse(!!prefs.collapsed);
let resize=null;$('detailResizer').onpointerdown=e=>{resize={y:e.clientY,h:$('details').getBoundingClientRect().height};e.target.setPointerCapture(e.pointerId);};$('detailResizer').onpointermove=e=>{if(resize)detailHeight(resize.h+resize.y-e.clientY);};$('detailResizer').onpointerup=()=>{resize=null;};$('detailResizer').onpointercancel=()=>{resize=null;};$('detailResizer').onkeydown=e=>{if(['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();detailHeight($('details').getBoundingClientRect().height+(e.key==='ArrowUp'?20:-20));}};

addEventListener('pagehide',save);document.addEventListener('visibilitychange',()=>{if(document.hidden)save();});
addEventListener('popstate',async()=>{const turn=++navigation;try{save();const route=urlTarget();if(!route){await load();return;}if(board&&route.target.kind==='stock'&&route.parent!==S.parent){S.parent=route.parent;const parent=model.detail(await transport.query(model.request(module,{kind:'group',id:S.parent},{barPeriod:'daily',barMode:'adjusted'},S.summary)));if(turn!==navigation)return;S.parentDetail=parent;}if(route.target.kind==='group')S.parent=board?route.target.id:null;await select(route.target,{push:false});}catch(e){if(turn!==navigation)return;$('detailStatus').textContent=e.message;toast(e.message);}});
const settings=createObserverSettings({state:S,$,preferences,chart,toast,readCalendar:transport.readCalendar});
document.addEventListener('keydown',event=>{
 if(event.key==='Escape'&&!$('settingsPanel').hidden){event.preventDefault();settings.show(false);return;}
 if(event.defaultPrevented||!$('settingsPanel').hidden||document.querySelector('dialog[open]')||event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]'))return;
 const direction=preferences.direction(event);if(!direction)return;
 const items=[...$('objectList').querySelectorAll('.object-row')];if(!items.length)return;
 event.preventDefault();if(event.repeat)return;
 const selected=items.findIndex(b=>b.classList.contains('selected'));
 const next=items[selected<0?(direction>0?0:items.length-1):(selected+direction+items.length)%items.length];
 const target={kind:next.dataset.kind,id:next.dataset.id},focusList=!!event.target.closest('.object-list');
 select(target,{newParent:true}).then(()=>{
  if(targetKey()!==targetKey(target))return;
  const active=$('objectList').querySelector('.object-row.selected');
  active?.scrollIntoView({block:'nearest'});
  (focusList&&innerWidth>820?active:$('chartOverlay'))?.focus({preventScroll:true});
 });
});
// Bounded diagnostics expose the same controller, now with normalized display data.
window.ObserverWorkspace={state:S,chart,select,save};
addEventListener('pagehide',event=>{if(!event.persisted){detailRequest?.abort();memberSignals.cancel();updates.destroy();settings.destroy();chart.destroy();}});
createObserverThemes({module,$,invoke:transport.invoke,toast,onChanged:load,getTarget:()=>S.parent});
// Open the reading surface before the background update-status poll performs
// its separate freshness/calendar reads on the same local service.
load().finally(()=>{updates.start();if(module==='theme'&&requestedThemeManager)$('manageThemes').click();});
})();
