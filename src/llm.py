# -*- coding: utf-8 -*-
"""表达层：MaaS（ModelArts Studio）大模型只做"把规则结论讲成人话"。

架构约束（这是本项目的核心主张，不是限制）
------------------------------------------
大模型不参与判断。它拿到的是一份**已经算好的事实清单**（谁漏了几项、金额多少、
哪条对账不平），只允许做三件事：概括、排序、把结论转成可以问出口的问题。
因此：

1. 提示词里写明"不得新增数字/结论/价格判断"；
2. 输出后做**接地校验（grounding check）**：出现事实清单里没有的数字 → 重试一次，
   仍失败则退回确定性模板。**宁可退回模板，也不让模型自由发挥。**
3. 调用失败、超时、未配置 Key → 全部退回模板，并在页面标注来源。
   页面永远显示"这段解读是谁生成的"，不把模板伪装成 AI。

配置（全部走环境变量，Key 不落盘、不进仓库）：
    MAAS_API_KEY=...
    MAAS_BASE_URL=https://api.modelarts-maas.com/v1   # 以控制台为准
    MAAS_TEXT_MODEL=DeepSeek-V3                        # 以控制台实际列表为准
    LLM_ENABLED=0                                      # 关闭调用，只用模板
"""
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.request

try:  # 既支持 `from src import llm`，也支持测试里把 src 加进 sys.path 后 `import llm`
    from . import privacy
except ImportError:  # pragma: no cover
    import privacy

SYSTEM_PROMPT = """你是「装明白」审计报告的表达层。你只负责把已经算好的审计事实讲清楚。

铁律：
1. 只能使用用户提供的事实清单中的数字与结论；不得新增任何数字、金额、百分比或工序名。
2. 不得判断"贵不贵""值不值""有没有虚高"；不得指控报价方欺诈或造假。
3. 不得给出法律意见、施工验收结论或"保证省钱"的承诺。
4. 事实清单之外的推测一律不写；不确定就写"需人工确认"。
5. 输出必须是合法 JSON，不要 markdown 代码块，不要额外解释。

输出结构：
{"headline": "一句话结论（≤60字）",
 "bullets": ["三条以内的要点，每条≤80字"],
 "questions": ["可以直接发给报价方的问句，最多3条，每条≤60字"],
 "caveat": "一句边界说明（≤50字）"}
"""


def _fmt(x):
    if x is None:
        return "—"
    try:
        return f"{float(x):,.0f}"
    except Exception:  # noqa: BLE001
        return str(x)


def build_facts(quotes, res):
    """把审计结果压成只含事实的上下文。所有数字都来自引擎，模型不得新增。"""
    docs = []
    for q in quotes:
        r = res.get(q.get("label")) or {}
        docs.append({
            "公司": q.get("label"),
            "报价总价": round(float(q.get("total") or 0), 2),
            "漏项": [
                {"工序": m.get("name") or m.get("concept"), "参照金额": m.get("ref_amount"),
                 "档位": m.get("tier")}
                for m in (r.get("missing") or [])
            ],
            "重复计价": [
                {"工序": d.get("name") or d.get("concept"), "金额": d.get("amount"),
                 "档位": d.get("tier")}
                for d in (r.get("duplicates") or [])
            ],
            "漏项补项": r.get("missing_cost", 0),
            "重复剔除": r.get("duplicate_cut", 0),
            "真实成本": r.get("effective", 0),
            "对账": (r.get("recon") or {}).get("grand") and {
                "印刷合计": (r.get("recon") or {}).get("grand", {}).get("amount"),
                "差额": (r.get("recon") or {}).get("diff"),
            },
            "口径风险数": len(r.get("risks") or []),
        })
    return {"documents": docs,
            "跨单口径": res.get("_cross_unit") or [],
            "口径声明": "漏项补项是补齐要花的钱，不是省下的钱；低置信度结论不进金额。"}


def _iter_numbers(obj):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        yield obj
    elif isinstance(obj, str):
        for m in re.findall(r"\d[\d,]*(?:\.\d+)?", obj):
            try:
                yield float(m.replace(",", ""))
            except Exception:  # noqa: BLE001
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_numbers(v)


def allowed_numbers(facts):
    vals = set()
    for v in _iter_numbers(facts):
        vals.add(round(float(v), 2))
        vals.add(round(float(v), 0))
    # 条数、档位数这类小整数是叙述必需的
    for i in range(0, 21):
        vals.add(float(i))
    vals.add(2025.0)
    vals.add(2026.0)
    vals.add(2027.0)
    return vals


def numbers_in(text):
    out = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except Exception:  # noqa: BLE001
            pass
    return out


def grounding_violations(text, allowed):
    bad = []
    for n in numbers_in(text):
        ok = round(n, 2) in allowed or round(n, 0) in allowed
        if not ok:
            bad.append(n)
    return sorted(set(bad))


def _top_missing(doc, k=3):
    items = [m for m in doc.get("漏项") or [] if m.get("参照金额")]
    items.sort(key=lambda m: -(m.get("参照金额") or 0))
    return items[:k]


def template_explanation(facts, reason="no_api_key"):
    """确定性模板：没有 Key / 调用失败 / 接地失败时的兜底。绝不编造。"""
    docs = facts.get("documents") or []
    if not docs:
        return {"source": "template", "reason": reason, "headline": "没有可解读的审计结果。",
                "bullets": [], "questions": [], "caveat": "以上不含价格判断。"}
    by_missing = sorted(docs, key=lambda d: -(d.get("漏项补项") or 0))
    by_dup = sorted(docs, key=lambda d: -(d.get("重复剔除") or 0))
    top = by_missing[0]
    headline = "%s 的漏项补项最高：%s 元；补漏后真实成本 %s 元。" % (
        top.get("公司"), _fmt(top.get("漏项补项")), _fmt(top.get("真实成本")))
    bullets = []
    tm = _top_missing(top)
    if tm:
        bullets.append("优先确认：" + "、".join(
            "%s（%s 元）" % (m["工序"], _fmt(m["参照金额"])) for m in tm) + "。")
    # 跨单口径：事实清单里本来就有，但模板以前完全没用——于是页面上的解读
    # 永远不提「这家的踢脚线按根、另外两家按米」，而这恰恰是最该问出口的一句。
    cross = facts.get("跨单口径") or []
    if cross:
        x = cross[0]
        who = "、".join("%s：%s" % (k, "、".join(v))
                        for k, v in (x.get("by_quote") or {}).items())
        bullets.append("计量口径不一致：%s —— %s，单价不可直接比。" % (x.get("concept"), who))
    if (by_dup[0].get("重复剔除") or 0) > 0:
        bullets.append("%s 有可核减的重复计价 %s 元。" % (
            by_dup[0].get("公司"), _fmt(by_dup[0].get("重复剔除"))))
    risky = [d for d in docs if (d.get("口径风险数") or 0) > 0]
    if risky:
        bullets.append("口径风险集中在：%s；签约前逐条书面确认。" % "、".join(
            d.get("公司") for d in risky[:3]))
    questions = ["「%s」这一项是否包含在报价内？如未包含，请补充单价与做法。" % m["工序"]
                 for m in tm[:3]]
    if cross:
        # 跨单那句更稀缺（漏项那句按模板批量生成，谁都会问），排不进前三条就等于没有。
        # 单份场景 cross 为空，仍是原来那三条，行为不变。
        q = "「%s」能否统一按一种计量方式报价？如不能，请注明折算关系。" % cross[0].get("concept")
        questions = (questions[:2] + [q]) if len(questions) >= 3 else (questions + [q])
    return {"source": "template", "reason": reason, "headline": headline,
            "bullets": bullets[:3], "questions": questions[:3],
            "caveat": "以上是规则结论的转写，不构成价格鉴定或法律意见。"}


def render_html(ai):
    """把解读渲染为报告中的一块。必须显式标注来源。"""
    if not ai:
        return ""
    src = ai.get("source")
    if src == "maas":
        badge = "MaaS 生成 · 已接地校验"
        note = "模型只做转写；数字全部来自规则结果，已逐项校验。"
    else:
        badge = "规则模板生成"
        why = {
            "no_api_key": "未配置 MaaS API Key",
            "disabled": "LLM_ENABLED=0",
            "http_error": "MaaS 调用失败",
            "bad_json": "模型返回非 JSON",
            "grounding_failed": "模型输出了事实之外的信息，已拒收",
            "timeout": "MaaS 超时",
        }.get(ai.get("reason"), "未启用模型")
        note = "%s；以下由确定性模板生成，不含模型自由发挥。" % why
    style = ("<style>.ai-note{background:#fff;border:1px solid #E4E2DC;"
             "border-left:4px solid #002FA7;padding:22px 26px;margin:26px auto;"
             "max-width:1040px;font-family:'PingFang SC','Noto Sans SC',system-ui,sans-serif}"
             ".ai-note h2{margin:0 0 8px;font-size:20px;color:#0A0A0A}"
             ".ai-note .badge{display:inline-block;font-size:12px;color:#002FA7;"
             "border:1px solid #002FA7;border-radius:2px;padding:2px 8px;margin-left:8px;"
             "vertical-align:middle;font-weight:400}"
             ".ai-note ul,.ai-note ol{color:#56606C;font-size:15px;line-height:1.9;"
             "padding-left:20px;margin:6px 0}"
             ".ai-note .note{font-size:12px;color:#8A877F;line-height:1.7}</style>")
    parts = [style,
             '<div class="ai-note"><h2>解读<span class="badge">%s</span></h2>'
             % html.escape(badge),
             '<p style="font-size:18px;line-height:1.6;margin:8px 0 14px"><b>%s</b></p>'
             % html.escape(ai.get("headline") or "")]
    bullets = ai.get("bullets") or []
    if bullets:
        parts.append("<ul>" + "".join(
            "<li>%s</li>" % html.escape(str(b)) for b in bullets[:4]) + "</ul>")
    qs = ai.get("questions") or []
    if qs:
        parts.append('<p style="font-size:14px;color:#56606C;margin:12px 0 4px">'
                     "可以直接问出口：</p><ol>" + "".join(
                         "<li>%s</li>" % html.escape(str(q)) for q in qs[:4]) + "</ol>")
    parts.append('<p class="note" style="margin-top:12px">%s %s</p></div>' % (
        html.escape(ai.get("caveat") or ""), html.escape(note)))
    return "".join(parts)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, api_key=None, base_url=None, model=None, enabled=None,
                 transport=None, timeout=45):
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("MAAS_API_KEY", "")).strip()
        self.base_url = (base_url or os.environ.get("MAAS_BASE_URL")
                         or "https://api.modelarts-maas.com/v1").rstrip("/")
        self.model = (model or os.environ.get("MAAS_TEXT_MODEL")
                      or os.environ.get("MAAS_MODEL") or "DeepSeek-V3")
        if enabled is None:
            enabled = os.environ.get("LLM_ENABLED", "1") != "0"
        self.enabled = bool(enabled)
        self.transport = transport
        self.timeout = timeout

    def available(self):
        return bool(self.enabled and self.api_key)

    def _post(self, payload):
        if self.transport:
            return self.transport(self.base_url + "/chat/completions",
                                  {"Content-Type": "application/json",
                                   "Authorization": "Bearer " + self.api_key},
                                  payload, self.timeout)
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError("HTTP %d: %s" % (e.code, e.read()[:300].decode(errors="ignore")))
        except Exception as e:  # noqa: BLE001
            raise LLMError(str(e))

    def chat(self, messages, temperature=0, max_tokens=700):
        body = self._post({"model": self.model, "temperature": temperature,
                           "max_tokens": max_tokens, "messages": messages})
        try:
            return body["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            raise LLMError("返回结构异常：%r" % (str(body)[:200],))

    def explain(self, facts, cache=None, cache_key=None):
        """返回 dict；任何异常都转成模板结果，调用方永远拿得到可展示内容。"""
        if not self.available():
            return template_explanation(facts, "disabled" if not self.enabled else "no_api_key")
        key = cache_key or hashlib.sha256(
            json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        if cache is not None:
            hit = cache.get_json("ai:%s" % key)
            if hit:
                hit["cached"] = True
                return hit
        allowed = allowed_numbers(facts)
        payload_facts = privacy.mask_pii(json.dumps(facts, ensure_ascii=False, default=str))[0]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": privacy.wrap_untrusted(
                "事实清单（唯一数字来源）：\n" + payload_facts, "facts")},
        ]
        last_reason = "http_error"
        for attempt in range(2):
            t0 = time.time()
            try:
                if attempt == 1:
                    messages[1]["content"] += (
                        "\n\n上一次输出包含事实清单之外的信息。请重写：只使用清单中的数字，"
                        "宁可不写也不要新增。")
                text = self.chat(messages)
            except LLMError as e:
                last_reason = "timeout" if "timed out" in str(e).lower() else "http_error"
                continue
            text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
            try:
                data = json.loads(text)
            except Exception:  # noqa: BLE001
                last_reason = "bad_json"
                continue
            if not isinstance(data, dict) or not data.get("headline"):
                last_reason = "bad_json"
                continue
            joined = json.dumps(data, ensure_ascii=False)
            bad = grounding_violations(joined, allowed)
            if bad:
                last_reason = "grounding_failed"
                continue
            out = {
                "source": "maas",
                "model": self.model,
                "latency_ms": int((time.time() - t0) * 1000),
                "headline": str(data.get("headline"))[:120],
                "bullets": [str(x)[:160] for x in (data.get("bullets") or [])][:3],
                "questions": [str(x)[:120] for x in (data.get("questions") or [])][:3],
                "caveat": str(data.get("caveat") or "以上是规则结论的转写，不构成价格鉴定。")[:100],
                "grounding": {"checked": True, "violations": []},
            }
            if cache is not None:
                cache.set_json("ai:%s" % key, out, ttl=3600)
            return out
        out = template_explanation(facts, last_reason)
        out["grounding"] = {"checked": True, "fallback": last_reason}
        return out


_shared = None


def get_client():
    global _shared
    if _shared is None:
        _shared = LLMClient()
    return _shared
