// Server owns claims, pause and history. Poll only the selected 25-row page;
// upstream font names/errors are text, never executable HTML. Keep focus/forms.
const $=id=>document.getElementById(id);
const kinds={import:'Приём',ots:'Проверка OTS',repair:'Исправление',analysis:'Анализ',preview:'Предпросмотр',convert:'Конвертация',download:'Загрузка'};
const states={blocked:'Ожидает допуска OTS',queued:'Ожидает',running:'В работе',done:'Завершено',error:'Ошибка',cancelled:'Отменено'};
const phases={gate:'Ожидание допуска',sanitizing:'Проверка OpenType Sanitizer',downloading:'Скачивание',conversion:'Конвертация',waiting:'В очереди',starting:'Начало обработки',hashing:'Проверка файла',copying:'Сохранение оригинала',analysis:'Анализ начертания',cataloguing:'Добавление в каталог',cleanup:'Завершение импорта',preview:'Создание WOFF2',verification:'Проверка предпросмотра',publishing:'Сохранение предпросмотра',complete:'Готово',failed:'Не удалось завершить'};
let page=1,loading=false,controls={},generation=0;
function text(tag,value,className){const element=document.createElement(tag);element.textContent=value;if(className)element.className=className;return element}
function notice(value){$('queue-notice').textContent=value||''}
async function post(url,data){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error||'Операция не выполнена');return result}
async function refresh(){
 if(loading)return;loading=true;const token=generation;
 try{
  const params=new URLSearchParams({kind:$('kind').value,status:$('status').value,page});
  const response=await fetch('/api/imports?'+params);if(!response.ok)throw Error('Очередь недоступна');const data=await response.json();if(token!==generation)return;
  page=data.page;controls=data.controls;
  $('queue-summary').textContent=`Ожидают: ${Object.values(data.counts).reduce((sum,c)=>sum+c.queued,0)} · Ошибки: ${Object.values(data.counts).reduce((sum,c)=>sum+c.error,0)}`;
  for(const row of document.querySelectorAll('[data-kind]')){
   const kind=row.dataset.kind;for(const cell of row.querySelectorAll('[data-count]'))cell.textContent=data.counts[kind][cell.dataset.count];
   const job=data.running.find(job=>job.kind===kind);row.querySelector('[data-running]').textContent=job?`${job.filename} · ${phases[job.phase]||job.phase}${job.detail?' · '+job.detail:''} · ${Math.max(0,Math.floor((data.now-job.started)/60))} мин`:'Нет текущего задания';
   const button=row.querySelector('[data-toggle]');button.disabled=false;button.textContent=controls[kind]?'Продолжить':'Приостановить';row.querySelector('[data-state]').textContent=controls[kind]?(job?'Пауза после текущего файла':'На паузе'):'Обработка включена';
  }
  // Do not replace a focused retry button underneath keyboard/pointer users.
  if(!$('queue-jobs').contains(document.activeElement)){
   const fragment=document.createDocumentFragment();
   for(const job of data.jobs){
    const row=document.createElement('tr');const file=document.createElement('td');file.append(text('strong',job.filename));
    if(job.kind==='preview'||job.kind==='convert')file.append(text('small',(job.family||'')+' '+(job.style||'')));
    if(job.kind==='download'&&job.status==='done'){const link=text('a','Скачать файл');link.href='/downloads/'+job.id+'/file';file.append(link)}
    if(job.kind==='analysis'){const link=text('a','Скачать оригинал');link.href='/originals/'+job.identity;file.append(link)}
    if(job.result){const original=['import','ots'].includes(job.kind)&&/^[a-f0-9]{64}$/.test(job.result);const link=text('a',original?'Скачать оригинал':'Открыть шрифт');const sample=new URLSearchParams(location.search).get('sample');link.href=(original?'/originals/':'/fonts/')+encodeURIComponent(job.result)+(sample===null?'':'?sample='+encodeURIComponent(sample));file.append(link)}
    const status=document.createElement('td');status.append(text('strong',states[job.status]));if(!['error','cancelled'].includes(job.status))status.append(text('small',phases[job.phase]||job.phase));if(job.detail)status.append(text('small',job.detail));
    if(job.error){const details=document.createElement('details');details.append(text('summary','Причина ошибки'),text('p',job.error));status.append(details)}
    const action=document.createElement('td');if(job.status==='error'){const button=text('button','Повторить');button.dataset.retry=job.id;button.setAttribute('aria-label','Повторить '+job.filename);action.append(button)}else if(job.kind==='download'&&['queued','running'].includes(job.status)){const button=text('button','Отменить');button.dataset.cancel=job.id;action.append(button)}else action.textContent='—';
    row.append(file,text('td',sourceNames[job.source]||job.source),text('td',kinds[job.kind]),status,action);fragment.append(row);
   }
   if(!data.jobs.length){const row=document.createElement('tr'),cell=text('td','Заданий с выбранными условиями нет.');cell.colSpan=5;row.append(cell);fragment.append(row)}
   $('queue-jobs').replaceChildren(fragment);
  }
  $('queue-shown').textContent=data.total?`Показано ${data.jobs.length} из ${data.total} · ${(page-1)*25+1}–${Math.min(page*25,data.total)}`:'Нет заданий';
  // Match search: a moving ten-page window, endpoint links and adjacent pages.
  const pages=document.createDocumentFragment();
  const pageLink=(target,label=target)=>{const link=text('a',label);link.href='#queue-pages';link.dataset.page=target;return link};
  if(data.pages>1){
   pages.append('Страницы: \u00a0');
   if(page>1)pages.append(pageLink(page-1,'Пред.'),'\u00a0\u00a0');
   const start=Math.max(1,Math.min(page-4,data.pages-9)),end=Math.min(start+9,data.pages);
   if(start>1)pages.append(pageLink(1),' … ');
   for(let target=start;target<=end;target++){
    if(target>start)pages.append(', ');
    if(target===page){const current=text('b',target);current.setAttribute('aria-current','page');pages.append(current)}else pages.append(pageLink(target));
   }
   if(end<data.pages)pages.append(' … ',pageLink(data.pages));
   if(page<data.pages)pages.append('\u00a0\u00a0',pageLink(page+1,'След.'));
  }
  $('queue-pages').replaceChildren(pages);
 }catch(error){notice(error.message)}finally{loading=false;if(token!==generation)refresh()}
}
document.addEventListener('click',async event=>{
 const pageLink=event.target.closest('#queue-pages a[data-page]');
 if(pageLink){event.preventDefault();page=Number(pageLink.dataset.page);generation++;refresh();return}
 const button=event.target.closest('button');if(!button)return;
 if(!button.dataset.toggle&&!button.dataset.retry&&!button.dataset.cancel)return;
 button.disabled=true;
 try{if(button.dataset.toggle)await post('/api/imports/control',{kind:button.dataset.toggle,paused:!controls[button.dataset.toggle]});else if(button.dataset.cancel)await post('/api/downloads/'+button.dataset.cancel+'/cancel',{});else await post('/api/imports/'+button.dataset.retry+'/retry',{});notice('');button.blur();await refresh()}catch(error){notice(error.message)}finally{button.disabled=false}
});
for(const id of ['kind','status'])$(id).addEventListener('change',()=>{page=1;generation++;refresh()});
$('add').onclick=()=>$('files').click();$('files').onchange=async()=>{const body=new FormData();for(const file of $('files').files)body.append('files',file);notice('Загрузка…');try{const response=await fetch('/api/upload',{method:'POST',body});const data=await response.json();if(!response.ok)throw Error(data.error||'Загрузка не удалась');notice(data.message);await refresh()}catch(error){notice(error.message)}finally{$('files').value=''}};
$('scan').onclick=async()=>{const button=$('scan');button.disabled=true;try{notice((await post('/api/scan',{})).message);await refresh()}catch(error){notice(error.message)}finally{button.disabled=false}};
refresh();setInterval(refresh,3000);
