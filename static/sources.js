// Search preferences, index readiness and link checks are distinct states.
// All source text is inserted with textContent; upstream errors are never HTML.
const date=value=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString('ru-RU'):'—';
const following=new Set();
function notice(message){const el=document.getElementById('sources-notice');el.textContent=message||'';el.hidden=!message}
async function refreshSources(){
 const response=await fetch('/api/sources');if(!response.ok)throw Error('Не удалось получить статусы');const data=await response.json();
 for(const source of data.sources){const select=document.querySelector(`[data-ots="${source.id}"]`);if(select)select.value=source.ots_policy}
 const internet=data.sources.filter(source=>source.id!=='local');
 document.getElementById('sources-summary').textContent=`В поиске: ${internet.filter(source=>source.enabled).length} из ${internet.length} · Готовых индексов: ${internet.filter(source=>source.ready).length}`;
 notice(data.indexer_error);
 for(const source of internet){
  const row=document.querySelector(`[data-source="${source.id}"]`);if(!row)continue;
  const check=source.status,index=source.indexer,job=index?.job,running=job&&['queued','running'].includes(job.status);
  row.querySelector('[data-index-status]').textContent=!source.active?'Неактивен':running?'Обновляется…':source.ready?'Готов':'Нет индекса';
  row.querySelector('[data-index-count]').textContent=source.ready?`${index?.snapshot?.count??'—'} семейств`:job?.status==='failed'?'Получение не удалось':'Ещё не получен';
  row.querySelector('[data-file-mode]').textContent=index?(index.capabilities.includes('download')?'Загрузка в архив':'Файлы на сайте источника'):'Нет данных о загрузке';
  const error=!source.active?source.inactive_reason:job?.status==='failed'?job.error:'';
  row.querySelector('[data-source-error]').textContent=error;row.querySelector('[data-source-error]').hidden=!error;
  row.querySelector('.source-diagnostics summary').textContent=error?'Причина ошибки':'Диагностика';
  row.querySelector('.source-diagnostics').classList.toggle('has-error',Boolean(error));
  row.querySelector('.source-status').textContent=!check?'Ссылка не проверена':check.available?'Ссылка доступна':'Проверка ссылки не удалась';
  row.querySelector('.source-status').title=check?.error||'';
  row.querySelector('.source-updated').textContent=date(index?.snapshot?.created);
  row.querySelector('.source-checked').textContent=check?'Проверено: '+date(check.checked.replace(' ','T')+'Z'):'';
  const toggle=row.querySelector('[data-enabled]');toggle.checked=source.enabled;toggle.disabled=!source.enabled&&!source.can_enable;
  toggle.title=source.can_enable?'Участие сохранённого индекса в поиске':'Сначала получите доступный индекс';
  row.querySelector('[data-enabled-label]').textContent=!source.active?'Неактивен':source.enabled?'Включён':source.can_enable?'Выключен':'Недоступен';
  row.querySelector('[data-enable-hint]').textContent=!source.active?'Адаптер не завершён':source.ready?'':'Нужен готовый индекс';
  row.querySelector('[data-source-search]').hidden=!source.enabled;
  const sync=row.querySelector('[data-sync]');sync.textContent=source.ready?'Обновить индекс':'Получить индекс';sync.disabled=!source.active||Boolean(running)||following.has(source.id);
  if(running&&!following.has(source.id))followJob(source.id,job.id);
 }
}
// Durable jobs survive page reload; reconnect without starting another sync.
async function followJob(source,id){
 const row=document.querySelector(`[data-source="${source}"]`),button=row.querySelector('[data-sync]'),status=row.querySelector('[data-index-status]');following.add(source);button.disabled=true;
 try{while(true){
  const response=await fetch(`/api/sources/${source}/jobs/${id}`);const job=await response.json();if(!response.ok)throw Error(job.error||'Индексатор недоступен');
  status.textContent='Индексация: '+job.phase;
  if(['failed','completed'].includes(job.status)){await refreshSources();break}
  await new Promise(resolve=>setTimeout(resolve,2000));
 }}catch(error){status.textContent=error.message}finally{following.delete(source);button.disabled=false}
}
document.querySelectorAll('[data-sync]').forEach(button=>button.onclick=async()=>{
 button.disabled=true;
 try{const response=await fetch(`/api/sources/${button.dataset.sync}/sync`,{method:'POST'});const data=await response.json();if(!response.ok)throw Error(data.error);await followJob(button.dataset.sync,data.job_id)}
 catch(error){notice(error.message);button.disabled=false}
});
document.querySelectorAll('[data-check]').forEach(button=>button.onclick=async()=>{
 button.disabled=true;
 try{const response=await fetch(`/api/sources/${button.dataset.check}/check`,{method:'POST'});if(!response.ok)throw Error('Ошибка проверки');await refreshSources()}
 catch(error){notice(error.message)}finally{button.disabled=false}
});
// A switch persists a preference; it neither syncs nor downloads a font.
document.querySelectorAll('[data-enabled]').forEach(toggle=>toggle.onchange=async()=>{
 const enabled=toggle.checked;toggle.disabled=true;
 try{const response=await fetch(`/api/sources/${toggle.dataset.enabled}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})});const data=await response.json();if(!response.ok)throw Error(data.error);try{localStorage.setItem('fonthub.sources',String(Date.now()))}catch{}await refreshSources()}
 catch(error){toggle.checked=!enabled;await refreshSources().catch(()=>{});notice(error.message)}
 finally{if(toggle.checked)toggle.disabled=false}
});
refreshSources().catch(error=>notice(error.message));

document.querySelectorAll('[data-ots]').forEach(select=>select.onchange=async()=>{select.disabled=true;try{const response=await fetch('/api/sources/'+select.dataset.ots+'/ots-policy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:select.value})});const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось сохранить');notice(data.message)}catch(error){notice(error.message);await refreshSources()}finally{select.disabled=false}});
