"""Deterministic, bounded proxy metrics AFTER all hard checks. Never model scores."""
import json
from pathlib import Path
from .patch_guard import decode_pointer
WEIGHTS = json.loads(Path(__file__).with_name("ranking_weights.json").read_text())

def evaluate(checked, patch, instruction):
    before, after = checked["document"], checked["draft"]
    paths = checked["changedPaths"]
    fields = {op["path"].rsplit("/",1)[-1] for op in patch if op["path"] in paths}
    ids = checked["effectiveScope"]["eligibleActivityIds"]
    old, new = before["entities"]["activities"], after["entities"]["activities"]
    def minute(s): return int(s[:2])*60+int(s[3:])
    time_delta = max([abs(minute(new[i]["startTime"])-minute(old[i]["startTime"])) for i in ids] or [0])
    relaxation = any(t in instruction.lower() for t in ("轻松","休息","慢","relax","rest"))
    replacement = any(t in instruction for t in ("景点","室内","雨天"))
    explicit_time = ":" in instruction
    matched = (replacement and "placeId" in fields) or (explicit_time and "startTime" in fields) or (relaxation and ("description" in fields or "startTime" in fields))
    semantic = 0.8 if matched else 0.4
    # A reproducible preference heuristic, not a measured usability study.
    rest = any(any(t in new[i]["description"] for t in ("休息","慢游")) for i in ids if "description" in fields)
    usability = min(1.0, 0.5 + (0.25 if rest else 0) + (0.15 if 10 <= time_delta <= 75 else 0))
    cost = min(1.0, len([p for p in paths if p not in checked["derivedChangedPaths"]])/max(1,len(checked["effectiveScope"]["allowedPaths"])))
    metrics = {"semanticFit":semantic,"functionalFit":1.0,"stability":1.0,"usability":usability,"changeEconomy":1-cost}
    return {"method":"rules-v1", "metrics":metrics,"changeCost":cost,"score":round(sum(metrics[k]*WEIGHTS[k] for k in WEIGHTS),4),"weights":WEIGHTS,
            "evidence":["Schema、锁、范围、时间不变量及实际Diff均通过，功能保真此处仅指这些结构检查。",
                        "语义匹配为关键词与实际字段的规则代理，无法证明自然语言目标完整满足。",
                        f"直接改动比例 {cost:.3f}；最大时间调整 {time_delta} 分钟；休息描述匹配 {rest}。",
                        "可用性为显式启发式，未进行用户实验或真实路线优化。"]}

def diversity(left, right):
    a={p["path"] for p in left["patch"]};b={p["path"] for p in right["patch"]}
    return {"pathJaccard":len(a&b)/max(1,len(a|b)),"sameFinalState":left["document"]==right["document"]}
