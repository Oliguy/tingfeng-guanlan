/* Same-origin display-window protocol. No quote requests or workspace state. */
(()=>{'use strict';
 const schema='guanlan-candle-window-v1',id=()=>crypto.randomUUID().replaceAll('-',''),validId=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
 const validMessage=v=>v&&typeof v==='object'&&v.schema===schema&&validId(v.session);
 window.ObserverCandleProtocol=Object.freeze({schema,id,validId,validMessage});
 window.createObserverInspector=({read,onStep,notice=()=>{}})=>{
  const key='guanlan:candle-inspector:session';let session;
  try{session=sessionStorage.getItem(key);if(!validId(session)){session=id();sessionStorage.setItem(key,session);}}catch{session=id();}
  const name='guanlan-candle-inspector-'+session,origin=location.origin,epoch=id(),lifetime=new AbortController();
  let detail=null,connected=false,sequence=0,dead=false,client=null,lastCommand=0;
  function send(type,extra={}){if(dead||!detail||detail.closed)return;try{detail.postMessage({schema,session,epoch,sequence,type,...extra},origin);}catch{connected=false;}}
  function isChild(candidate){try{return candidate&&candidate!==window&&!candidate.closed&&candidate.opener===window&&candidate.name===name&&candidate.location.origin===origin&&candidate.location.pathname==='/candle-details.html'&&candidate.location.hash==='#'+session;}catch{return false;}}
  function update(){if(dead)return;sequence++;if(connected)send('state',{state:read()});}
  function open(){
   if(dead)return false;
   if(!detail||detail.closed){connected=false;detail=window.open('/candle-details.html#'+session,name,'popup=yes,width=320,height=250,resizable=yes,scrollbars=yes');}
   if(!detail){notice('详情窗口未能打开；允许观澜弹出窗口后，再双击K线或按Enter重试。');return false;}
   detail.focus();update();return true;
  }
  window.addEventListener('message',event=>{
   const value=event.data;if(dead||event.origin!==origin||!validMessage(value)||value.session!==session)return;
   if(value.type==='ready'){
    if(!validId(value.handshake)||!validId(value.client)||!isChild(event.source))return;
    if(value.client!==client){client=value.client;lastCommand=0;}
    detail=event.source;connected=true;send('connected',{handshake:value.handshake,state:read()});
   }else if(value.type==='step'&&event.source===detail&&connected&&value.client===client&&value.epoch===epoch&&Number.isSafeInteger(value.command)&&value.command>lastCommand&&[-1,1].includes(value.direction)){
    const state=read();if(state.status!=='ready'||value.generation!==state.context.generation)return;
    lastCommand=value.command;onStep(value.direction);
   }
  },{signal:lifetime.signal});
  function destroy(){if(dead)return;send('owner-pending');dead=true;lifetime.abort();detail=null;connected=false;}
  return {open,update,destroy,getStatus:()=>({connected:connected&&!!detail&&!detail.closed,sequence,epoch})};
 };
})();
