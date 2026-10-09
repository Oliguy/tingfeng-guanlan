/* Credentials live only in this form submission and an HttpOnly session cookie. */
(()=>{'use strict';let pending=null,authorization=null,token=null;
 window.guanlanForgetSession=()=>{token=null;authorization=null;};
 window.guanlanAuthorize=()=>{
  if(token)return Promise.resolve(token);
  if(!authorization)authorization=(async()=>{
   let response=await fetch('/api/v1/health');
   if(response.status===401){await window.guanlanConnect();response=await fetch('/api/v1/health');}
   const health=await response.json();
   if(!response.ok||!health.write_token)throw Error('服务连接失败，请检查地址与配置');
   token=health.write_token;window.guanlanCapabilities=health.capabilities;
   return token;
  })().finally(()=>{authorization=null;});return authorization;
 };
 window.guanlanConnect=()=>{
  if(pending)return pending;
  pending=new Promise((resolve)=>{
   const dialog=document.createElement('dialog');dialog.className='connection-dialog';
   dialog.innerHTML='<form method="dialog"><h2>连接听风观澜</h2><p>输入服务管理员提供的访问令牌。行情和训练记录保存在服务端。</p><label>访问令牌 <input name="token" type="password" autocomplete="off" required minlength="32"></label><p role="status"></p><button type="submit">连接</button></form>';
   document.body.append(dialog);dialog.addEventListener('cancel',e=>e.preventDefault());dialog.showModal();
   const form=dialog.querySelector('form'),input=form.elements.token,button=form.querySelector('button'),status=form.querySelector('[role=status]');
   form.addEventListener('submit',async e=>{e.preventDefault();button.disabled=true;status.textContent='正在连接…';
    try{const response=await fetch('/api/v1/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:input.value})});
     const result=await response.json();if(!response.ok)throw Error(result.detail||'连接失败');
     input.value='';dialog.close();dialog.remove();pending=null;resolve();
    }catch(error){status.textContent=error.message||'服务暂时不可用';button.disabled=false;}
   });input.focus();
  });return pending;
 };
})();
