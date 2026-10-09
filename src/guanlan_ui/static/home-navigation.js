/* Cross-column navigation stores only the last successful observation identity. */
(()=>{'use strict';const KEY='stockObserver:home:recent:v1',module=document.body.dataset.observerModule;
window.ObserverHomeNavigation={remember(record){if(!['industry30','etf','theme','movers'].includes(record.module))return;try{localStorage.setItem(KEY,JSON.stringify({...record,at:Date.now()}));}catch{}},read(){try{const r=JSON.parse(localStorage.getItem(KEY)||'null');return r&&['industry30','etf','theme','movers'].includes(r.module)&&typeof r.name==='string'&&r.name.length<=150?r:null;}catch{return null;}},clear(){try{localStorage.removeItem(KEY);}catch{}}};
if(module==='home')return;
const fromHome=new URLSearchParams(location.search).get('from')==='home';
if(fromHome){const header=document.querySelector('header.global,.movers-header');if(header){const a=document.createElement('a');a.href='/home/';a.textContent='← 返回首页';a.className='text-link';a.id='backHome';a.style.cssText='white-space:nowrap;color:#b7d8f4;font-size:12px';header.prepend(a);}}
})();
