/* Member signal loading is independent of chart navigation; stale replies are fenced. */
(()=>{'use strict';
 window.createWorkspaceSignals=({transport,model,onChanged,timeoutMs=45000})=>{
  let active=null;
  const results=new Map();
  const keyOf=d=>JSON.stringify([d.target.kind,d.target.id,d.revision,d.members.sourceRevision]);
  function cancel(){
   const previous=active;active=null;
   if(previous){clearTimeout(previous.timer);previous.controller.abort();}
  }
  function load(next,{force=false}={}){
   if(!next?.members?.signalsAvailable)return;
   if(!force&&next.members.signalState==='ready'){cancel();return;}
   const key=keyOf(next);
   if(!force&&active?.key===key){active.detail=next;return;}
   cancel();
   if(!force&&next.members.sourceRevision&&results.has(key)){
    const raw=results.get(key);results.delete(key);results.set(key,raw);
    model.memberSignals(next,structuredClone(raw));onChanged();return;
   }
   if(force)results.delete(key);
   const request={key,detail:next,controller:new AbortController(),timer:null};active=request;
   request.timer=setTimeout(()=>{
    if(active!==request)return;
    active=null;request.controller.abort();
    model.memberSignals(request.detail,{items:{},error:'读取强弱超时，请点重新读取'});onChanged();
   },timeoutMs);
   Promise.resolve().then(()=>transport.query({view:'member_signals',target:next.target,publication_id:next.revision},{signal:request.controller.signal})).then(raw=>{
    if(active!==request)return;
    const detail=request.detail;
    if(detail.revision!==raw.data_revision||detail.target.id!==raw.target?.id||detail.target.kind!==raw.target?.kind)throw Error('成分股强弱与当前集合版本不一致，请重试');
    if(!raw.items||typeof raw.items!=='object'||Array.isArray(raw.items))throw Error('成分股强弱返回格式无效，请重试');
    // A source may change after detail loaded. The server validates the pinned
    // quote references again; display its result, but never cache under an old token.
    if(detail.members.sourceRevision&&detail.members.sourceRevision===raw.source_revision){
     results.set(key,structuredClone(raw));
     if(results.size>20)results.delete(results.keys().next().value);
    }
    model.memberSignals(detail,raw);onChanged();
   }).catch(error=>{
    if(active!==request)return;
    model.memberSignals(request.detail,{items:{},error:error.message||'读取强弱失败，请重试'});onChanged();
   }).finally(()=>{clearTimeout(request.timer);if(active===request)active=null;});
  }
  return {load,cancel};
 };
})();
