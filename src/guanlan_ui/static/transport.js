/* The only browser module that knows the HTTP operation envelope. */
(()=>{'use strict';
window.createObserverTransport=(module)=>{
 let token=null,pendingToken=null;
 async function authorize(){
  return window.guanlanAuthorize();
 }
 async function invoke(operation,params={},scope=module,{signal}={}){
  const authorization=await authorize();
  const response=await fetch(`/api/v1/${scope}/invoke`,{method:'POST',signal,headers:{'Content-Type':'application/json','X-Stock-Operator-Write-Token':authorization},body:JSON.stringify({schema_version:'operation_request_v1',module:scope,operation,params,request_id:crypto.randomUUID()})});
  if(response.status===401||response.status===403)window.guanlanForgetSession();
  const body=await response.json();if(!response.ok||!body.ok)throw Error(body.error?.message||body.detail||'读取失败');
  return body.data;
 }
 async function readCalendar({signal}={}){
  await authorize();const response=await fetch('/api/v1/calendar',{signal});const value=await response.json();
  if(!response.ok||!value.ok)throw new Error(value.detail||'日历不可用');return value.data;
 }
 return {invoke,query:(params,options)=>invoke('observer.query',params,module,options),readCalendar};
};
})();
