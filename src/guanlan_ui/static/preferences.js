/* Shared user settings. Object views only own period, price basis and viewport. */
window.ObserverPreferences=(()=>{
  'use strict';
  const key='stockObserver:preferences:v1',layers=['showZhixing','showThirtyWeek','showBbi'];
  const lineWidths=Object.freeze({zhixingWhiteWidth:2.1,zhixingYellowWidth:2.2,thirtyWeekWidth:1.8,bbiWidth:1.8});
  const widthLimits=Object.freeze({min:0.5,max:5,step:0.1});
  const defaults={zoomGesture:'ctrl',nextObjectKey:'tab',showZhixing:true,showThirtyWeek:true,showBbi:true,...lineWidths};
  let current={...defaults};
  function normalize(value){
    const result={...defaults};
    if(['ctrl','alt','wheel','off'].includes(value?.zoomGesture))result.zoomGesture=value.zoomGesture;
    if(['tab','alt-arrow','page','off'].includes(value?.nextObjectKey))result.nextObjectKey=value.nextObjectKey;
    for(const field of layers)if(typeof value?.[field]==='boolean')result[field]=value[field];
    for(const field of Object.keys(lineWidths))if(typeof value?.[field]==='number'&&Number.isFinite(value[field])){
      result[field]=Math.round(Math.max(widthLimits.min,Math.min(widthLimits.max,value[field]))*10)/10;
    }
    return result;
  }
  function read(seed){
    try{
      const raw=localStorage.getItem(key);current=normalize(raw===null?(seed||current):JSON.parse(raw));
      if(raw===null&&seed)localStorage.setItem(key,JSON.stringify(current));
    }catch{}
    return {...current};
  }
  function set(values){
    current=normalize({...read(),...values});
    try{localStorage.setItem(key,JSON.stringify(current));}catch{}
    return {...current};
  }
  function permitsWheel(event){
    if(current.zoomGesture==='ctrl')return event.ctrlKey&&!event.altKey&&!event.metaKey&&!event.shiftKey;
    if(current.zoomGesture==='alt')return event.altKey&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey;
    return current.zoomGesture==='wheel'&&!event.ctrlKey&&!event.altKey&&!event.metaKey&&!event.shiftKey;
  }
  function direction(event){
    const setting=current.nextObjectKey;
    if(event.ctrlKey||event.metaKey||event.isComposing)return 0;
    if(setting==='tab'&&event.key==='Tab'&&!event.altKey)return event.shiftKey?-1:1;
    if(setting==='alt-arrow'&&event.altKey&&!event.shiftKey)return event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:0;
    if(setting==='page'&&!event.altKey&&!event.shiftKey)return event.key==='PageDown'?1:event.key==='PageUp'?-1:0;
    return 0;
  }
  return {key,layers,lineWidths,widthLimits,read,set,permitsWheel,direction};
})();
