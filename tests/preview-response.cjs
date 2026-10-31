// Observe the same browser fetch via a read-only clone. Chromium CDP does not
// reliably retain response.text() for SSE; no extra generation request is sent.
async function install(page){
 const observer=()=>{if(window.__testPreviewCaptureInstalled)return;window.__testPreviewCaptureInstalled=true;window.__testPreviewStreams=[];const original=window.fetch.bind(window);window.fetch=async(...args)=>{const response=await original(...args);if((response.headers.get('content-type')||'').includes('text/event-stream')){const item={url:response.url,body:args[1]?.body||'',text:null,error:null};window.__testPreviewStreams.push(item);response.clone().text().then(t=>item.text=t,e=>item.error=String(e));}return response;};};
 await page.addInitScript(observer);await page.evaluate(observer);
}
module.exports=async function previewResponse(response){
 if(typeof response.frame!=='function')return response.json();
 const page=response.frame().page();
 if(!(response.headers()['content-type']||'').includes('text/event-stream')){await install(page);return response.json();}
 const url=response.url(),body=response.request().postData();
 await page.waitForFunction(({url,body})=>{const item=window.__testPreviewStreams?.findLast(x=>x.url===url&&x.body===body);return item&&(item.text!==null||item.error);},{url,body});
 const text=await page.evaluate(({url,body})=>{const item=window.__testPreviewStreams.findLast(x=>x.url===url&&x.body===body);if(item.error)throw Error(item.error);return item.text;},{url,body});
 const events=text.split('\n').filter(l=>l.startsWith('data: ')).map(l=>JSON.parse(l.slice(6)));
 const finish=events.at(-1);if(finish?.type==='RUN_ERROR')return {detail:finish.message,__streamStatus:Number(finish.code)};if(finish?.type!=='RUN_FINISHED')throw Error('Stream did not finish: '+JSON.stringify(finish));
 const complete=events.find(e=>e.type==='CUSTOM'&&e.name==='candidates.complete');if(!complete)throw Error('No validated candidate batch');return complete.value;
};
