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


def _pdf_fragments(reader):
    """每页的 [(y, x, text)]。pypdf 的 visitor 逐片段回调，带文本矩阵。"""
    pages = []
    for page in reader.pages:
        frags = []

        def visitor(text, cm, tm, font_dict, font_size, _acc=frags):
            t = (text or "").strip()
            if t:
                _acc.append((round(tm[5], 1), round(tm[4], 1), t))

        page.extract_text(visitor_text=visitor)
        pages.append(frags)
    return pages


# PDF 表头专用匹配串。显式列出，避免「说明」同时命中「规格工艺」和「备注」
PDF_HEADER_KEYS = [
    ("序号", ["序号", "序"]),
    ("项目名称", ["项目名称", "施工项目", "项目", "名称"]),
    ("数量", ["工程量", "数量"]),
    ("单位", ["单位"]),
    ("损耗", ["损耗"]),
    ("单价", ["单价"]),
    ("合价", ["合计", "合价", "金额", "小计"]),
    ("备注", ["施工工艺及材料说明", "施工工艺", "工艺说明", "说明", "备注"]),
]
_NUMERIC_COLS = ("序号", "数量", "单价", "合价", "损耗")
_CURRENCY_ONLY = re.compile(r"^[#￥¥$]+$")
# 计量单位白名单。x 坐标落在「损耗」列附近的单位（如 M）会被就近分配错，
# 单位是强特征，直接按内容判定比按坐标稳。
_UNIT_RE = re.compile(
    r"^(㎡|m²|m2|m3|m³|M|m|cm|mm|米|毫米|项|套|个|根|樘|处|间|台|组|只|张|块|条|座|片|对|副|"
    r"公斤|kg|t|吨|工日|台班|次|盏|扇|延米|间|批|系统)$", re.I)


def _header_anchors(frags):
    """从一页片段里找表头各列的 x。表头可能跨两行（「序」和「号」分开）。"""
    anchors = {}
    for _, x, t in frags:
        for std, keys in PDF_HEADER_KEYS:
            if std in anchors:
                continue
            for k in keys:
                if t == k or t.startswith(k) or (k.startswith(t) and len(k) > len(t)):
                    anchors[std] = x
                    break
    return anchors


def _is_number(s):
    return bool(re.fullmatch(r"-?[\d,]+(?:\.\d+)?", (s or "").strip()))


def _pdf_rows_by_coords(pages):
    """坐标法重建表格：行按 y 聚类，列按最近的表头锚点分配。

    为什么不能用字符位置切片：真实报价单里 `1164.00` 比表头「合计」宽得多，
    按字符位置切会把首字符切掉（实测 1164.00 → 164.00，65 行算术不符）。
    """
    anchors = {}
    for frags in pages:
        anchors = _header_anchors(frags)
        if all(k in anchors for k in ("项目名称", "单价", "合价")):
            break
    if len(anchors) < 4:
        raise PerceptionError("PDF 里没找到可用的表头坐标")

    order = sorted(anchors.items(), key=lambda kv: kv[1])      # [(列名, x)] 从左到右

    def col_of(x):
        return min(order, key=lambda kv: abs(kv[1] - x))[0]

    comment_x = anchors.get("备注", 1e9)

    # 先把每页聚成行（y 容差 2），再决定阅读方向。
    all_rows = []
    for pi, frags in enumerate(pages):
        rows = []
        for y, x, t in sorted(frags, key=lambda f: (-f[0], f[1])):
            if rows and abs(rows[-1][0][0] - y) <= 2.0:
                rows[-1].append((y, x, t))
            else:
                rows.append([(y, x, t)])
        all_rows.append((pi, rows))

    # 方向探测：有的 PDF 用翻转坐标系（y 越大越靠下）。判据是表头行的 y
    # 与数据行 y 中位数的相对位置——表头在视觉顶部，正常情况下 y 最大。
    header_y, data_ys = None, []
    for _, rows in all_rows:
        for row in rows:
            joined = "".join(t for _, _, t in row)
            if header_y is None and "项目名称" in joined:
                header_y = row[0][0]
            for _, _, t in row:
                if _is_number(t) and "." in t:
                    data_ys.append(row[0][0])
                    break
    inverted = bool(header_y is not None and data_ys
                    and header_y < sorted(data_ys)[len(data_ys) // 2])

    recs, cur = [], None
    for pi, rows in all_rows:
        rows = sorted(rows, key=lambda r: r[0][0], reverse=not inverted)

        for row in rows:
            joined = "".join(t for _, _, t in row)
            if any(k in joined for k in ("签字", "附件：", "装饰工程报价单")):
                continue

            cells = {}
            for _, x, t in sorted(row, key=lambda f: f[1]):
                if _CURRENCY_ONLY.match(t):
                    # PDF 字体常把 ￥ 映射成 #，且紧贴单价。它不是数据，丢掉。
                    continue
                # 单位是强特征：宁可信任内容，也不信任坐标
                # （实测「M」落在损耗列与单位列的中点附近，会判错）
                col = "单位" if _UNIT_RE.match(t) else col_of(x)
                # 类型校正：数字列里出现非数字，说明该片段其实属于名称或说明
                if col in _NUMERIC_COLS and not _is_number(t):
                    col = "项目名称" if x < comment_x else "备注"
                cells[col] = (cells.get(col, "") + " " + t).strip() if col in cells else t

            if not cells:
                continue
            name = re.sub(r"\s+", "", cells.get("项目名称", ""))
            has_num = any(cells.get(c) for c in ("数量", "单价", "合价"))
            # 分区标题（「一、候诊大厅」）在 PDF 里常被拆成「一」+「、候诊大厅」两段，
            # 拼接后会带空格，所以先去掉空白再判，且必须没有数字列
            if re.match(r"^[一二三四五六七八九十百]+[、.．]", name) and not has_num:
                recs.append({"__section__": name})
                continue

            if _is_number(cells.get("序号", "")):
                cur = {"序号": cells["序号"], "项目名称": name, "单位": cells.get("单位", ""),
                       "数量": cells.get("数量"), "单价": cells.get("单价"),
                       "合价": cells.get("合价"), "备注": cells.get("备注", "")}
                recs.append(cur)
            elif cur is not None:
                # 续行：名称列续名称，说明列续备注
                if cells.get("项目名称"):
                    cur["项目名称"] = (cur["项目名称"] + cells["项目名称"]).strip()
                if cells.get("备注"):
                    cur["备注"] = (cur["备注"] + " " + cells["备注"]).strip()

    section, grid = "", [{_col_letter(i): c for i, c in enumerate(COLUMNS)}]
    for r in recs:
        if "__section__" in r:
            section = r["__section__"]
            continue
        row = dict(r)
        row["规格工艺"] = ("分区：%s" % section) if section else ""
        grid.append({_col_letter(i): ("" if row.get(c) is None else str(row.get(c)))
                     for i, c in enumerate(COLUMNS)})
    return grid


def _pdf_to_rows(path):
    """PDF 文本层 → 网格。优先用坐标法，失败才降级到按字符位置切片。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PerceptionError(
            "解析 PDF 需要 pypdf：pip install pypdf。"
            "注意这是**可选依赖**，只有 PDF 输入才需要，引擎本身仍然零依赖。")
    reader = PdfReader(path)
    pages = _pdf_fragments(reader)
    if not any(pages):
        raise PerceptionError(
            "这个 PDF 没有文本层（扫描件或图片合成），无法直接取字。"
            "请改用图片路径：--provider maas 或 --provider ocr_table。")
    try:
        return _pdf_rows_by_coords(pages)
    except PerceptionError:
        lines = []
        for pg in reader.pages:
            try:
                txt = pg.extract_text(extraction_mode="layout")
            except TypeError:
                txt = pg.extract_text()
            if txt:
                lines.extend(txt.splitlines())
        if not any(l.strip() for l in lines):
            raise
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


def _next_col(letter):
    """列字母右移一位：A→B，Z→AA。"""
    i = 0
    for ch in letter:
        i = i * 26 + (ord(ch) - 64)
    i += 1
    s = ""
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def _num_ratio(rows, col):
    """某列非空单元格中「能解析为数字」的比例。用于结构兜底的安全阀。"""
    vals = [(r.get(col) or "").strip() for r in rows]
    vals = [v for v in vals if v]
    if not vals:
        return 0.0
    return sum(1 for v in vals if _num(v) is not None) / len(vals)


def detect_header(raw_rows, max_scan=8):
    """在前 max_scan 行里找表头，返回 (index, mapping)；找不到返回 (None, {})。

    真实单据的表头形态差异极大，按优先级依次尝试（每一步都留痕，便于复盘）：
      ① 标准：映射 ≥4 列且含「项目名称」
      ② 两行表头：主行缺「合价」，把下一行（人工+材料/小计 子表头）补进来
      ③ 无「项目名称」列名：取数据行中文占比最高且未映射的列补上
         （反例：124㎡ 单的表头只有 数量/单价/合计/说明，项目名所在的列没有表头文字）
      ④ 结构兜底：合价=单价右列、备注=最右列，用数值占比做安全阀（不猜）
    """
    for i, cells in enumerate(raw_rows[:max_scan]):
        m = map_header(cells)
        if not m:
            continue
        body = raw_rows[i + 1:]
        # ② 两行表头合并
        if "合价" not in m and i + 1 < len(raw_rows):
            for k, v in map_header(raw_rows[i + 1]).items():
                m.setdefault(k, v)
        # ③ 项目名称兜底：中文占比最高的未映射列
        if "项目名称" not in m and ("单价" in m or "合价" in m):
            best, score = None, 0.0
            for c in sorted({c for r in body for c in r if (r.get(c) or "").strip()}):
                if c in m.values():
                    continue
                vals = [(r.get(c) or "").strip() for r in body]
                vals = [v for v in vals if v]
                if len(vals) < 3:
                    continue
                ratio = sum(1 for v in vals if re.search(r"[\u4e00-\u9fa5]", v)) / len(vals)
                if ratio > score:
                    best, score = c, ratio
            if best and score >= 0.5:
                m["项目名称"] = best
        if "项目名称" not in m or len(m) < 4:
            continue
        # ④ 结构兜底
        if "合价" not in m and m.get("单价"):
            cand = _next_col(m["单价"])
            if _num_ratio(body, cand) >= 0.5:
                m["合价"] = cand
        if "备注" not in m:
            cols = sorted({c for r in body for c in r if (r.get(c) or "").strip()})
            if cols and cols[-1] not in m.values() and _num_ratio(body, cols[-1]) < 0.5:
                m["备注"] = cols[-1]
        return i, m
    return None, {}


def rows_to_quote(raw_rows, header_row_index=None):
    """把网格行转成标准行项。返回 (rows, report)。

    关键：**逐行做算术自校验**（数量×单价 是否等于合价），
    这是唯一能机器验证"读对了"的手段，误差超容差的行要显式报出来。
    """
    if not raw_rows:
        raise PerceptionError("没有可解析的行")

    header_idx, mapping = detect_header(raw_rows)
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
                ratio = round(rec["合价"] / calc, 3) if calc else None
                bad_arith.append({"name": name, "qty": rec["数量"], "price": rec["单价"],
                                  "amount": rec["合价"], "calc": calc, "ratio": ratio})
        out.append(rec)

    # 系统性系数：当大量行的「合价 / (数量×单价)」等于同一个常数，
    # 说明单据对某类行乘了一个系数（常见是 10% 损耗或管理费），而表内没有声明。
    # 这不是识别错误，是**该报给业主的口径问题**，所以单独统计而不是混在错误里。
    multipliers = {}
    for b in bad_arith:
        if b.get("ratio"):
            key = b["ratio"]
            multipliers[key] = multipliers.get(key, 0) + 1
    consistent = {r: n for r, n in multipliers.items() if n >= 3}

    report = {
        "header_row": header_idx, "mapped_columns": mapping,
        "rows": len(out), "dropped_no_name": no_name,
        "arith_checked": sum(1 for r in out if r["数量"] is not None
                             and r["单价"] is not None and r["合价"] is not None),
        "arith_bad": bad_arith,
        "multipliers": consistent,
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

    if provider == "vision":
        from . import vision_ocr
        rows, report = vision_ocr.perceive(image_path)
        meta = {"provider": "vision", "engine": "macos-vision",
                "boxes": report.get("boxes"), "rows": len(rows),
                "review_ratio": report.get("review_ratio")}
        return rows, meta, report

    if provider not in ("maas", "ocr_table", "mock", "vision"):
        raise PerceptionError("未知 provider：%s（可选 file / maas / ocr_table / vision / mock）" % provider)
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
