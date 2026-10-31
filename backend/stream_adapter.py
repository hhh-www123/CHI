"""AG-UI lifecycle/CUSTOM subset over POST SSE; no event can commit a trip."""
import json
import logging
import queue
import threading
from uuid import uuid4
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
_SLOTS=threading.BoundedSemaphore(4)
_LOG=logging.getLogger("travel.protocol")

def response(session_id, body, build):
    stop=threading.Event();events=queue.Queue(maxsize=64);run_id=uuid4().hex
    def emit(event):
        if stop.is_set(): raise InterruptedError("Preview cancelled")
        events.put_nowait(event)
    def progress(name,value=None): emit({"type":"CUSTOM","name":name,"value":value})
    def worker():
        acquired=_SLOTS.acquire(blocking=False)
        try:
            if not acquired: raise HTTPException(429,"生成并发已满，请稍后重试。")
            batch=build(session_id,body,progress)
            emit({"type":"CUSTOM","name":"candidates.complete","value":batch})
            emit({"type":"RUN_FINISHED","threadId":session_id,"runId":run_id})
        except InterruptedError: pass
        except HTTPException as error:
            logging.getLogger("travel.candidates").info(json.dumps({"event":"generation_error","httpStatus":error.status_code,"runId":run_id}))
            detail=error.detail
            try: emit({"type":"RUN_ERROR","message":detail if isinstance(detail,str) else detail.get("message","生成失败"),"code":str(error.status_code)})
            except InterruptedError: pass
        except Exception:
            try: emit({"type":"RUN_ERROR","message":"生成流未能完成，正式行程未修改，请重新生成。","code":"502"})
            except InterruptedError: pass
        finally:
            if acquired:_SLOTS.release()
    def stream():
        try:
            yield "data: "+json.dumps({"type":"RUN_STARTED","threadId":session_id,"runId":run_id})+"\n\n"
            thread=threading.Thread(target=worker,daemon=True);thread.start()
            while True:
                try:event=events.get(timeout=1)
                except queue.Empty:
                    yield ": keepalive\n\n";continue
                yield "data: "+json.dumps(event,ensure_ascii=False,allow_nan=False)+"\n\n"
                if event["type"] in {"RUN_FINISHED","RUN_ERROR"}:break
        finally:stop.set()
    return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})
