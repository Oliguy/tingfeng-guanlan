/* The shell shares preferences; existing observer controllers bind their settings. */
(()=>{'use strict';const $=id=>document.getElementById(id),module=document.body.dataset.observerModule,P=ObserverPreferences;
if(!['home','training'].includes(module))return;
function apply(){const p=P.read();for(const name of ['zoomGesture','nextObjectKey'])$(name).value=p[name];for(const name of Object.keys(P.lineWidths)){Object.assign($(name),P.widthLimits);$(name).value=p[name];$(name+'Value').textContent=p[name].toFixed(1)+' px';}dispatchEvent(new CustomEvent('observer:preferences'));}
async function calendar(){try{await window.guanlanAuthorize();const response=await fetch('/api/v1/calendar',{cache:'no-store'}),body=await response.json();if(!body.ok)throw new Error('日历暂不可读');const c=body.data;$('calendarStatus').textContent=`${c.date} · ${c.is_open===true||c.is_open===1?'交易日':c.is_open===false||c.is_open===0?'休市':'未覆盖'}`;$('calendarDates').textContent=`最近交易日 ${c.latest_open||'未知'} · 下个交易日 ${c.next_open||'未覆盖'}`;$('calendarCoverage').textContent=`覆盖 ${c.coverage_start||'未知'} — ${c.coverage_end||'未知'}`;}catch(e){$('calendarStatus').textContent=e.message;}}
function show(open){$('settingsPanel').hidden=!open;$('openSettings').setAttribute('aria-expanded',String(open));if(open){apply();$('zoomGesture').focus();calendar();}else $('openSettings').focus();}
$('openSettings').onclick=()=>show($('settingsPanel').hidden);$('closeSettings').onclick=()=>show(false);
for(const name of ['zoomGesture','nextObjectKey'])$(name).onchange=()=>{P.set({[name]:$(name).value});apply();};
for(const name of Object.keys(P.lineWidths))$(name).oninput=()=>{P.set({[name]:Number($(name).value)});apply();};
$('resetShortcuts').onclick=()=>{P.set({zoomGesture:'ctrl',nextObjectKey:'tab'});apply();};$('resetLineWidths').onclick=()=>{P.set(P.lineWidths);apply();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('settingsPanel').hidden){e.preventDefault();show(false);}});addEventListener('storage',e=>{if(e.key===P.key||e.key===null)apply();});
if(module==='training'){$('nextObjectKey').closest('aside').querySelector('p').textContent='所有栏目共用辅助线、粗细和缩放手势。';$('nextObjectKey').insertAdjacentHTML('afterend','<p>训练页的 Tab 保留正常焦点；Space 只前进一个回合。</p>');}
apply();})();
