# -*- coding: utf-8 -*-
"""感知层：单据图片 → 结构化行项。

设计原则（和判断层一致）：**感知层只负责"看见"，不负责"判断"。**
它输出的每一行都带字段级置信度，交给校对台；它绝不说"这项漏了"。

三个 provider，同一套输出结构，可在不改上层代码的前提下切换：

| provider   | 依赖 | 用途 |
|------------|------|------|
| `file`     | 无（PDF 需可选装 pypdf） | **优先**：CSV / XLSX / PDF 文本层，零成本、确定性最好 |
| `maas`     | ModelArts Studio API Key（`MAAS_API_KEY`） | 照片首选：视觉大模型直接读表，能处理手写/合并单元格 |
| `ocr_table`| 华为云 AK/SK + 开通「表格识别」服务 | 纯版式识别，确定性好、无模型费用，但不懂语义 |
| `mock`     | 无 | 单测与 CI。**明确标记为 fixture，不允许出现在演示路径里** |

**为什么 `file` 排第一**：装企给业主的正式报价多是 PDF 或 Excel，不是照片。
这类文件根本不需要 OCR——把 AI 用在能用规则解决的地方，本身就是错的。
照片才需要 `maas` / `ocr_table`。

已实测到的边界（2026-10-30）：
- AK/SK 手工签名（SDK-HMAC-SHA256）可用，但**规范化 URI 必须带尾斜杠**，否则 401；
- `POST /v2/{project_id}/ocr/general-table/` 签名通过，返回
  `ModelArts.4204 The API ... is not subscribed` —— 即**服务未开通**，不是代码问题；
- `api.modelarts-maas.com` 可达（无 Key 时 400），需要 API Key。

用法：
    python3 scripts/parse_doc.py 图片.jpg --provider mock --out out.csv
    MAAS_API_KEY=xxx python3 scripts/parse_doc.py 图片.jpg --out out.csv
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

# 标准列。注意：真实单据列名千差万别，靠 HEADER_ALIASES 映射，不靠位置
COLUMNS = ["序号", "项目名称", "规格工艺", "单位", "数量", "单价", "合价", "备注"]

HEADER_ALIASES = {
    "序号": ["序号", "编号", "no", "no.", "行号"],
    "项目名称": ["项目名称", "项目", "名称", "施工项目", "工程名称", "品名", "材料名称", "工程项目"],
    "规格工艺": ["规格", "规格工艺", "工艺", "材料结构及工艺标准", "说明", "型号", "品牌规格"],
    "单位": ["单位", "计量单位", "unit"],
    "数量": ["数量", "工程量", "qty", "数量/工程量"],
    "单价": ["单价", "综合单价", "price", "单价(元)", "单价（元）"],
    "合价": ["合价", "小计", "金额", "总价", "合计", "amount", "合价(元)", "合价（元）"],
    "备注": ["备注", "说明", "备注说明", "施工说明", "工艺说明"],
}


class PerceptionError(RuntimeError):
    """感知层失败。必须显式抛出，不允许返回空结果让上层误判为"没漏项"。"""


# ---------------------------------------------------------------- 华为云签名

def _sign(ak, sk, method, host, uri, body=b""):
    """SDK-HMAC-SHA256。uri 必须与服务端规范化后的一致（含尾斜杠）。"""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = "content-type:application/json\nhost:%s\nx-sdk-date:%s\n" % (host, now)
    signed = "content-type;host;x-sdk-date"
    canonical = "\n".join([method, uri, "", canonical_headers, signed, payload_hash])
    sts = "\n".join(["SDK-HMAC-SHA256", now, hashlib.sha256(canonical.encode()).hexdigest()])
    sig = hmac.new(sk.encode(), sts.encode(), hashlib.sha256).hexdigest()
    return {
        "Host": host, "X-Sdk-Date": now, "Content-Type": "application/json",
        "Authorization": "SDK-HMAC-SHA256 Access=%s, SignedHeaders=%s, Signature=%s"
                         % (ak, signed, sig),
    }


def _project_id(ak, sk, region):
    """取 project_id。顺序：环境变量 → KooCLI → 报错并给出可执行指令。

    直接用 IAM 的 hw_ak_sk 方式换 token 在本账号上返回 400（请求体不被接受），
    而 KooCLI 自己的鉴权是可用的，所以这里优先复用 KooCLI 的结果——
    不重复实现一套已经验证过的东西。
    """
    cached = os.environ.get("HW_PROJECT_ID")
    if cached:
        return cached
    import shutil
    import subprocess
    hcloud = shutil.which("hcloud") or os.path.expanduser("~/.local/hcloud/hcloud")
    if os.path.exists(hcloud):
        try:
            out = subprocess.run([hcloud, "IAM", "KeystoneListProjects",
                                  "--cli-region=" + region],
                                 capture_output=True, text=True, timeout=30).stdout
            m = re.search(r"\{.*\}", out, re.S)
            if m:
                for p in json.loads(m.group(0)).get("projects", []):
                    if p.get("name") == region:
                        return p["id"]
        except Exception:
            pass
    raise PerceptionError(
        "无法取得 project_id。请先执行 `hcloud configure set --cli-mode=AKSK ...`，"
        "或直接 export HW_PROJECT_ID=<你的项目ID>（控制台「我的凭证 → 项目列表」可查）。")


# ---------------------------------------------------------------- provider

def _maas_parse(image_bytes, mime, model, base_url, api_key, prompt=None):
    """ModelArts Studio，OpenAI 兼容协议 + 视觉输入。"""
    if not api_key:
        raise PerceptionError(
            "缺少 MAAS_API_KEY。请到 ModelArts Studio 领取模型免费额度并创建 API Key，"
            "然后 export MAAS_API_KEY=... 再重试（详见 感知层接入说明.md）")
    b64 = base64.b64encode(image_bytes).decode()
    default_prompt = (
        "你是一个报价单结构化工具。只做识别，不做判断，不要总结，不要评价价格。\n"
        "把图中报价表的每一行输出为 JSON，严格使用这个结构：\n"
        '{"rows":[{"no":"","name":"","spec":"","unit":"","qty":0,"price":0,"amount":0,"note":""}],'
        '"header_conf":0.0,"notes":""}\n'
        "规则：\n"
        "1. 数字只填数字，不带单位、不带千分位；缺失填 null；\n"
        "2. 分区标题行（如「客厅」「水电」）填进 spec 字段并在 name 写该分区名；\n"
        "3. 不要合并、不要补全你没有看到的内容；看不清的字符用「?」；\n"
        "4. header_conf 是你对表头识别正确的把握程度（0-1）。\n"
        "只输出 JSON，不要 markdown 代码块。")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt or default_prompt},
            {"type": "image_url",
             "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
        ]}],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    try:
        resp = urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as e:
        raise PerceptionError("MaaS 调用失败 HTTP %d：%s"
                              % (e.code, e.read()[:300].decode(errors="ignore")))
    body = json.loads(resp.read())
    text = body["choices"][0]["message"]["content"]
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise PerceptionError("模型返回的不是合法 JSON，前 200 字：%r" % text[:200])
    return data.get("rows") or [], {"provider": "maas", "model": model,
                                    "header_conf": data.get("header_conf"),
                                    "usage": body.get("usage")}


def _ocr_table_parse(image_bytes, ak, sk, region):
    """华为云「表格识别」。用 return_excel 拿 xlsx，再用标准库解，避免依赖未知 JSON 形状。"""
    if not (ak and sk):
        raise PerceptionError("缺少 HW_ACCESS_KEY / HW_SECRET_KEY")
    pid = _project_id(ak, sk, region)
    if not pid:
        raise PerceptionError("无法取得 project_id")
    host = "ocr.%s.myhuaweicloud.com" % region
    path = "/v2/%s/ocr/general-table" % pid
    # 实测（2026-10-30）：**请求 URL 不能带尾斜杠，但签名里的 URI 必须带**。
    # APIG 会先把请求路径规范化成带斜杠再去比对签名——
    # URL 带斜杠 → 404 API 不存在；签名不带斜杠 → 401 签名失败。两者都试过。
    body = json.dumps({"image": base64.b64encode(image_bytes).decode(),
                       "return_excel": True, "return_confidence": True}).encode()
    req = urllib.request.Request("https://" + host + path, data=body, method="POST",
                                 headers=_sign(ak, sk, "POST", host, path + "/", body))
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        raw = e.read()[:400].decode(errors="ignore")
        if "not subscribed" in raw or "4204" in raw:
            raise PerceptionError(
                "华为云「表格识别」服务未开通（ModelArts.4204）。"
                "到控制台 → 文字识别 OCR → 开通「表格识别」，有免费额度，开通后无需改代码。"
                "原始返回：" + raw[:200])
        raise PerceptionError("OCR 调用失败 HTTP %d：%s" % (e.code, raw))

    data = json.loads(resp.read())
    result = data.get("result") or {}
    excel_b64 = result.get("excel")
    if excel_b64:
        rows = _xlsx_to_rows(base64.b64decode(excel_b64))
        return rows, {"provider": "ocr_table", "via": "excel",
                      "table_count": result.get("table_count")}
    rows = _ocr_tables_to_rows(result.get("tables") or [])
    if not rows:
        raise PerceptionError("OCR 返回成功但没有解析出表格内容")
    return rows, {"provider": "ocr_table", "via": "tables"}


def _xlsx_to_rows(blob):
    """标准库解析 xlsx（sharedStrings + sheet1），不引入 openpyxl。"""
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
        for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
            shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "ignore")
    out = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = {}
        for c in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
            attrs, inner = c
            ref = re.search(r'r="([A-Z]+)(\d+)"', attrs)
            if not ref:
                continue
            col = ref.group(1)
            v = re.search(r"<v>(.*?)</v>", inner, re.S)
            txt = v.group(1) if v else "".join(re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S))
            if 't="s"' in attrs and txt.isdigit() and int(txt) < len(shared):
                txt = shared[int(txt)]
            cells[col] = _unescape(txt)
        if cells:
            out.append(cells)
    return out


def _ocr_tables_to_rows(tables):
    """兜底：不同版本返回的 tables 结构不一致，能取多少取多少。"""
    rows = []
    for t in tables:
        for r in (t.get("rows") or []):
            if isinstance(r, list):
                cells = {}
                for i, c in enumerate(r):
                    txt = c.get("text") if isinstance(c, dict) else str(c)
                    cells[_col_letter(i)] = txt or ""
                rows.append(cells)
            elif isinstance(r, str):
                rows.append({"A": r})
    return rows


def _col_letter(i):
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def _unescape(s):
    return (s.replace("&amp;", "&").replace("&lt;", "<")
             .replace("&gt;", ">").replace("&quot;", '"').strip())


def _mock_parse(image_bytes, mime):
    """单测/CI 用。返回固定的两行，明确标注为 fixture。"""
    return ([{"no": "1", "name": "滚涂立邦乳胶漆（顶面）", "spec": "分区：客厅",
              "unit": "m2", "qty": 110, "price": 15.6, "amount": 1716, "note": "面漆两遍"},
             {"no": "2", "name": "粘贴地砖", "spec": "分区：客厅", "unit": "m2",
              "qty": 31, "price": 140.4, "amount": 4352.4, "note": ""}],
            {"provider": "mock", "fixture": True,
             "warning": "这是固定样例，不是对输入图片的识别结果"})


# ------------------------------------------------- 结构化文件（无需任何 AI 服务）

def _csv_to_rows(path):
    """CSV/TSV → 网格。表头行原样保留，由 rows_to_quote 统一映射。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
        grid = []
        for row in csv.reader(f, delimiter=delim):
            if not any((c or "").strip() for c in row):
                continue
            grid.append({_col_letter(i): (c or "").strip() for i, c in enumerate(row)})
    return grid


def _pdf_to_rows(path):
    """PDF 文本层 → 网格。按表头各列字符位置切片，比按空白分割稳。

    扫描件/图片合成的 PDF 没有文本层——必须明确报错让人换图片路径，
    不能返回空表（空表会被上层当成"没有漏项"）。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PerceptionError(
            "解析 PDF 需要 pypdf：pip install pypdf。"
            "注意这是**可选依赖**，只有 PDF 输入才需要，引擎本身仍然零依赖。")
    reader = PdfReader(path)
    lines = []
    for page in reader.pages:
        try:
            txt = page.extract_text(extraction_mode="layout")
        except TypeError:                       # 老版本 pypdf 不支持 layout
            txt = page.extract_text()
        if txt:
            lines.extend(txt.splitlines())
    if not any(l.strip() for l in lines):
        raise PerceptionError(
            "这个 PDF 没有文本层（扫描件或图片合成），无法直接取字。"
            "请改用图片路径：--provider maas 或 --provider ocr_table。")
    return _lines_to_grid(lines)


def _header_positions(line):
    """在表头行里定位各标准列的起始字符位置。取最长别名，避免「单价」被「单价(元)」干扰。"""
    found = []
    for std, aliases in HEADER_ALIASES.items():
        best = None
        for a in sorted(aliases, key=len, reverse=True):
            pos = line.find(a)
            if pos >= 0:
                best = pos
                break
        if best is not None:
            found.append((best, std))
    return {std: pos for pos, std in found}


def _lines_to_grid(lines):
    header_idx, cols = None, {}
    for i, line in enumerate(lines[:120]):
        pos = _header_positions(line)
        if "项目名称" in pos and len(pos) >= 3:
            header_idx, cols = i, pos
            break
    if header_idx is None:
        raise PerceptionError("PDF 里没找到可识别的表头（需要 项目名称/单位/数量/合价 等列）")

    ordered = sorted(cols.items(), key=lambda kv: kv[1])      # [(std, pos)] 按位置
    grid = [{_col_letter(j): std for j, (std, _) in enumerate(ordered)}]
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        row = {}
        for j, (_, start) in enumerate(ordered):
            end = ordered[j + 1][1] if j + 1 < len(ordered) else len(line)
            row[_col_letter(j)] = line[start:end].strip()
        if (row.get("A") or "").strip():       # 首列空行多为续行/页脚，跳过
            grid.append(row)
    return grid


def _file_parse(path):
    """按扩展名分派。这条路不需要任何云服务，是覆盖率最高的一种。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        grid, kind = _csv_to_rows(path), "csv"
    elif ext in (".xlsx", ".xlsm"):
        with open(path, "rb") as f:
            grid, kind = _xlsx_to_rows(f.read()), "xlsx"
    elif ext == ".pdf":
        grid, kind = _pdf_to_rows(path), "pdf"
    elif ext in (".xls",):
        raise PerceptionError("老式 .xls 是二进制格式，请在 Excel 里另存为 .xlsx 或 .csv 再上传。")
    else:
        raise PerceptionError("不支持的文件类型：%s（支持 csv/tsv/xlsx/pdf，图片请用 maas 或 ocr_table）"
                              % (ext or "无扩展名"))
    if not grid:
        raise PerceptionError("文件里没有可解析的内容：%s" % path)
    return grid, {"provider": "file", "kind": kind, "needs_ai": False}


# ---------------------------------------------------------------- 归一化

def map_header(cells):
    """一行单元格 → {标准列: 列字母}。识别不出的列不猜。"""
    mapping = {}
    for col, raw in cells.items():
        key = re.sub(r"\s|\(.*?\)|（.*?）", "", str(raw or "")).lower()
        for std, aliases in HEADER_ALIASES.items():
            if std in mapping:
                continue
            if any(key == a.lower() or (a.lower() in key and len(key) <= len(a) + 4)
                   for a in aliases):
                mapping[std] = col
                break
    return mapping


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("，", "").replace("元", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def rows_to_quote(raw_rows, header_row_index=None):
    """把网格行转成标准行项。返回 (rows, report)。

    关键：**逐行做算术自校验**（数量×单价 是否等于合价），
    这是唯一能机器验证"读对了"的手段，误差超容差的行要显式报出来。
    """
    if not raw_rows:
        raise PerceptionError("没有可解析的行")

    header_idx, mapping = None, {}
    for i, cells in enumerate(raw_rows[:8]):        # 表头通常在前 8 行
        m = map_header(cells)
        if len(m) >= 4 and "项目名称" in m:
            header_idx, mapping = i, m
            break
    if header_idx is None:
        raise PerceptionError("前 8 行里没找到可识别的表头（至少需要 项目名称/单位/数量/合价）")

    out, bad_arith, no_name = [], [], 0
    for cells in raw_rows[header_idx + 1:]:
        name = (cells.get(mapping.get("项目名称", ""), "") or "").strip()
        if not name:
            no_name += 1
            continue
        rec = {"序号": (cells.get(mapping.get("序号", ""), "") or "").strip() or None,
               "项目名称": name,
               "规格工艺": (cells.get(mapping.get("规格工艺", ""), "") or "").strip(),
               "单位": (cells.get(mapping.get("单位", ""), "") or "").strip(),
               "数量": _num(cells.get(mapping.get("数量", ""))),
               "单价": _num(cells.get(mapping.get("单价", ""))),
               "合价": _num(cells.get(mapping.get("合价", ""))),
               "备注": (cells.get(mapping.get("备注", ""), "") or "").strip()}
        if rec["数量"] is not None and rec["单价"] is not None and rec["合价"] is not None:
            calc = round(rec["数量"] * rec["单价"], 2)
            if abs(calc - rec["合价"]) > max(1.0, rec["合价"] * 0.01):
                bad_arith.append({"name": name, "qty": rec["数量"], "price": rec["单价"],
                                  "amount": rec["合价"], "calc": calc})
        out.append(rec)

    report = {
        "header_row": header_idx, "mapped_columns": mapping,
        "rows": len(out), "dropped_no_name": no_name,
        "arith_checked": sum(1 for r in out if r["数量"] is not None
                             and r["单价"] is not None and r["合价"] is not None),
        "arith_bad": bad_arith,
        "total": round(sum(r["合价"] for r in out if r["合价"]), 2),
    }
    return out, report


def perceive(image_path, provider=None, **kw):
    """唯一对外入口。返回 (rows, meta, report)。"""
    provider = provider or os.environ.get("PERCEPTION_PROVIDER", "maas")

    # 结构化文件不需要读成字节，也不需要任何云端服务
    if provider == "file":
        grid, meta = _file_parse(image_path)
        rows, report = rows_to_quote(grid)
        return rows, meta, report

    if provider not in ("maas", "ocr_table", "mock"):
        raise PerceptionError("未知 provider：%s（可选 file / maas / ocr_table / mock）" % provider)
    if image_path == "__selftest__" or (provider == "mock" and not os.path.exists(image_path)):
        blob = b"__selftest__"          # 自检/单测不读文件
    else:
        with open(image_path, "rb") as f:
            blob = f.read()
    if not blob:
        raise PerceptionError("图片为空：%s" % image_path)
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

    if provider == "mock":
        raw, meta = _mock_parse(blob, mime)
        if raw and "name" in raw[0]:          # mock 直接给结构化行，跳过表头识别
            return raw, meta, {"provider": "mock", "header_row": None,
                               "rows": len(raw), "arith_bad": [], "total": None}
    elif provider == "maas":
        raw, meta = _maas_parse(
            blob, mime,
            model=kw.get("model") or os.environ.get("MAAS_MODEL", "qwen2.5-vl-72b"),
            base_url=kw.get("base_url") or os.environ.get(
                "MAAS_BASE_URL", "https://api.modelarts-maas.com/v1"),
            api_key=kw.get("api_key") or os.environ.get("MAAS_API_KEY"),
            prompt=kw.get("prompt"))
    elif provider == "ocr_table":
        raw, meta = _ocr_table_parse(
            blob, kw.get("ak") or os.environ.get("HW_ACCESS_KEY"),
            kw.get("sk") or os.environ.get("HW_SECRET_KEY"),
            kw.get("region") or os.environ.get("HW_REGION_NAME", "cn-north-4"))
    else:                                    # mock 已在上面处理，此处不可达
        raise PerceptionError("未知 provider：%s" % provider)

    rows, report = rows_to_quote(raw)
    return rows, meta, report


def write_csv(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in COLUMNS})
