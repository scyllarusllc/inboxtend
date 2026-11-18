'use strict';
const form=document.getElementById('login-form');
const status=document.getElementById('login-status');
form.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=form.querySelector('button[type="submit"]');
  button.disabled=true;status.textContent='';
  try{
    const body=Object.fromEntries(new FormData(form).entries());
    const response=await fetch('/api/auth/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok)throw Error(result.error||'Sign in failed');
    location.assign('/user/inbox');
  }catch(error){status.textContent=error.message;button.disabled=false;}
});

// Sign in with Google is offered only when the server is set up for it.
fetch('/api/auth/providers',{credentials:'same-origin'}).then(r=>r.json()).then(p=>{document.getElementById('google-signin').hidden=!p.google;}).catch(()=>{});
const MESSAGES={declined:'Google sign-in was cancelled.',failed:'Google sign-in did not work. Please try again.',expired:'That sign-in took too long or was opened in another browser. Please try again.',
  unverified:'Google says this email address is not verified, so it cannot be used.',unknown:'No account here uses that Google account. Sign in with your password, then link it under Settings \u2192 Account.',
  taken:'That Google account is linked to another user.',off:'Sign in with Google is not set up on this server.'};
const outcome=new URLSearchParams(location.search).get('google');
if(outcome&&MESSAGES[outcome])status.textContent=MESSAGES[outcome];
