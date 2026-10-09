/* A display-only window; its owner supplies exactly one selected candle. */
(()=>{'use strict';
 const P=ObserverCandleProtocol,$=id=>document.getElementById(id),{price,money,pct,tone}=ObserverFormat,origin=location.origin,session=location.hash.slice(1),owner=window.opener;
 let epoch=null,sequence=-1,handshake=null,lastMessage=Date.now(),connected=false,generation=null,command=0;const client=P.id();
 const values={detailOpen:'open',detailHigh:'high',detailLow:'low',detailClose:'close',detailWhite:'z_zhixing_short_trend',detailYellow:'z_zhixing_bull_bear',detailThirty:'thirty_week_ma',detailBbi:'bbi'};
 async function identifyDesktop(){
  const title=document.title,nonce=P.id();
  try{
   const health=await fetch('/api/v1/health').then(r=>r.json());
   document.title=title+' · '+nonce;
   for(let attempt=0;attempt<3;attempt++){
    await new Promise(resolve=>setTimeout(resolve,80));
    const response=await fetch('/api/v1/global/invoke',{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Operator-Write-Token':health.write_token},body:JSON.stringify({schema_version:'operation_request_v1',module:'global',operation:'desktop.identify-inspector',params:{nonce},request_id:crypto.randomUUID()})}).then(r=>r.json());
    if(response.data?.identified)break;
   }
  }catch{}finally{document.title=title;}
 }
 function clear(message){connected=false;for(const element of Object.keys(values))$(element).textContent='—';for(const element of ['detailChange','detailRatio','detailVolume','detailAmount']){$(element).textContent='—';$(element).title='';}tone($('detailChange'),0);$('detailIdentity').textContent=message;$('detailIdentity').title='';}
 function render(state){
  const context=state?.context||{},value=state?.selection,r=value?.row,period=context.period==='weekly'?'周':'日';
  $('detailName').textContent=context.title||'K线详情';$('detailChangeLabel').textContent=period+'涨跌';$('detailRatioLabel').textContent=period+'量比';
  if(state?.status!=='ready'||!r){clear(state?.status==='loading'?'正在读取新的K线…':state?.status==='error'?'主图读取失败；在主窗口重新读取后同步。':'暂无可用K线');return;}
  connected=true;generation=context.generation;
  const date=r.period_start?`${r.period_start} — ${r.trade_date}`:r.trade_date,mode=context.priceMode==='raw'?'原价':'前复权';
  $('detailIdentity').textContent=[context.code,date,period+'线 / '+mode,context.units?.price].filter(Boolean).join(' · ');
  const change=Number.isFinite(r.change_pct)?r.change_pct:null,ratio=Number.isFinite(r.volume_ratio)?r.volume_ratio:null;
  $('detailChange').textContent=change==null?'—':(change>0?'+':'')+pct(change);tone($('detailChange'),change||0);$('detailRatio').textContent=ratio==null?'—':ratio.toFixed(2)+'倍';
  for(const [element,key] of Object.entries(values))$(element).textContent=price(r[key]);
  $('detailVolume').textContent=money(r.volume);$('detailVolumeUnit').textContent=context.units?.volume?'('+context.units.volume+')':'';$('detailAmount').textContent=Number.isFinite(r.amount)?money(r.amount)+'元':'—';
  $('detailIdentity').title=[r.status==='suspended'?'全天停牌 · 无成交':r.status==='missing'?'行情缺失':null,r.provisional?'本周尚未结束':null,...(value.markers||[]).map(m=>m.label+' '+m.date)].filter(Boolean).join(' · ');
  $('detailRatio').title=ratio==null?r.volume_ratio_reason||'量比历史不足':`${period}量比：本期成交量 / 前5交易${period}均量`;
  $('detailChange').title=change==null?r.change_reason||'涨跌幅数据不足':period==='日'?'日涨跌采用原始行情口径':'周涨跌相对前一周收盘';
 }
 function request(){
  if(!owner||owner.closed){clear('主窗口已关闭');if(owner)window.close();return;}
  if(Date.now()-lastMessage>3500)clear('等待主图重新连接…');
  handshake=P.id();owner.postMessage({schema:P.schema,session,type:'ready',handshake,client},origin);
 }
 function step(direction){if(connected&&owner&&!owner.closed)owner.postMessage({schema:P.schema,session,type:'step',epoch,sequence,generation,client,command:++command,direction},origin);}
 if(!P.validId(session)||!owner){clear('请从观澜图表双击K线打开详情。');return;}
 window.addEventListener('message',event=>{
  const value=event.data;if(event.origin!==origin||event.source!==owner||!P.validMessage(value)||value.session!==session)return;
  if(value.type==='connected'){
   if(value.handshake!==handshake||!P.validId(value.epoch))return;epoch=value.epoch;sequence=value.sequence;lastMessage=Date.now();render(value.state);
  }else if(value.epoch===epoch&&value.type==='state'&&Number.isInteger(value.sequence)&&value.sequence>sequence){sequence=value.sequence;lastMessage=Date.now();render(value.state);}
  else if(value.epoch===epoch&&value.type==='owner-pending'){clear('主图切换中…');epoch=null;sequence=-1;}
 });
 window.addEventListener('keydown',event=>{if(event.target.closest('input,textarea,select,[contenteditable]'))return;if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();step(event.key==='ArrowLeft'?-1:1);}});
 request();identifyDesktop();const timer=setInterval(request,1000);window.addEventListener('pagehide',()=>clearInterval(timer));
})();
