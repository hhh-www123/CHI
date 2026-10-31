const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
const dir=fs.mkdtempSync(path.join(__dirname,'../.runtime/p4-protocol-'));let count=0;
const source=fs.readFileSync(path.join(__dirname,'../src/features/agentic-editor/protocol.ts'),'utf8');
fs.writeFileSync(path.join(dir,'protocol.cjs'),ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText);
const {PreviewStream,parseSurface,readPreviewStream}=require(path.join(dir,'protocol.cjs'));
const start={type:'RUN_STARTED',threadId:'trip',runId:'run'},end={type:'RUN_FINISHED',threadId:'trip',runId:'run'};
const candidate={candidateId:'c',document:{dayOrder:['d'],entities:{days:{d:{activityIds:['a']}},activities:{a:{}}}}};
const valid=[{version:'v0.9.1',createSurface:{surfaceId:'c',catalogId:'urn:travel-genui:readonly-catalog:1'}},{version:'v0.9.1',updateComponents:{surfaceId:'c',components:[{id:'root',component:'TripCard',children:['x']},{id:'x',component:'ActivityCard',activityId:'a'}]}}];
(async()=>{try{
assert.deepEqual(parseSurface(valid,candidate,['a']),['a']);count++;
for(const change of[
 m=>m[1].updateComponents.components[1].component='HTML',m=>m[1].updateComponents.components[1].html='<script>x</script>',m=>m[1].updateComponents.components[1].href='javascript:alert(1)',m=>m[1].updateComponents.components[1].onClick='alert(1)',m=>m[1].updateComponents.components[1].activityId='other',m=>m[1].updateComponents.components[0].children=['root'],m=>m[0].createSurface.surfaceId='other',m=>m[0].version='v1.0'
]){const copy=structuredClone(valid);change(copy);assert.throws(()=>parseSurface(copy,candidate,['a']));count++;}
for(const event of[{type:'STATE_DELTA',delta:[{op:'replace',path:'/version',value:99}]},{type:'STATE_SNAPSHOT',snapshot:{version:99}},{type:'CUSTOM',name:'commit',value:{}},{...end,runId:'other'},{...start}]){const s=new PreviewStream('trip',()=>{});s.accept(start);assert.throws(()=>s.accept(event));count++;}
const s=new PreviewStream('trip',()=>{});s.accept(start);s.accept({type:'STATE_SNAPSHOT',snapshot:{previewOnly:true,baseVersion:1,candidateIds:[]}});assert.throws(()=>s.result());count++;
s.accept({type:'CUSTOM',name:'candidates.complete',value:{id:'draft'}});s.accept(end);assert.deepEqual(s.result(),{id:'draft'});assert.throws(()=>s.accept(end));count++;
const enc=new TextEncoder(),wire=[start,{type:'CUSTOM',name:'candidates.complete',value:{text:'旅程'}},end].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('');const bytes=enc.encode(wire);
const response=new Response(new ReadableStream({start(c){for(const b of bytes)c.enqueue(Uint8Array.of(b));c.close();}}));assert.deepEqual(await readPreviewStream(response,'trip',()=>{}),{text:'旅程'});count++;
await assert.rejects(()=>readPreviewStream(new Response('data: '+JSON.stringify(start)+'\n\n'),'trip',()=>{}),/中断/);count++;
await assert.rejects(()=>readPreviewStream(new Response('data: {not-json}\n\n'),'trip',()=>{}));count++;
console.log(JSON.stringify({passed:true,checks:count,formalStateAccess:false}));
}finally{fs.unlinkSync(path.join(dir,'protocol.cjs'));fs.rmdirSync(dir);}})().catch(e=>{console.error(e);process.exitCode=1;});
