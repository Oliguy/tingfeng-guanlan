/* Presentation primitives, shared by model projections and DOM views. */
(()=>{'use strict';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>v==null?'—':Math.abs(v)>=1e8?(v/1e8).toFixed(2)+'亿':Math.abs(v)>=1e4?(v/1e4).toFixed(1)+'万':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
const pct=v=>v==null?'—':(v*100).toFixed(2)+'%',price=v=>v==null?'—':Number(v).toFixed(3);
const tone=(el,v)=>{el.classList.toggle('positive',v>0);el.classList.toggle('negative',v<0);};
window.ObserverFormat={esc,money,pct,price,tone};
})();
