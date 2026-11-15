'use strict';
const list=document.getElementById('messages'),detail=document.getElementById('detail'),draft=document.getElementById('draft');
let messages=[],selectedId=null;
function el(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
function select(id){
 selectedId=id;const m=messages.find(x=>x.id===id);if(!m)return;
 for(const b of list.querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.id===id));
 detail.replaceChildren(el('h3',m.subject),el('div',m.from+' · '+m.date,'meta'),el('p',m.body),el('h4','Excerpt (not AI-generated)'),el('p',m.summary));
 const b=el('button','Prepare a reply draft');b.className='action';
 b.addEventListener('click',async()=>{b.disabled=true;try{
 const r=await fetch('/api/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
 if(!r.ok)throw Error('Draft unavailable');const data=await r.json();
 if(selectedId===id)draft.replaceChildren(el('h4','Unsent draft'),el('pre',data.draft));
 }catch(e){if(selectedId===id)draft.replaceChildren(el('p',String(e)));}finally{b.disabled=false;}});
 detail.append(b);draft.replaceChildren();
}
async function init(){try{
 const response=await fetch('/api/messages');if(!response.ok)throw Error('Could not load sample messages');
 const data=await response.json();messages=data.messages;list.replaceChildren();
 for(const m of messages){const b=el('button');b.className='message';b.dataset.id=m.id;b.setAttribute('aria-pressed','false');b.append(el('div',m.subject),el('div',m.priority+' priority · '+m.from,'meta'));b.addEventListener('click',()=>select(m.id));list.append(b);}
 if(messages.length)select(messages[0].id);
 }catch(e){list.textContent=String(e);}}
init();
