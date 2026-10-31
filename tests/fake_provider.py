"""Deterministic two-phase HTTP fixture. Never use as a real model provider."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REGIONS = ["day-1", "day-2", "day-3"]


def reply_for(context):
    instruction = context["instruction"]
    current = context["current"]
    selected = context["selectedRegionId"]
    named = [f"day-{n}" for n, cn in [(1, "一"), (2, "二"), (3, "三")] if re.search(rf"第(?:{cn}|{n})天", instruction)]
    if "最后一天" in instruction:
        named = ["day-3"]
    regions = REGIONS if "每天" in instruction or (selected == "all" and not named) else named or [selected]
    kind = "routes" if "交通" in instruction or "地图" in instruction else "checklist" if "出门" in instruction or "清单" in instruction else "timeline" if "时间轴" in instruction else "layout" if "清晰" in instruction or "布局" in instruction else "unsupported"
    if context.get("phase") == "scope":
        permissions = []
        for region in regions:
            rule = {"regionId": region}
            if kind == "routes":
                rule.update(uiFields=["showTransport", "showMap", "mapPlacement", "transportStyle"], requiredUI={"showTransport": True, "showMap": True})
            elif kind == "checklist":
                rule["newBlockTypes"] = ["checklist"]
            elif kind == "timeline":
                rule.update(uiFields=["view"], requiredUI={"view": "timeline"})
            elif kind == "layout":
                rule["uiFields"] = ["view", "compact"]
            permissions.append(rule)
        return {"message": "仅调整请求涉及的日期与组件，保留其他内容", "permissions": permissions if kind != "unsupported" else [], "clarification": "请补充需要的具体内容。" if kind == "unsupported" else None, "unsupportedCapabilities": []}

    if context.get("phase") == "candidates":
        variants = []
        if kind == "routes":
            for title, placement, style in [("行程后看地图", "afterStops", "inline"), ("先看地图再出发", "beforeStops", "inline"), ("集中查看交通", "afterStops", "summary")]:
                operations = [{"kind": "set_ui", "regionId": region, "showTransport": True, "showMap": True, "mapPlacement": placement, "transportStyle": style} for region in regions]
                variants.append({"title": title, "rationale": "仅改变本次新增交通与地图的摆放方式", "operations": operations})
        elif kind == "checklist":
            region = regions[0]
            existing = {block["id"] for block in current["uiSpec"]["regions"][region].get("blocks", [])}
            serial = 1
            while f"packing-{serial}" in existing:
                serial += 1
            for title, items in [("简要准备", ["带好雨伞", "检查手机电量"]), ("分类准备", ["雨具：随身携带雨伞", "电子用品：手机与充电宝"]), ("出发前核对", ["确认随身雨具", "检查证件和手机电量"] )]:
                block = {"id": f"packing-{serial}", "type": "checklist", "title": "出门前检查", "text": "一般准备建议，非实时查询。", "items": items, "columns": [], "rows": []}
                variants.append({"title": title, "rationale": "保留原行程，只比较新增清单的组织方式", "operations": [{"kind": "upsert_block", "regionId": region, "block": block}]})
        elif kind == "timeline":
            variants = [{"title": "时间轴", "rationale": "仅按要求切换为时间轴", "operations": [{"kind": "set_ui", "regionId": region, "view": "timeline"} for region in regions]}]
        elif kind == "layout":
            for title, view, compact in [("时间顺序", "timeline", False), ("紧凑卡片", "cards", True), ("紧凑时间轴", "timeline", True)]:
                variants.append({"title": title, "rationale": "仅调整展示结构与信息密度", "operations": [{"kind": "set_ui", "regionId": region, "view": view, "compact": compact} for region in regions]})
        for variant in variants:
            variant.update(message="按本次要求更新指定日期", scope="all_days" if len(regions) == 3 else "selected", clarification=None, unsupportedCapabilities=[])
        return {"message": "已准备范围内的不同候选", "candidates": variants}

    # Retain the old fixture's legacy endpoint contract for isolated compatibility checks.
    operations = [{"kind": "set_ui", "regionId": region, "showTransport": True, "showMap": True} for region in regions] if kind == "routes" else []
    return {"message": "按本次要求更新指定日期", "scope": "all_days" if len(regions) == 3 else "selected", "operations": operations, "clarification": None if operations else "请补充需要的具体内容。", "unsupportedCapabilities": []}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        context = json.loads(body["messages"][1]["content"])
        plan = reply_for(context)
        content = json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)}}]}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
