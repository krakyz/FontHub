// Explicit candidate generation; comparison is text, never upstream HTML.
const repairDialog=document.getElementById('repair-dialog');let repairHash,repairTimer,repairGeneration=0;
function repairText(tag,value){const node=document.createElement(tag);node.textContent=value;return node}
async function repairPost(url,data={}){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw Error(value.error||'Операция не выполнена');return value}
async function refreshRepair(generation){
 try{const response=await fetch('/api/repairs/'+repairHash);if(!response.ok)throw Error('Не удалось получить сравнение');const data=await response.json();if(generation!==repairGeneration||!repairDialog.open)return;
 const state=document.getElementById('repair-state'),area=document.getElementById('repair-comparison'),select=document.getElementById('repair-source');
 if(!select.options.length)for(const source of data.sources)select.add(new Option(source,source));
 document.getElementById('repair-original').disabled=!data.sources.length;
 document.getElementById('repair-candidate').disabled=data.status!=='ready';
 state.textContent=data.status==='ready'?'Копия прошла повторную проверку OTS. Проверьте изменения перед сохранением.':data.status==='error'?'Исправление не удалось: '+data.error:'Подготовка исправления в отдельной очереди…';
 if(data.status==='ready'){
  const report=data.report;area.replaceChildren(repairText('p',report.method),repairText('p',`Размер: ${report.original_size} → ${report.candidate_size} байт. Начертания: ${report.original_faces??'?'} → ${report.candidate_faces}.`));
  if(report.original_error)area.append(repairText('p','Оригинал не удалось полностью прочитать: '+report.original_error));
  const table=document.createElement('table');table.className='repair-table';const header=document.createElement('tr');header.append(repairText('th','Параметр'),repairText('th','Оригинал'),repairText('th','Обработанная копия'));table.append(header);
  for(const face of report.faces){for(const [label,field] of [['Семейство','family'],['Начертание','style'],['Глифы','glyphs'],['Символы','chars'],['Таблицы','tables'],['OpenType-функции','features']]){const row=document.createElement('tr');const display=value=>value===undefined?'Не прочитано':Array.isArray(value)?field==='chars'?value.length:value.join(', '):value;row.append(repairText('th',`№ ${face.index+1} · ${label}`),repairText('td',display(face.original?.[field])),repairText('td',display(face.candidate[field])));table.append(row)}
   area.append(repairText('p',`Начертание ${face.index+1}: удалено символов ${face.removed_chars?.length??'?'}, добавлено ${face.added_chars?.length??'?'}. Удалённые таблицы: ${face.removed_tables?.join(', ')||'нет'}. Удалённые функции: ${face.removed_features?.join(', ')||'нет'}.`));
   if(face.original?.complete===false)area.append(repairText('p','Покрытие оригинала прочитано частично: сравнение символов неполное. '+(face.original.warnings||[]).join(' ')));
  }area.prepend(table);area.append(repairText('p','Сравнение структуры и символов не гарантирует одинаковое формирование текста или контуры.'));
  const link=repairText('a','Скачать обработанную копию');link.href='/repairs/'+repairHash+'/file';area.append(link);
 }
 if(['queued','running'].includes(data.status))repairTimer=setTimeout(()=>refreshRepair(generation),1500);
 }catch(error){document.getElementById('repair-state').textContent=error.message}
}
document.querySelectorAll('[data-repair]').forEach(button=>button.onclick=async()=>{clearTimeout(repairTimer);const generation=++repairGeneration;repairHash=button.dataset.repair;document.getElementById('repair-comparison').replaceChildren();document.getElementById('repair-source').replaceChildren();document.getElementById('repair-original').disabled=true;document.getElementById('repair-candidate').disabled=true;document.getElementById('repair-state').textContent='Постановка исправления в очередь…';repairDialog.showModal();try{await repairPost('/api/repairs/'+repairHash);await refreshRepair(generation)}catch(error){document.getElementById('repair-state').textContent=error.message}});
document.getElementById('repair-close').onclick=()=>repairDialog.close();repairDialog.addEventListener('close',()=>{clearTimeout(repairTimer);repairGeneration++});
for(const choice of ['original','candidate'])document.getElementById('repair-'+choice).onclick=async()=>{const button=document.getElementById('repair-'+choice);button.disabled=true;try{const data=await repairPost('/api/repairs/'+repairHash+'/choose',{choice,source:document.getElementById('repair-source').value});document.getElementById('repair-state').textContent=data.message;const link=repairText('a','Открыть очередь');link.href=data.url;document.getElementById('repair-comparison').append(link)}catch(error){document.getElementById('repair-state').textContent=error.message}finally{button.disabled=false}};
