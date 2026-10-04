// Only an explicit preparation click schedules work. Polling reads job state;
// it never downloads upstream files or starts conversions by itself.
(()=>{
 const table=document.querySelector('[data-export-face]');if(!table)return;
 const face=encodeURIComponent(table.dataset.exportFace),body=document.getElementById('export-options'),notice=document.getElementById('export-notice');
 let busy=false,autodownload=null;
 const node=(tag,value)=>{const el=document.createElement(tag);el.textContent=value;return el};
 const size=bytes=>bytes==null?'?':(bytes/1024).toLocaleString('ru-RU',{maximumFractionDigits:1})+' КБ';
 async function refresh(){
  if(busy)return;busy=true;
  try{
   const response=await fetch('/api/fonts/'+face+'/exports');if(!response.ok)throw Error('Не удалось получить варианты скачивания');const data=await response.json();
   if(body.contains(document.activeElement))return;
   const fragment=document.createDocumentFragment();
   const row=(format,method,bytes,action)=>{const tr=document.createElement('tr'),last=document.createElement('td');last.className='download-column';last.append(action);tr.append(node('td',format),node('td',method),node('td',size(bytes)),last);fragment.append(tr)};
   const download=(url,label)=>{const a=node('a','💾');a.href=url;a.title=label;a.setAttribute('aria-label',label);return a};
   // Always expose the untouched original, including an entire TTC collection.
   row(data.original.format+' · оригинал','В архиве',data.original.size,download(data.original.url,'Скачать оригинал '+data.original.name));
   for(const item of data.rows){
    if(item.method==='Оригинал')continue;
    let action;
    if(item.url){action=download(item.url,'Скачать '+item.format);if(autodownload===item.id){autodownload=null;const a=download(item.url,'Скачать');document.body.append(a);a.click();a.remove();notice.textContent='Файл готов. Скачивание началось.'}}
    else {action=node('button',item.status==='error'?'!':item.status==='queued'||item.status==='running'?'…':item.supported?'↓':'—');action.dataset.format=item.id;action.disabled=!item.supported||item.status==='queued'||item.status==='running'||item.status==='error';action.title=item.error||'Подготовить '+item.format;action.setAttribute('aria-label',action.title)}
    const labels={queued:'В очереди',running:'Подготовка',error:'Ошибка',unavailable:'Недоступен',available:'Подготовить',ready:'Готов'};
    row(item.format,item.status==='ready'?'Подготовлен':labels[item.status],item.size,action);
   }
   body.replaceChildren(fragment);
  }catch(error){notice.textContent=error.message}finally{busy=false}
 }
 body.addEventListener('click',async event=>{const button=event.target.closest('button[data-format]');if(!button||button.disabled)return;button.disabled=true;try{
  const response=await fetch('/api/fonts/'+face+'/exports/'+button.dataset.format,{method:'POST'}),data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось подготовить файл');
  if(data.url){location.href=data.url}else{autodownload=button.dataset.format;notice.textContent='Файл добавлен в очередь. Скачивание начнётся после подготовки, пока эта страница открыта.'}
  button.blur();await refresh();
 }catch(error){notice.textContent=error.message;button.disabled=false}});
 refresh();setInterval(refresh,3000);
})();
