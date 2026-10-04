// Install a completed background preview without reloading or losing the user's
// specimen, size, colors or variable-axis choices. No binary is fetched early.
(()=>{
 const notice=document.getElementById('preview-notice');if(!notice)return;
 async function poll(){
  try{
   const response=await fetch('/api/fonts/'+encodeURIComponent(notice.dataset.face)+'/preview-status');if(!response.ok)throw Error();const data=await response.json();
   if(data.available){const font=await new FontFace('preview',`url('/font/${encodeURIComponent(notice.dataset.face)}')`).load();document.fonts.add(font);document.getElementById('sample').dataset.previewReady='1';notice.hidden=true;update();return}
   const message=data.status==='queued'?'Предпросмотр готовится.':data.status==='error'?'Предпросмотр не удалось подготовить: '+data.error:'Предпросмотр недоступен.';
   if(notice.firstChild?.nodeType===Node.TEXT_NODE)notice.firstChild.textContent=message+' Оригинал можно скачать. ';
  }catch{}
  setTimeout(poll,5000);
 }
 poll();
})();
