document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-import]');if(!button)return;
 button.disabled=true;const status=document.getElementById('remote-status')||document.getElementById('notice');status.textContent='Загрузка и анализ семейства…';
 try{const response=await fetch('/api/sources/'+button.dataset.source+'/import/'+button.dataset.import,{method:'POST'});const data=await response.json();if(!response.ok)throw Error(data.error||'Ошибка импорта');const sample=new URLSearchParams(location.search).get('sample');location.href=data.url+(sample!==null?'?sample='+sample:'')}
 catch(error){status.textContent=error.message;button.disabled=false}
});

// Only this explicit button fetches an external preview. Merely opening a
// remote family, changing filters or paging never downloads a font binary.
const previewButton=document.getElementById('show-remote-preview');
if(previewButton)previewButton.onclick=async()=>{previewButton.disabled=true;try{const font=await new FontFace('remote',`url(/font/${previewButton.dataset.fontId})`).load();document.fonts.add(font);document.getElementById('sample').style.fontFamily='remote,sans-serif';previewButton.textContent='Образец загружен';document.getElementById('preview-status').textContent='Показан первый файл семейства. Языковое покрытие и характеристики ещё не проверены.'}catch{previewButton.disabled=false;previewButton.textContent='Не удалось загрузить. Повторить'}};

// Same specimen controls as the archive page, but no character-coverage claim:
// index metadata cannot establish which glyphs the preview file actually has.
const text=document.getElementById('sample-text'),sample=document.getElementById('sample');
if(text&&sample){
 try{text.value=localStorage.getItem('fonthub.sample')??text.value;const param=new URLSearchParams(location.search).get('sample');if(param!==null)text.value=new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(param.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0)))}catch{}
 const update=()=>{sample.textContent=text.value;sample.style.fontSize=document.getElementById('size').value+'px';document.getElementById('size-label').textContent=document.getElementById('size').value;sample.style.color=document.getElementById('text-color').value;sample.style.backgroundColor=document.getElementById('background-color').value;document.getElementById('missing').textContent='Проверка символов образца недоступна до импорта.'};
 document.querySelectorAll('.preview-card input,.preview-card textarea').forEach(el=>el.addEventListener('input',update));
 document.querySelectorAll('[data-fg]').forEach(button=>button.onclick=()=>{document.getElementById('text-color').value=button.dataset.fg;document.getElementById('background-color').value=button.dataset.bg;update()});update();
}
