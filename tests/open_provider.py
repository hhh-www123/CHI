"""Local open-UI HTTP fixture. Deliberately deterministic, never a real Agent.

Run separately from an isolated application configured with LLM_BASE_URL
http://127.0.0.1:18081, a dummy key, and AMAP_KEY empty.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


SEARCH_HTML = """<section class="custom-panel"><h2>真实景点搜索与准备</h2>
<label for="search-places">搜索真实景点</label><input id="search-places" placeholder="输入景点名称">
<p id="place-count" aria-live="polite"></p><ul id="place-results"></ul>
<label class="packing"><input type="checkbox" id="umbrella">已准备雨伞</label>
<p id="packing-progress"></p></section>"""
SEARCH_JS = """const search=document.getElementById('search-places');
const results=document.getElementById('place-results');
const umbrella=document.getElementById('umbrella');
function render(){
 const query=search.value.trim();const places=window.travel.places.filter(place=>place.name.includes(query));
 results.replaceChildren(...places.map(place=>{const row=document.createElement('li');row.textContent=place.name;row.dataset.placeId=place.id;return row;}));
 document.getElementById('place-count').textContent='找到 '+places.length+' 个真实地点';
 umbrella.checked=!!window.travel.widgetState.umbrella;
 document.getElementById('packing-progress').textContent=umbrella.checked?'准备进度：1/1':'准备进度：0/1';
}
search.addEventListener('input',render);
umbrella.addEventListener('change',()=>window.travel.saveState({...window.travel.widgetState,umbrella:umbrella.checked}));
window.travel.onChange(render);"""
ACCORDION_JS = """window.travel.onChange(()=>{
 const target=document.getElementById('accordion');target.replaceChildren();
 for(const stop of window.travel.day.stops){
  const place=window.travel.places.find(item=>item.id===stop.placeId);
  const details=document.createElement('details');const summary=document.createElement('summary');
  summary.textContent=stop.startTime+' · '+place.name;const body=document.createElement('p');
  body.textContent=place.description;details.append(summary,body);target.append(details);
 }
});"""


def surface_for(context, index=0):
    current = context['current']['uiSpec']['regions']['day-2'].get('surface')
    instruction = context['instruction']
    if '折叠面板' in instruction:
        return {'title': '可折叠景点行程', 'html': '<section><h2>点击展开景点详情</h2><div id="accordion"></div></section>',
                'css': 'details{padding:14px;border:1px solid #cbd9cf;border-radius:12px;margin:9px 0}summary{cursor:pointer;font-weight:700}',
                'javascript': ACCORDION_JS, 'placement': 'replaceStops'}
    if '蓝色' in instruction and current:
        result = deepcopy(current)
        result['title'] = '蓝色搜索与准备面板'
        result['css'] += '\n.custom-panel{--accent:#2563eb;border-color:#93c5fd;background:#eff6ff}'
        return result
    colors = ['#307854', '#a85f23', '#7351a3']
    result = {'title': ['绿色搜索面板', '暖色搜索面板', '紫色搜索面板'][index], 'html': SEARCH_HTML,
              'css': '.custom-panel{--accent:'+colors[index]+';padding:18px;border:1px solid var(--accent);border-radius:14px}h2{font-size:18px;color:var(--accent)}input:not([type=checkbox]){display:block;width:100%;padding:9px;border:1px solid #aabfb0;border-radius:8px}ul{max-height:180px;overflow:auto;padding-left:20px}.packing{display:flex;gap:8px;align-items:center}',
              'javascript': SEARCH_JS, 'placement': 'afterStops'}
    if '模拟运行错误' in instruction and not context.get('validationFeedback'):
        result['javascript'] = "throw new Error('fixture controlled runtime failure');"
    return result


def reply_for(context):
    instruction = context['instruction']
    trip_only = '总预算改为3000' in instruction
    replacing = '折叠面板' in instruction
    if context.get('phase') == 'scope':
        return {'message': '只调整本次明确要求的内容',
                'permissions': [] if trip_only else [{'regionId': 'day-2', 'editSurface': True, 'replacePresentation': replacing}],
                'tripFields': ['budgetLimit'] if trip_only else [], 'clarification': None, 'unsupportedCapabilities': []}
    if context.get('phase') != 'candidates':
        raise ValueError('open fixture only supports scope/candidates phases')
    variants = []
    for index in range(1 if trip_only or replacing or '蓝色' in instruction or '模拟运行错误' in instruction else 3):
        surface = None if trip_only else surface_for(context, index)
        variants.append({'title': '仅调整总预算' if trip_only else surface['title'],
                         'rationale': '只改变预算' if trip_only else '保持真实行程数据，仅生成请求的交互界面',
                         'message': '已生成本次范围内的修改', 'scope': 'selected',
                         'operations': [] if trip_only else [{'kind': 'set_surface', 'regionId': 'day-2', 'surface': surface}],
                         'tripChanges': {'budgetLimit': 3000} if trip_only else None,
                         'clarification': None, 'unsupportedCapabilities': []})
    return {'message': '开放式界面测试候选', 'candidates': variants}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            context = json.loads(body['messages'][1]['content'])
            content = json.dumps(reply_for(context), ensure_ascii=False)
            response = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]}
            encoded = json.dumps(response, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
        except (ValueError, KeyError, TypeError):
            encoded = b'{"error":"invalid local fixture request"}'
            self.send_response(400)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 18081), Handler).serve_forever()
