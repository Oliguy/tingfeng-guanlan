/* Explicit theme editing. Market collection is owned by the existing global updater. */
(()=>{'use strict';
window.createObserverThemes=({module,$,invoke,toast,onChanged,getTarget})=>{
 if(module!=='theme')return;let current=null,draft=null,heads=[],source={},busy=false;
 $('manageThemes').hidden=false;
 const run=async fn=>{if(busy)return;busy=true;try{await fn();}catch(e){$('themePreview').textContent=e.message;toast(e.message);}finally{busy=false;}};
 const invalidate=()=>{draft=null;$('saveTheme').disabled=true;$('themePreview').textContent='修改后请先预览。';};
 function fill(value){current=value;source=value?.source||{};$('themeName').value=value?.name||'';$('themeSource').value=source.title||'';
  $('themeMembers').value=(value?.members||[]).map(m=>m.code+' | '+m.paths.map(p=>p.join(' > ')).join('; ')).join('\n');
  for(const id of ['themeName','themeSource','themeMembers','previewTheme'])$(id).disabled=!!value?.head_archived;
  $('archiveTheme').hidden=!value;$('archiveTheme').textContent=value?.head_archived?'恢复题材':'移出观察列表';$('applyTheme').hidden=!value||value.head_archived;
  invalidate();$('themePreview').textContent=value?(value.head_archived?'已归档，成员、标签和旧发布仍保留。':`修订 ${value.revision} · ${value.members.length}只成员。`):'输入一个题材及其成员。';
 }
 async function reload(selectId){heads=(await invoke('themes.list')).items;$('themeList').replaceChildren();
  for(const head of heads){const b=document.createElement('button');b.textContent=head.name+(head.archived?' · 已归档':'');b.onclick=()=>run(async()=>fill(await invoke('themes.get',{id:head.id})));$('themeList').append(b);}
  if(selectId&&heads.some(h=>h.id===selectId))fill(await invoke('themes.get',{id:selectId}));
 }
 function payload(){return {name:$('themeName').value,source:{...source,title:$('themeSource').value},members:$('themeMembers').value.split(/\r?\n/).filter(s=>s.trim()).map(line=>{
  const parts=line.split('|');if(parts.length>2)throw Error('每行只允许一个 | 分隔成员与标签');
  return {code:parts[0].trim(),paths:(parts[1]||'').split(/[;；]/).filter(s=>s.trim()).map(path=>path.split('>').map(s=>s.trim()))};
 })};}
 async function apply(){const job=await invoke('themes.apply',{id:current.id,expected_revision:current.revision});
  $('themePreview').textContent=job.params?.target===current.id?'已提交本地计算，可在“更新状态”查看结果。计算完成前继续保留原发布。':'目录已保存；另一个更新任务正在运行，结束后点击“应用当前修订”。';
  toast('目录已保存；本地应用状态见管理面板');await onChanged();
 }
 $('manageThemes').onclick=()=>run(async()=>{await reload(getTarget());if(!current)fill(null);$('themesDialog').showModal();});
 $('closeThemes').onclick=()=>$('themesDialog').close();$('newTheme').onclick=()=>fill(null);
 for(const id of ['themeName','themeSource','themeMembers'])$(id).oninput=invalidate;
 $('themeForm').onsubmit=e=>{e.preventDefault();run(async()=>{draft=await invoke('themes.preview',{payload:payload(),...(current?{id:current.id,expected_revision:current.revision}:{})});
  $('themePreview').textContent=`${draft.member_count}只股票 · ${draft.tag_relations}条标签关系\n新增 ${draft.added.length}只，移出 ${draft.removed.length}只；重复行合并 ${draft.duplicate_rows_merged??0}条。`+(draft.issues.length?'\n未解决：'+draft.issues.map(i=>JSON.stringify(i)).join('\n'):'\n预览通过，可保存并本地应用。');$('saveTheme').disabled=!!draft.issues.length;
 });};
 $('saveTheme').onclick=()=>run(async()=>{if(!draft)return;const result=await invoke('themes.save',{draft});await reload(result.id);await apply();});
 $('applyTheme').onclick=()=>run(apply);
 $('archiveTheme').onclick=()=>run(async()=>{const restored=current.head_archived,result=await invoke(restored?'themes.restore':'themes.archive',{id:current.id,expected_revision:current.revision});await reload(result.id);await onChanged();$('themePreview').textContent=restored?'已恢复；点击“应用当前修订”重建索引。':'已移出观察列表，仍可恢复；共享股票行情未改动。';});
};
})();
