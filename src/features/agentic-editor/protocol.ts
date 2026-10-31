import { z } from 'zod';
import type { EditCandidate } from './candidateTypes';
const catalog = 'urn:travel-genui:readonly-catalog:1';
const node = z.union([
  z.object({ id: z.literal('root'), component: z.literal('TripCard'), children: z.array(z.string()).max(32) }).strict(),
  z.object({ id:z.string(), component:z.literal('ActivityCard'), activityId:z.string() }).strict(),
]);
const messages = z.tuple([
  z.object({version:z.literal('v0.9.1'),createSurface:z.object({surfaceId:z.string(),catalogId:z.literal(catalog)}).strict()}).strict(),
  z.object({version:z.literal('v0.9.1'),updateComponents:z.object({surfaceId:z.string(),components:z.array(node).min(1).max(33)}).strict()}).strict(),
]);
// A closed custom Catalog: no HTML, event handlers, CSS, URL, functions or remote assets.
export function parseSurface(value:unknown, candidate:EditCandidate, eligible:string[]):string[] {
 const [create,update]=messages.parse(value);
 if(create.createSurface.surfaceId!==candidate.candidateId || update.updateComponents.surfaceId!==candidate.candidateId)throw Error('候选Surface身份不一致');
 const nodes=update.updateComponents.components, top=nodes[0];
 if(top.component!=='TripCard')throw Error('缺少Catalog根组件');
 const expected=candidate.document.dayOrder.flatMap(id=>candidate.document.entities.days[id].activityIds).filter(id=>eligible.includes(id));
 if(nodes.length!==expected.length+1 || top.children.length!==expected.length || new Set(top.children).size!==top.children.length)throw Error('组件树数量不一致');
 const ids=nodes.slice(1).map((item,i)=>{if(item.component!=='ActivityCard'||item.id!==top.children[i]||item.id==='root'||item.activityId!==expected[i])throw Error('组件数据绑定越界');return item.activityId;});
 return ids;
}
const errorEvent=z.object({type:z.literal('RUN_ERROR'),message:z.string().max(2000),code:z.string()}).strict();
const lifecycle=(type:'RUN_STARTED'|'RUN_FINISHED')=>z.object({type:z.literal(type),threadId:z.string().min(1),runId:z.string().min(1)}).strict();
const custom=z.object({type:z.literal('CUSTOM'),name:z.enum(['scope.started','scope.validated','generation.started','generation.finished','generation.retry','validation.started','validation.finished','candidate.validated','candidates.complete']),value:z.unknown().optional()}).strict();
const snapshot=z.object({type:z.literal('STATE_SNAPSHOT'),snapshot:z.object({previewOnly:z.literal(true),baseVersion:z.number().int().positive(),candidateIds:z.array(z.string())}).strict()}).strict();
const eventSchema=z.union([lifecycle('RUN_STARTED'),lifecycle('RUN_FINISHED'),errorEvent,custom,snapshot]);
export class PreviewStream {
 started=false;finished=false;runId='';batch:unknown;events=0;
 constructor(private threadId:string,private onProgress:(name:string,value?:unknown)=>void){}
 accept(input:unknown){
  if(this.finished || ++this.events>100)throw Error('生成流顺序或事件数无效');
  const e=eventSchema.parse(input);
  if(e.type==='RUN_STARTED'){if(this.started||e.threadId!==this.threadId)throw Error('生成流身份无效');this.started=true;this.runId=e.runId;return;}
  if(!this.started)throw Error('生成流缺少开始事件');
  if(e.type==='RUN_ERROR')throw Object.assign(Error(e.message),{status:Number(e.code)||502});
  if(e.type==='RUN_FINISHED'){if(e.threadId!==this.threadId||e.runId!==this.runId||!this.batch)throw Error('生成流结束事件无效');this.finished=true;return;}
  if(e.type==='STATE_SNAPSHOT')return; // Never write TripDocument. STATE_DELTA is unsupported/rejected.
  if(e.type!=='CUSTOM')throw Error('未知生成事件');
  if(e.name==='candidates.complete'){if(this.batch)throw Error('重复候选批次');this.batch=e.value;}
  this.onProgress(e.name,e.value);
 }
 result(){if(!this.finished||!this.batch)throw Error('生成流中断，草稿已丢弃；正式行程未修改，请重试。');return this.batch;}
}
export async function readPreviewStream(response:Response,id:string,onProgress:(name:string,value?:unknown)=>void):Promise<unknown>{
 if(!response.body)throw Error('生成服务没有返回事件流');
 const state=new PreviewStream(id,onProgress),reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',total=0;
 try{
  while(true){const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>5000000)throw Error('生成流过大');buffer=(buffer+decoder.decode(value,{stream:true})).replace(/\r\n/g,'\n');
   let end;while((end=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);if(frame.length>2000000)throw Error('事件过大');const data=frame.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(data)state.accept(JSON.parse(data));}
  }
  if(buffer.trim())throw Error('事件流尾部不完整');return state.result();
 }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
