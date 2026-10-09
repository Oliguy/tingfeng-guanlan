/* ReaderDisplay v1. All source-specific DTO differences stop at this pure boundary.
   No DOM, requests, storage, chart mutation, or index calculations here. */
(()=>{'use strict';
const {money,pct,price}=ObserverFormat;
const qualityLabels={PASS:'可用',AVAILABLE:'有行情',PARTIAL:'部分可用',MISSING:'缺失',UNAVAILABLE:'资料不足',STALE:'待重算',UNMIGRATED:'待核验',NOT_APPLICABLE:'不适用'};
const reasonLabels={calendar_dependency_changed:'日历来源已变化，派生数据等待重算',missing_adjacent_share_observation:'缺少相邻交易日的份额披露',conflicting_share_observations:'份额来源存在冲突',source_not_verified_daily:'份额来源尚不具备已核验的日频资格',nav_date_mismatch:'净值日期与份额日期不匹配',split_not_verified:'拆分或除权尚未核验',invalid_shares_or_factor:'份额或折算系数无效',missing_member_volumes:'部分成员缺少成交量',missing_member_quotes:'部分成员缺少行情',return_inputs_incomplete:'收益计算所需行情不完整',prior_continuity_gap:'此前历史存在断点',derived_quality_unverified:'派生数据尚未按当前规则核验',derived_recalculation_pending:'来源已变化，派生数据等待重算'};
const quality=v=>qualityLabels[v]||v||'—';
const text=value=>({type:'text',value}),details=(title,blocks)=>({type:'details',title,blocks}),table=(headers,rows)=>({type:'table',headers,rows:rows.map(cells=>({cells}))});
const link=(url,label)=>({type:'link',url,label});
function evidenceLabel(value){try{const r=JSON.parse(value||'{}');return r.lexical?.length?'指数：'+r.lexical[0].name:r.holding_industries?.length?'持仓：'+r.holding_industries[0].name:r.reason||'规则验证';}catch{return value||'—';}}
function summary(raw,module){
 const board=module!=='etf';
 const convert=(r,focus)=>({target:focus?{kind:'etf',id:r.etf_code}:{kind:'group',id:r.group_id},name:focus?r.focus_name||r.fund_name:r.group_name,
  change:focus?r.daily_return:r.group_return,signal:r.weekly_strength||{},section:focus?'focus':'groups',
  parentId:r.parent_id,publicationId:r.publication_id,referenced:Object.hasOwn(r,'publication_id'),collectionKind:r.kind,pending:r.pending,failure:r.failure,
  caption:board?(r.stats?.current_listed_members??0)+'只成分'+(r.quality==='working_unverified'?' · 工作分类':r.quality==='classified_sample'?' · 已分类样本':'')+(r.failure?' · 应用失败':r.pending?' · 待本地应用':''):r.leader_name||r.fund_name||r.group_id||r.etf_code,
  search:[r.group_name,r.focus_name,r.fund_name,r.group_id,r.etf_code,r.leader_name].filter(Boolean).join(' ').toLowerCase()});
 return {schema:'reader_display_v1',module,revision:raw.data_revision,asOf:raw.as_of,objects:[...(raw.focus_items||[]).map(r=>convert(r,true)),...(raw.items||[]).map(r=>convert(r,false))]};
}
function request(module,target,view,summary,parent){
 const params={view:'detail',target,period:view.barPeriod,price_mode:view.barMode,limit:1000};
 if(module!=='etf'){const row=summary.objects.find(r=>r.target.id===(target.kind==='stock'?parent:target.id));if(row?.referenced){if(row.publicationId)params.publication_id=row.publicationId;if(target.kind==='stock')params.parent_id=parent;}else{params.run_id=summary.revision;if(target.kind==='stock')params.industry_id=parent;}}
 return params;
}
function detail(raw){
 const d={...raw},g=d.industry,stock=d.stock;
 const series=d.series_table?d.series_table.rows.map(r=>Object.fromEntries(d.series_table.columns.map((c,i)=>[c,r[i]]))):d.series||[];
 const last=series.at(-1)||{},bar=(d.chart_bars||[]).filter(r=>r.close!=null).at(-1)||{};
 const result={schema:'reader_display_v1',target:d.target,revision:d.data_revision,period:d.period,priceMode:d.price_mode,
  title:g?(g.kind==='subindustry'&&g.path?.length>1?g.path[0]+' · '+g.name:g.name):stock?stock.name||stock.code:d.leader?.fund_name||d.group?.group_name||d.target.id,
  code:g?(g.kind==='theme'?'':g.id):stock?stock.code:d.leader?.etf_code||d.target.id,
  parentLabel:g?(g.kind==='theme'?'题材库':g.path?.join(' / ')||'30行业'):stock?d.parent.name:d.group?.group_name||'ETF',
  meta:g?`${g.members.length}只成分 · 每日等权${g.quality==='working_unverified'?' · 工作分类（未核实）':g.quality==='classified_sample'?' · 已分类样本':''}${d.pending?' · 新修订待应用，当前为已发布版本':''}`:stock?({D:'已退市 · 分类成员',P:'暂停上市 · 分类成员'}[stock.metadata?.list_status]||'行业成分股'):d.leader?.tracking_index_name||'',
  date:d.price_date||bar.trade_date||'暂无',signal:d.weekly_strength,unitLabel:g?'指数点':stock?'元/股 · 股':'元/份',
  status:g?(d.failure?'应用失败：'+d.failure:`${g.stats.valid_sessions}/${g.stats.sessions}个交易日连续`):stock?`${stock.events.filter(e=>e.kind==='suspended').length}个停牌日 · ${stock.events.filter(e=>e.kind==='missing').length}个缺失日`+(bar.thirty_week_ma==null?' · 30周历史不足':''):d.calendar_dependency_status==='UNAVAILABLE'?'交易日历不可用；按已存行情展示':quality(last.quality_status),
  chart:{bars:d.chart_bars||[],units:d.units||{price:g?'指数点':'元/份',volume:g?'股/只':'份',amount:'元'},period:d.period,priceMode:d.price_mode},
  capabilities:{volume:!!g,price:!g,memberChart:!!g,returnToParent:!!stock},
  kind:g?'industry':stock?'stock':'etf',metrics:[],info:[],members:null};
 if(g){
  result.volumeBars={daily:g.points,weekly:g.weekly_points};
  result.metrics=[['行业涨跌',pct(g.points.at(-1)?.daily_return)],['行业总成交额',money(bar.amount)+'元'],['等权20日相对量',price(g.points.at(-1)?.relative_volume_20)+'倍'],['当前上市成分',g.stats.current_listed_members+'只'],['有效交易日',g.stats.valid_sessions+'/'+g.stats.sessions],['近一年收益',pct(g.stats.year_return)]];
  result.info=[text(g.kind==='theme'?'按用户提供的当前题材成员回溯；标签仅属于此题材，同股多个标签只计一次。高低为合成包络。':g.quality==='working_unverified'?'固定工作分类成员回溯，保留未核实及待定状态；仅实际经营阶段参与指数，研发和投资不计入。普通行情更新不会改变成员。高低为合成包络。':g.quality==='classified_sample'?'仅当前已分类样本；可能跨一级行业，不能视为完整上下游指数。高低为合成包络。':'当前成员回溯；分类尚在进行，不代表全市场完整覆盖。行业高低为合成包络。'),text(`分类版本 ${d.header.classification_snapshot}\n行情版本 ${d.data_revision}`),text(`已核验 ${g.stats.verified_members}只；来源支持 ${g.stats.source_supported_members}只；未核实 ${g.stats.unverified_members||0}只；排除 ${g.stats.excluded_members}只。`),text('确认全天停牌前值估值、量额零，仍计入等权分母；个股图只显示实际成交K线。')];
  result.members={title:g.name+' · '+g.members.length+'只成分',headers:['代码','名称','日涨跌','量比','主要产品'],ratioOffset:3,rows:g.members.map(m=>({target:{kind:'stock',id:m.code},search:[m.code,m.name,...(m.products||[])].filter(Boolean).join(' ').toLowerCase(),change:m.quote?.change,volumeRatio:m.quote?.volume_ratio??null,
   cells:[m.code,m.name||'—',{value:pct(m.quote?.change),tone:m.quote?.change},ObserverMemberStrength.ratioCell(m.quote||{}),{type:'products',values:m.products||[],title:m.product_note||''}]})),extra:[]};
  result.members.signalsAvailable=!!d.member_signals_available;result.members.signalState=result.members.signalsAvailable?'pending':'unsupported';result.members.sourceRevision=d.member_signal_revision||null;
  if(result.members.signalsAvailable){result.members.signalOffset=2;result.members.signalCount=ObserverMemberStrength.cells().length;result.members.ratioOffset+=result.members.signalCount;result.members.headers.splice(2,0,'近5周','连续站上','偏离30周线');result.members.rows.forEach(r=>{r.weeklySignal={status:'pending'};r.cells.splice(2,0,...ObserverMemberStrength.cells(r.weeklySignal));});}
  result.members.tags=g.tags||[];
  if(g.kind==='theme'||g.kind==='subindustry'){if(result.members.signalsAvailable)result.members.signalOffset++;result.members.ratioOffset++;result.members.headers.splice(2,0,'标签 / 路径');result.members.rows.forEach((r,i)=>{const m=g.members[i];r.tags=result.members.tags.filter(t=>(m.paths||[]).some(p=>t.path.every((part,i)=>p[i]===part))).map(t=>t.id);r.tagOrder=Math.min(...(m.tag_ids||[]).map(id=>result.members.tags.find(t=>t.id===id)?.order??9999),9999);r.name=m.name||m.code;r.tagText=(m.paths||[]).map(p=>p.join(' / ')).join('；');r.search+=' '+r.tagText.toLowerCase();r.cells.splice(2,0,{type:'tags',values:(m.paths||[]).map(p=>p.join(' / '))});});}
  if(g.source)result.info.push(text((g.kind==='theme'?'题材来源：':'分类来源：')+(g.source.title||'用户提供')+'\n'+(g.source.note||'')));
 }else if(stock){
  result.metrics=[['股票最新原价',price(stock.quote.close)+'元'],['日涨跌',pct(stock.quote.change)],['成交额',money(stock.quote.amount)+'元'],['成交量',money(stock.quote.volume)+'股'],['行情日期',stock.quote.date||'—'],['所属集合',d.parent.name]];
  result.info=[text(`当前股票 ${stock.name} ${stock.code} · ${stock.status==='verified'?'已核验':stock.status==='user_supplied'?'用户题材成员':stock.status==='unverified'?'工作分类 · 未核实':'来源支持'}`),text(`所属行业：${d.parent.name} · 版本 ${d.data_revision}`),...(stock.relations||[]).map(r=>details(r.report_period||'分类依据',[text(r.source_run_id?`${r.reason}\n分类批次 ${r.source_run_id} · 原引用 ${r.source_refs} · ${r.verification_status}`:`${r.reason}\n文档 ${r.document_id} · 事实 ${r.fact_id}`)])),details(`停牌与缺口 ${stock.events.length}项`,[text(stock.events.map(e=>e.trade_date+' '+e.label).join('\n')||'该区间无已确认全天停牌')])];
 }else{
  result.metrics=[['聚合成交额',money(last.aggregate_amount)],['20日相对成交额',price(last.relative_amount_20d)+'倍'],['行业/ETF涨跌',pct(last.group_return)],['估算日净申购',money(last.estimated_net_subscription)],['成员覆盖',pct(last.coverage_ratio)],['质量状态',quality(last.quality_status)]];
  result.metricNotes=[text('日净申购须有合格的相邻交易日份额和匹配净值；暂无可用值时显示“—”。'),details('披露区间份额变化',(d.share_change_intervals||[]).length?d.share_change_intervals.slice(-12).reverse().map(r=>text(`${r.period_start} — ${r.period_end}：份额变化 ${money(r.share_change)}，期末净值估值 ${money(r.end_value_estimate)}；${r.status||''}。不等于单日净申购。`)):[text('暂无可核验披露区间')])];
  result.info=[text(d.group?.group_type==='focus'?'当前重点ETF单独观察，不参与行业聚合。':'整段图表使用本月代表ETF；优先从60周历史的成员中按上月成交额评选。'),text(`行情 ${d.price_date} · 日历 ${({UNAVAILABLE:'来源不可用',STALE:'来源已变化，待重算',UNVERIFIED:'待核验',CURRENT:'当前可用'})[d.calendar_dependency_status]||d.calendar_dependency_status||'—'} · 质量 ${quality(last.quality_status)}`),text('质量依据：'+((last.reason_codes||[]).map(c=>reasonLabels[c]||c).join('、')||'无补充原因')),details('月度代表ETF历史',[table(['月份','ETF','上月成交额'],(d.leader_history||[]).map(r=>[r.leader_month,`${r.fund_name} ${r.etf_code}`,money(r.monthly_amount)]))]),details('价格折算事件',(d.adjustment_events||[]).length?d.adjustment_events.map(e=>text(`${e.trade_date} ×${price(e.factor)}`)):[text('无折算事件')])];
  const holdings=d.holdings||[];
  result.members={title:(d.members||[]).length+'只ETF成员 · 持仓按定期披露',headers:['ETF代码','名称','归类依据'],rows:(d.members||[]).map(m=>({search:(m.etf_code+' '+m.fund_name).toLowerCase(),cells:[m.etf_code,m.fund_name,evidenceLabel(m.evidence_json)]})),extra:[{type:'heading',value:'前十大持仓 '+(holdings[0]?.report_date||'')},link(holdings[0]?.source_url,'查看披露原文'),{type:'table',headers:['股票','代码','权重','日涨跌'],rows:holdings.slice(0,10).map(m=>({search:(m.stock_code+' '+m.stock_name).toLowerCase(),cells:[m.stock_name,m.stock_code,m.weight_pct==null?'—':Number(m.weight_pct).toFixed(2)+'%',pct(m.daily_return)]}))}]};
 }
 if(d.member_signals?.items&&result.members?.signalsAvailable){
  if(d.member_signals.data_revision!==result.revision||d.member_signals.target?.id!==result.target.id)throw Error('成分股强弱与当前集合版本不一致');
  memberSignals(result,d.member_signals);
 }
 return result;
}
function chart(d,view){
 if(!d)return {bars:[],units:{},period:view.barPeriod,priceMode:view.barMode};
 let bars=d.chart.bars,units={...d.chart.units},description='';
 if(d.kind==='industry'){
  const volume=view.volumeMode;
  bars=(d.volumeBars[view.barPeriod]||[]).map(r=>({...r,volume:r[volume]}));
  units.volume={mean_volume:'股/只',volume:'股',amount:'元',relative_volume_20:'倍'}[volume];
  description='当前成员回溯 · 每日收益等权 · 高低为合成包络；量柱 '+({mean_volume:'平均每股成交量（股/只）',volume:'行业总量（股）',amount:'行业总额（元）',relative_volume_20:'等权20日相对量（倍）'}[volume]);
 }else if(d.kind==='stock')description=(view.barMode==='raw'?'原价':'固定截止日因子锚定前复权')+' · 真实交易OHLC；停牌留空并标记，量为股、额为元。'+(view.barPeriod==='weekly'?'周线按自然周聚合，最后一周可能未结束。':'');
 else description='ETF '+(view.barMode==='raw'?'原始价格':'连续复权')+' · 四辅助线与原ETF算法一致。'+(view.barPeriod==='weekly'?'周线含最后一个未完成周。':'');
 return {...d.chart,bars,units,description};
}
function memberSignals(detail,payload){const m=detail.members;if(!m?.signalsAvailable)return;for(const r of m.rows){r.weeklySignal=payload.items[r.target.id]||{status:'unavailable',reason:payload.error||'缺少此成员信号'};r.cells.splice(m.signalOffset,m.signalCount,...ObserverMemberStrength.cells(r.weeklySignal));r.volumeRatio=r.weeklySignal.volume_ratio??null;r.cells[m.ratioOffset]=ObserverMemberStrength.ratioCell(r.weeklySignal);}m.signalState=payload.error?'error':'ready';}
function matchesRequest(raw,params){return !!raw&&raw.target?.kind===params.target.kind&&raw.target?.id===params.target.id&&raw.period===params.period&&raw.price_mode===params.price_mode&&(!params.publication_id||raw.data_revision===params.publication_id)&&(!params.run_id||raw.data_revision===params.run_id);}
window.ObserverModel={summary,detail,request,chart,memberSignals,matchesRequest};
})();
