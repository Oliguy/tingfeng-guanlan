/* Anomaly page coordinator: dates, filters and navigation. No market calculations. */
(()=>{'use strict';
const $=id=>document.getElementById(id),transport=createObserverTransport('movers'),preferences=ObserverPreferences;
const PAGE=document.body.dataset.moversPage==='leader'?'leader':'list';
const FLOOR_KEY='guanlan_movers_floor_guanlan_leader_observation_v0_6';
const S={loadState:"empty",data:null,leader:null,leaderError:null,display:null,filter:{page:PAGE,axis:'industry',direction:'all',search:'',sort:PAGE==='leader'?'leader':'absolute',theme:'',tag:'',complete_only:false,floor:35},detail:null,stockCode:null,barPeriod:'daily',barMode:'adjusted',barRange:150,panOffset:0,volumeMode:'volume'};
let dayRequest=null,leaderRequest=null,stockRequest=null,daySequence=0,stockSequence=0,dateCursor=null,dateLoading=false,returnPosition=null,toastTimer=null;
addEventListener('observer:preferences',()=>{if(!S.detail){const p=preferences.read();S.barPeriod=p.defaultPeriod;S.barMode=p.defaultPrice;S.barRange=p.defaultRange;}});
const initialQuery=new URLSearchParams(location.search);if(['up','down'].includes(initialQuery.get('direction'))){S.filter.direction=initialQuery.get('direction');$('moveDirection').value=S.filter.direction;}
if(PAGE==='list'){document.querySelectorAll('#moversSort [data-score-only]').forEach(o=>o.remove());$('completeScoresWrap').hidden=true;$('scoreFloorWrap').hidden=true;$('leaderRules').hidden=true;$('leaderStatusLine').hidden=true;$('supplementLimits').hidden=false;}else{$('moversPageTitle').textContent='龙头评分';document.querySelector('.movers-heading>span').textContent='近5个交易日异动 · 日线评分';$('supplementLimits').hidden=true;try{const saved=localStorage.getItem(FLOOR_KEY);if(saved!==null&&Number.isInteger(Number(saved))&&Number(saved)>=0&&Number(saved)<=130)S.filter.floor=Number(saved);}catch{}$('scoreFloor').value=S.filter.floor;}
$('moversTab'+(PAGE==='leader'?'Leader':'List')).setAttribute('aria-current','page');
try{const saved=localStorage.getItem(PAGE==='leader'?'guanlan_movers_leader_sort':'guanlan_movers_sort');if([...$('moversSort').options].some(o=>o.value===saved)){S.filter.sort=saved;}}catch{}
$('moversSort').value=S.filter.sort;
const views=new Map();
function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),6000);}
const key=()=>`${S.data?.date}:${S.stockCode}:${S.barPeriod}:${S.barMode}`;
function saveView(){if(S.detail){views.set(`${S.detail.date}:${S.detail.target.id}:${S.detail.period}:${S.detail.priceMode}`,{range:S.barRange,pan:S.panOffset});while(views.size>80)views.delete(views.keys().next().value);}}
const chart=createWorkspaceChart({state:S,$,root:document,preferences,onViewChange:saveView});
const settings=createObserverSettings({state:S,$,preferences,chart,toast,readCalendar:transport.readCalendar});
function displayData(){return S.leader?.window&&PAGE==='leader'?{...S.data,...S.leader.window,date:S.data.date}:{...S.data,leader_error:S.leaderError};}
function renderList(){if(S.data)S.display=view.render(displayData(),S.filter,S.leader);}
function showLeader(code,groupKey){
 const info=S.leader?.scores?.[code],row=displayData()?.rows.find(r=>r.code===code);if(!info||!row)return;
 const esc=ObserverFormat.esc,components={price_today:'当日涨幅排名',price_previous4:'此前4日涨幅排名',boards:'近10日板数',daily_shape:'当日日线形态',volume_adjustment:'量比奖惩'};
 const reasons={return_or_comparison_cohort_unavailable:'收益或同制度比较样本不足',unknown_daily_limit_states:'部分交易日涨停状态待核',five_positive_session_volumes_unavailable:'此前5日缺少有效成交量',current_positive_volume_unavailable:'当日有效成交量不足',share_volume_comparability_unverified:'成交量可比性待核',reference_price_adjustment_volume_comparability_unverified:'价格参考发生调整，成交量可比性待核',effective_up_limit_unavailable:'实际涨停价依据不足',invalid_or_missing_daily_shape:'日线形态数据不足',calendar_window_incomplete:'交易日历不完整'};
 const number=v=>v==null?'待核验':Number(v).toFixed(2), percent=v=>v==null?'待核验':(v*100).toFixed(2)+'%';
 function basis(name,v){
  if(name==='price_today'||name==='price_previous4')return `涨幅 ${percent(v.return_value)}；同制度 ${v.sample_count} 只，百分位 ${percent(v.percentile)}×${name==='price_today'?15:25}${name==='price_previous4'?'；此前4日不含今天':''}`;
  if(name==='boards')return `${v.known_boards}个确认板×5${v.unknown_days?`；${v.unknown_days}日待核`:''}；每板继续累加`;
  if(name==='daily_shape')return v.kind==='one_price'?`四价均为涨停价 ${number(v.up_limit)}，真实成交，一字20分`:v.kind==='not_limit'?'当日未收盘涨停，形态0分':v.kind==='other_limit'?`开 ${number(v.open)} / 高 ${number(v.high)} / 低 ${number(v.low)} / 收 ${number(v.close)}；涨停 ${number(v.up_limit)}，昨收参考 ${number(v.pre_close)}；实体比例 ${percent(v.body_ratio)}，下影比例 ${percent(v.lower_shadow_ratio)}；max(0，18×(1−实体比例)−10×下影比例)`:'实际限价或日线依据不足';
  return `量比 ${number(v.ratio)}＝当日 ${number(v.volume_lot)} 手÷此前5日均量 ${number(v.baseline_volume_lot)} 手；${v.baseline_dates?.join('、')||'基准待核'}。缩量涨停＋min(20，40×(1−量比))；放量−min(20，20×(量比−1))`;
 }
 const part=Object.entries(info.components).map(([name,v])=>`<tr><td>${esc(components[name]||name)}</td><td class="${v.points>0?'positive':v.points<0?'negative':''}">${v.points==null?`${number(v.min)}–${number(v.max)}`:(v.points>0&&name==='volume_adjustment'?'+':'')+(v.points_display??Number(v.points).toFixed(1))}</td><td>${esc(basis(name,v))}${v.unknown_reasons?.length?'<br>'+esc(v.unknown_reasons.map(r=>reasons[r]||'本地依据不足，待核验').join('；')):''}</td></tr>`).join('');
 const days=info.boards.days.map(d=>`<tr><td>${esc(d.date)}</td><td>${esc(({confirmed_up:'收盘涨停',not_limit:'未涨停',unrestricted:'无涨跌幅限制',conflict:'来源冲突',unknown:'待核验',not_listed:'尚未上市',suspended:'已确认停牌'})[d.status]||'待核验')}</td><td>${d.status==='unknown'?'该日依据不足':d.status==='conflict'?'证据冲突':d.sources?.some(s=>s.kind==='dated_limit_price')?'当日限制价':d.sources?.some(s=>s.kind==='dated_positive_event')?'当日收盘涨停记录':'本地日线规则'}</td></tr>`).join('');
 const windows=Object.entries(info.boards.window_counts||{}).map(([n,v])=>`${n}日${v.unknown?`${v.min}–${v.max}板（${v.unknown}日待核）`:v.known+'板'}`).join(' · ');
 const relative=S.leader?.groups?.[groupKey]?.relative5?.[code];
 $('leaderDetailBody').innerHTML=`<h2>${esc(row.name)} ${esc(code)} · ${info.score==null?`${info.lower_display}–${info.upper_display}（待核验）`:info.score_display+'分'}</h2><p>评分截至 ${esc(info.date)} · 最近异动 ${esc(row.event_date||info.date)}。总分＝max(0，价格＋板数＋日线形态＋量比)，允许超过100。近5日相对本组收益 ${relative==null?'依据不足':(relative*100).toFixed(2)+'%'}；历史分组采用当前分类。${info.unknown_reasons?.length?'交易日历不完整，暂不确认总分。':''}</p><table><caption>分项依据</caption><thead><tr><th>项目</th><th>得分</th><th>计算依据</th></tr></thead><tbody>${part}</tbody></table><h3>${esc(info.n_m_label||'近10日板数')}</h3><p>${esc(windows)}。标签只在近10日证据完整且至少2板时展示。</p><table><caption>逐日收盘状态</caption><tbody>${days}</tbody></table><button type="button" class="leader-open-chart" data-stock="${esc(code)}">查看截至${esc(info.date)}的K线</button>`;
 $('leaderDetails').hidden=false;$('leaderDetails').scrollIntoView({block:'nearest'});$('closeLeaderDetails').focus();
}
const view=createMoversView({$,onStock:openStock,onLeader:showLeader,onTag:(theme,tag)=>{S.filter.theme=theme;S.filter.tag=tag;renderList();$('moversScroll').scrollTop=0;},onJump:id=>{$(id)?.scrollIntoView({block:'start'});}});
async function loadLeader(data,sequence){
 leaderRequest?.abort();leaderRequest=new AbortController();S.leaderError=null;$('retryLeader').hidden=true;$('leaderStatus').textContent='正在读取五日异动池并计算日线评分…';
 try{const leader=await transport.query({view:'leader',date:data.date,market_revision:data.market_revision,classification_revision:data.classification_revision},{signal:leaderRequest.signal});if(sequence!==daySequence||S.data?.date!==data.date)return;
  if(leader.date!==data.date||leader.market_revision!==data.market_revision||leader.classification_revision!==data.classification_revision)throw Error('评分来源已变化，请刷新当前日期');
  S.leader=leader;document.body.dataset.leaderModel=leader.schema_version;$('leaderStatus').textContent=`日线评分已就绪 · 五日池 ${leader.window.stats.total} 只 · ${Object.values(leader.scores).filter(v=>v.score!=null).length} 只完整评分 · 区间行按下界排序、无正式名次 · 点击分数查看依据`;renderList();}
 catch(e){if(e.name==='AbortError'||sequence!==daySequence)return;S.leaderError=e.message;$('leaderStatus').textContent='观察评分暂不可用：'+e.message+' · 可重试';$('retryLeader').hidden=false;renderList();}
}
function setDateOption(day){if(![...$('eventDate').options].some(o=>o.value===day))$('eventDate').add(new Option(day,day));const options=[...$('eventDate').options].sort((a,b)=>b.value.localeCompare(a.value));$('eventDate').replaceChildren(...options);$('eventDate').value=day;}
async function loadDates(more=false){if(dateLoading)return;dateLoading=true;$('moreDates').disabled=true;try{const data=await transport.query({view:'dates',limit:60,...(more&&dateCursor?{before:dateCursor}:{})});for(const day of data.dates)if(![...$('eventDate').options].some(o=>o.value===day))$('eventDate').add(new Option(day,day));dateCursor=data.next_cursor;$('moreDates').hidden=!dateCursor;if(S.data)setDateOption(S.data.date);}catch(e){toast('日期目录读取失败：'+e.message);}finally{dateLoading=false;$('moreDates').disabled=false;}}
function syncHistory(){if(!S.data)return;const q=new URLSearchParams({date:S.data.date});if(S.filter.direction!=='all')q.set('direction',S.filter.direction);if(initialQuery.get('from')==='home')q.set('from','home');history.replaceState(null,'',`/movers/${PAGE==='leader'?'leader/':''}?${q}`);$('moversTabList').href='/movers/?'+q;$('moversTabLeader').href='/movers/leader/?'+q;}
function dateStatus(){if(!S.data)return;const d=S.data,quality=d.quality;$('moversReadStatus').classList.remove('error');$('moversReadStatus').textContent=`${d.date===d.latest_collected_trade_date?'最新采集日':'已采集历史日'} ${d.date} · 最新采集 ${d.latest_collected_trade_date} · 本地 ${quality.source_rows} 只日线`+(quality.unavailable_change?` · ${quality.unavailable_change}只涨跌幅不可判定`:'')+(quality.invalid_ohlc?` · ${quality.invalid_ohlc}只OHLC异常`:'')+(quality.quarantined_records?` · ${quality.quarantined_records}条源隔离记录`:'')+(quality.limit_coverage?.status==='unavailable'?' · '+quality.limit_coverage.message:'')+' · 只读浏览';}
async function loadDay(day){
 const sequence=++daySequence;dayRequest?.abort();leaderRequest?.abort();stockRequest?.abort();++stockSequence;dayRequest=new AbortController();
 $('supplementLimits').disabled=true;
 $('moversReadStatus').textContent='读取 '+(day||'最新采集日')+'，保留上次成功画面…';$('moversReadStatus').classList.remove('error');
 try{const data=await transport.query({view:'day',...(day?{date:day}:{})},{signal:dayRequest.signal});if(sequence!==daySequence)return;
  saveView();S.data=data;S.leader=null;S.leaderError=null;S.detail=null;S.stockCode=null;S.loadState="empty";chart.drawCurrentBars();$('moversStockPane').hidden=true;$('moversListPane').hidden=false;$('leaderDetails').hidden=true;returnPosition=null;renderList();$('moversScroll').scrollTop=0;setDateOption(data.date);
  $('previousDate').disabled=!data.previous_date;$('nextDate').disabled=!data.next_date;dateStatus();$('connection').textContent='本地';
  syncHistory();ObserverHomeNavigation.remember({module:'movers',date:data.date,direction:S.filter.direction,name:(PAGE==='leader'?'龙头评分':'异动')+' · '+data.date+(S.filter.direction==='up'?' · 大幅上涨':S.filter.direction==='down'?' · 大幅下跌':'')});if(PAGE==='leader')loadLeader(data,sequence);
 }catch(e){if(e.name==='AbortError'||sequence!==daySequence)return;$('moversReadStatus').textContent='读取失败：'+e.message+(S.data?' · 当前仍显示 '+S.data.date:'');$('moversReadStatus').classList.add('error');toast(e.message);if(S.data)setDateOption(S.data.date);}
 finally{if(sequence===daySequence)$('supplementLimits').disabled=!S.data;}
}
async function openStock(code){
 if(!S.data)return;const row=displayData().rows.find(r=>r.code===code);if(!row)return;
 if($('moversStockPane').hidden)returnPosition={scroll:$('moversScroll').scrollTop,code};
 saveView();S.stockCode=code;S.detail=null;S.loadState="loading";chart.drawCurrentBars();const sequence=++stockSequence;stockRequest?.abort();stockRequest=new AbortController();
 $('moversListPane').hidden=true;$('moversStockPane').hidden=false;$('leaderDetails').hidden=true;$('backIndustry').hidden=false;$('backIndustry').textContent=PAGE==='leader'?'返回龙头评分':'返回异动名单';$('instrumentName').textContent=row.name;$('instrumentCode').textContent=code;$('detailStatus').textContent='读取 '+row.name+' '+code+' …';$('retryDetail').hidden=true;$('volumeMode').hidden=true;
 const requestKey=key(),currentDay=S.data.date;
 try{const raw=await transport.query({view:'stock',date:currentDay,...(PAGE==='leader'?{event_date:row.event_date||currentDay}:{}),target:{kind:'stock',id:code},period:S.barPeriod,price_mode:S.barMode,event_revision:row.event_revision,classification_revision:S.data.classification_revision},{signal:stockRequest.signal});if(sequence!==stockSequence||S.data.date!==currentDay)return;
  S.detail=ObserverMoversModel.stock(raw);S.loadState=S.detail.chart.bars.length?"ready":"empty";const saved=views.get(requestKey);S.barRange=saved?.range??(S.barPeriod==='weekly'?'all':150);S.panOffset=saved?.pan??0;
  $('instrumentName').textContent=S.detail.title;$('instrumentCode').textContent=code;$('instrumentMeta').textContent=S.detail.meta;$('parentName').textContent=S.detail.parentLabel;$('dataDate').textContent='行情截至 '+S.detail.date;$('detailStatus').textContent=S.detail.status;$('priceUnit').textContent=S.detail.unitLabel;
  const signal=raw.weekly_strength;$('signal').textContent=signal?.above_now!=null?`30周线 ${signal.above_now?'之上':'之下'} · 连续${signal.consecutive_above_weeks??0}周${signal.streak_is_lower_bound?'+':''}`:'30周信号历史不足';
  $('moversStockNote').textContent=`黄色虚线标记 ${raw.event.date} 异动日，图表截至 ${currentDay}。未采集的日线留空；返回保留分组、下限、筛选和位置。`;
  chart.drawCurrentBars();syncChartButtons();saveView();
 }catch(e){if(e.name==='AbortError'||sequence!==stockSequence)return;S.loadState='error';chart.drawCurrentBars();$('detailStatus').textContent='读取失败：'+e.message+(S.detail?' · 保留上一张图':'');$('retryDetail').hidden=false;toast(e.message);}
}
function back(){saveView();stockRequest?.abort();++stockSequence;S.stockCode=null;S.detail=null;S.loadState='empty';chart.drawCurrentBars();$('moversStockPane').hidden=true;$('moversListPane').hidden=false;renderList();if(returnPosition){$('moversScroll').scrollTop=returnPosition.scroll;const button=$('moversGroups').querySelector(`[data-stock="${returnPosition.code}"]`);button?.focus({preventScroll:true});}}
function syncChartButtons(){document.querySelectorAll('[data-period]').forEach(b=>b.classList.toggle('active',b.dataset.period===S.barPeriod));document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===S.barMode));document.querySelectorAll('[data-range]').forEach(b=>{if(b.dataset.range!=='all')b.textContent=b.dataset.range+(S.barPeriod==='weekly'?'周':'日');});}
$('previousDate').onclick=()=>{if(S.data?.previous_date)loadDay(S.data.previous_date);};$('nextDate').onclick=()=>{if(S.data?.next_date)loadDay(S.data.next_date);};$('latestDate').onclick=()=>loadDay();$('eventDate').onchange=()=>loadDay($('eventDate').value);$('moreDates').onclick=()=>loadDates(true);$('refreshMovers').onclick=()=>loadDay(S.data?.date);
document.querySelectorAll('[data-axis]').forEach(b=>b.onclick=()=>{S.filter.axis=b.dataset.axis==='theme'?'theme':$('boardLevel').value;renderList();$('moversScroll').scrollTop=0;});
$('boardLevel').onchange=()=>{S.filter.axis=$('boardLevel').value;renderList();$('moversScroll').scrollTop=0;};
$('completeScores').onchange=()=>{S.filter.complete_only=$('completeScores').checked;renderList();$('moversScroll').scrollTop=0;};
$('scoreFloor').onchange=()=>{const value=$('scoreFloor').value.trim();S.filter.floor=value===''?35:Math.max(0,Math.min(130,Math.round(Number(value)||0)));$('scoreFloor').value=S.filter.floor;try{localStorage.setItem(FLOOR_KEY,String(S.filter.floor));}catch{}renderList();$('moversScroll').scrollTop=0;};
$('closeLeaderDetails').onclick=()=>{$('leaderDetails').hidden=true;};
$('leaderDetailBody').addEventListener('click',event=>{const button=event.target.closest('[data-stock]');if(button)openStock(button.dataset.stock);});
$('retryLeader').onclick=()=>{if(S.data)loadLeader(S.data,daySequence);};
for(const [id,property] of [['moveDirection','direction'],['moversSort','sort'],['moversSearch','search']])$(id).addEventListener(id==='moversSearch'?'input':'change',()=>{S.filter[property]=$(id).value;if(property==='sort')try{localStorage.setItem(PAGE==='leader'?'guanlan_movers_leader_sort':'guanlan_movers_sort',S.filter.sort);}catch{}if(property==='direction'){syncHistory();if(S.data)ObserverHomeNavigation.remember({module:'movers',date:S.data.date,direction:S.filter.direction,name:(PAGE==='leader'?'龙头评分':'异动')+' · '+S.data.date});}renderList();$('moversScroll').scrollTop=0;});
$('clearTag').onclick=()=>{S.filter.theme='';S.filter.tag='';renderList();};$('backIndustry').onclick=back;$('retryDetail').onclick=()=>openStock(S.stockCode);
document.querySelectorAll('[data-period],[data-mode]').forEach(b=>b.onclick=()=>{saveView();if(b.dataset.period)S.barPeriod=b.dataset.period;if(b.dataset.mode)S.barMode=b.dataset.mode;syncChartButtons();if(S.stockCode)openStock(S.stockCode);});
for(const name of preferences.layers)$(name).onchange=()=>{S[name]=$(name).checked;preferences.set({[name]:S[name]});if(S.detail)chart.drawCurrentBars();};
document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>chart.setChartRange(b.dataset.range==='all'?'all':Number(b.dataset.range)));
$('panOlder').onclick=()=>chart.panChart(1);$('panNewer').onclick=()=>chart.panChart(-1);$('resetView').onclick=()=>chart.resetChart();$('timelineRange').oninput=()=>chart.core.setView({pan:Number($('timelineRange').max)-Number($('timelineRange').value)});$('zoomRange').oninput=()=>chart.setChartRange(Number($('zoomRange').value));
addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('settingsPanel').hidden){settings.show(false);return;}if(event.target.closest('input,select,textarea,dialog,#settingsPanel')||event.target.isContentEditable)return;const direction=preferences.direction(event);if(!direction||!S.stockCode)return;const order=S.display?.order||[];if(!order.length)return;event.preventDefault();openStock(order[(order.indexOf(S.stockCode)+direction+order.length)%order.length]);});
const updates=createObserverUpdates({module:'movers',$,invoke:transport.invoke,getTarget:()=>({kind:'stock',id:S.stockCode}),toast,onChanged:async()=>{const old=S.data,latest=await transport.query({view:'dates',limit:1});if(old?.date===old?.latest_collected_trade_date)await loadDay();else if(old){await loadDay(old.date);if(latest.latest_collected_trade_date!==old.latest_collected_trade_date)toast('已有新采集日 '+latest.latest_collected_trade_date+'，已保留历史日期');}await loadDates();}});
$('supplementLimits').onclick=async()=>{if(!S.data)return;try{const job=await transport.invoke('updates.submit',{mode:'limits',end_date:S.data.date},'global');toast('限制价补齐：'+job.message);updates.poll();}catch(e){toast(e.message);}};
addEventListener('pagehide',()=>{dayRequest?.abort();leaderRequest?.abort();stockRequest?.abort();chart.destroy();settings.destroy();updates.destroy();clearTimeout(toastTimer);});
loadDay(new URLSearchParams(location.search).get('date')||undefined).then(()=>loadDates());updates.start();
})();
