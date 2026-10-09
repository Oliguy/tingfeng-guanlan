/* Observer charts adapt shared settings; the shell owns menu and persistence. */
(()=>{'use strict';window.createObserverSettings=({state:S,$,preferences,chart})=>{const lifetime=new AbortController();
function apply(){const chosen=preferences.read();for(const k of preferences.layers){S[k]=chosen[k];if($(k))$(k).checked=S[k];}const label={ctrl:'Ctrl加滚轮缩放',alt:'Alt加滚轮缩放',wheel:'滚轮缩放',off:'滚轮缩放关闭'}[chosen.zoomGesture];if($('chartOverlay')){$('chartOverlay').setAttribute('aria-label',`K线图，左右键逐根选择，双击或Enter打开详情，拖动平移、${label}`);$('chartOverlay').title=label;}if(S.detail)chart.drawCurrentBars();}
addEventListener('observer:preferences',apply,{signal:lifetime.signal});apply();return {show:open=>window.GuanlanSettings.show(open),destroy:()=>lifetime.abort()};};})();
