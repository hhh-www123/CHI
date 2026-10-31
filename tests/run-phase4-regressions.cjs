// Owns only the dedicated 8013 no-key regression fixture.
const fs=require('node:fs'),path=require('node:path'),net=require('node:net');const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:8013',data=path.join(root,'.runtime/phase1-browser');let child;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function run(script,prefix){return new Promise((resolve,reject)=>{const out=fs.openSync(path.join(root,'docs/verification',prefix+'.log'),'w');const proc=spawn(process.execPath,[script],{cwd:root,env:{...process.env,UI_ARTIFACT_PREFIX:prefix},stdio:['ignore',out,out],windowsHide:true});proc.on('exit',code=>{fs.closeSync(out);code===0?resolve():reject(Error(script+' exit '+code));});});}
(async()=>{try{
 const busy=await new Promise(resolve=>{const sock=net.connect(8013,'127.0.0.1');sock.once('connect',()=>{sock.destroy();resolve(true)});sock.once('error',()=>resolve(false));});if(busy)throw Error('8013 already occupied; no process stopped');
 fs.mkdirSync(data,{recursive:true});const fd=fs.openSync(path.join(data,'phase4-server.log'),'a');
 child=spawn(path.join(root,'.venv/Scripts/python.exe'),['-B','-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8013'],{cwd:root,env:{...process.env,TRAVEL_ENV_FILE:path.join(data,'absent.env'),TRAVEL_GENUI_DATA_DIR:data,AGENT_MODE:'demo',CANDIDATE_AGENT_MODE:'mock',CANDIDATE_TTL_SECONDS:'30',LLM_API_KEY:'',DEEPSEEK_API_KEY:'',AMAP_KEY:'',AGENT_REQUESTS_PER_MINUTE:'120'},stdio:['ignore',fd,fd],windowsHide:true});
 let ready=false;for(let i=0;i<80;i++){try{const h=await(await fetch(base+'/api/health')).json();if(h.projectRoot===root){ready=true;break;}}catch{}await delay(200);}if(!ready)throw Error('Regression server not ready');
 await run('tests/phase1-ui.cjs','phase4-p1');await run('tests/phase2-ui.cjs','phase4-p2');console.log('PASS original Phase1 12 groups + Phase2 9 groups');
 }finally{if(child){try{const h=await(await fetch(base+'/api/health')).json();if(h.projectRoot!==root)throw Error('Unexpected server owner');process.kill(h.processId);}finally{child.kill();}}}})().catch(e=>{console.error(e);process.exitCode=1;});
