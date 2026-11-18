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
    location.assign('/user/dashboard');
  }catch(error){status.textContent=error.message;button.disabled=false;}
});
