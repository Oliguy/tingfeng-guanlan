/* Persistent global preferences and settings panel. */
(()=>{'use strict';
window.createObserverSettings=({state:S,$,preferences,chart,toast,readCalendar})=>{
 const lifetime=new AbortController();
function applyPreferences(){
 const chosen=preferences.read();for(const k of preferences.layers){S[k]=chosen[k];$(k).checked=S[k];}
 $('zoomGesture').value=chosen.zoomGesture;$('nextObjectKey').value=chosen.nextObjectKey;
 for(const name of Object.keys(preferences.lineWidths)){
  $(name).value=chosen[name];const label=`${chosen[name].toFixed(1)} px`;
  $(name+'Value').textContent=label;$(name).setAttribute('aria-valuetext',label);
 }
 const label={ctrl:'Ctrl加滚轮缩放',alt:'Alt加滚轮缩放',wheel:'滚轮缩放',off:'滚轮缩放关闭'}[chosen.zoomGesture];
 $('chartOverlay').setAttribute('aria-label',`K线图，左右键逐根选择，双击或Enter打开详情，拖动平移、${label}`);$('chartOverlay').title=label;
 if(S.detail)chart.drawCurrentBars();
}
function showSettings(open){
 $('settingsPanel').hidden=!open;$('openSettings').setAttribute('aria-expanded',String(open));
 if(open){applyPreferences();$('zoomGesture').focus();showCalendar();}else $('openSettings').focus();
}
async function showCalendar(){
 try{
  const c=await readCalendar({signal:lifetime.signal});
  $('calendarStatus').textContent=`${c.date} · ${c.is_open===true||c.is_open===1?'交易日':c.is_open===false||c.is_open===0?'休市':'日历未覆盖'}`;
  $('calendarDates').textContent=`最近交易日 ${c.latest_open||'未知'} · 下个交易日 ${c.next_open||'未覆盖'}`;
  $('calendarCoverage').textContent=`覆盖 ${c.coverage_start||'未知'} — ${c.coverage_end||'未知'} · ${c.source}`;
 }catch(error){if(error.name!=='AbortError')$('calendarStatus').textContent='日历读取失败：'+error.message;}
}
$('openSettings').onclick=()=>showSettings($('settingsPanel').hidden);$('closeSettings').onclick=()=>showSettings(false);
for(const name of ['zoomGesture','nextObjectKey'])$(name).onchange=()=>{preferences.set({[name]:$(name).value});applyPreferences();};
$('resetShortcuts').onclick=()=>{preferences.set({zoomGesture:'ctrl',nextObjectKey:'tab'});applyPreferences();toast('快捷键已恢复默认，辅助线选择保留');};
for(const name of Object.keys(preferences.lineWidths)){
 Object.assign($(name),preferences.widthLimits);
 $(name).oninput=()=>{preferences.set({[name]:Number($(name).value)});applyPreferences();};
}
$('resetLineWidths').onclick=()=>{preferences.set(preferences.lineWidths);applyPreferences();toast('辅助线粗细已恢复默认');};
addEventListener('storage',event=>{if(event.key===preferences.key||event.key===null)applyPreferences();},{signal:lifetime.signal});

 applyPreferences();return {show:showSettings,destroy:()=>lifetime.abort()};
};
})();
