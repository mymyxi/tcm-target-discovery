import sqlite3
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
import requests
import json
from collections import defaultdict
import time

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "tcm.db")
STATIC = BASE_DIR

def init_log_table():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS query_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT DEFAULT (datetime('now','localtime')),
        ip TEXT, type TEXT, query TEXT, np_id TEXT
    )""")
    conn.commit()
    conn.close()

init_log_table()

# 速率限制：每IP每分钟最多5次
_rate = defaultdict(list)
def rate_limit(ip, limit=5, window=60):
    now = time.time()
    _rate[ip] = [t for t in _rate[ip] if now - t < window]
    if len(_rate[ip]) >= limit:
        return False
    _rate[ip].append(now)
    return True

AI_KEY = os.environ.get("AI_KEY", "")
AI_URL = "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"

def call_ai(prompt):
    try:
        resp = requests.post(AI_URL,
            headers={"Authorization": f"Bearer {AI_KEY}", "content-type": "application/json"},
            json={"model": "kimi-k2-5", "messages": [{"role": "user", "content": prompt}]},
            timeout=30)
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"AI 解读暂时不可用（网络限制），部署后可正常使用。"

# 中文草药名→拉丁名映射
HERB_CN = {
    "黄芪": "Astragalus membranaceus",
    "甘草": "Glycyrrhiza",
    "人参": "Panax ginseng",
    "当归": "Angelica sinensis",
    "丹参": "Salvia miltiorrhiza",
    "黄连": "Coptis chinensis",
    "金银花": "Lonicera japonica",
    "柴胡": "Bupleurum chinense",
    "白术": "Atractylodes macrocephala",
    "茯苓": "Poria cocos",
    "川芎": "Ligusticum chuanxiong",
    "熟地黄": "Rehmannia glutinosa",
    "枸杞": "Lycium barbarum",
    "麻黄": "Ephedra sinica",
    "桂枝": "Cinnamomum cassia",
}

@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")

def query(sql, params=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def log_query(type, query_str, np_id=""):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO query_log (ip, type, query, np_id) VALUES (?,?,?,?)",
                 (ip, type, query_str, np_id))
    conn.commit()
    conn.close()

@app.route("/api/search/herb")
def search_herb():
    """搜索草药，返回相关天然产物"""
    q = request.args.get("q", "")
    q = HERB_CN.get(q, q)
    limit = int(request.args.get("limit", 20))
    rows = query("""
        SELECT DISTINCT c.np_id, c.name, c.num_of_target,
               s.org_name, s.species_name, s.family_name
        FROM compound c
        JOIN compound_species cs ON c.np_id = cs.np_id
        JOIN species s ON cs.org_id = s.org_id
        WHERE s.org_name LIKE ? OR s.species_name LIKE ?
        ORDER BY c.num_of_target DESC
        LIMIT ?
    """, (f"%{q}%", f"%{q}%", limit))
    # 用属名前5字符匹配 SymMap 性味归经
    genus = q.split()[0][:5] if q else ""
    herb_info = query("""
        SELECT chinese_name, properties_chinese, meridians_chinese
        FROM symmap_herb WHERE latin_name LIKE ? LIMIT 1
    """, (f"%{genus}%",))
    extra = herb_info[0] if herb_info else {}
    log_query("herb", q)
    return jsonify({"query": q, "count": len(rows), "results": rows, "herb_info": extra})

@app.route("/api/search/compound")
def search_compound():
    """搜索天然产物，返回靶点"""
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", 20))
    rows = query("""
        SELECT t.target_name, t.target_type, t.uniprot_id,
               ct.activity_type, ct.activity_value, ct.activity_units
        FROM compound_target ct
        JOIN target t ON ct.target_id = t.target_id
        JOIN compound c ON ct.np_id = c.np_id
        WHERE c.name LIKE ? AND ct.activity_value IS NOT NULL
        ORDER BY ct.activity_value ASC
        LIMIT ?
    """, (f"%{q}%", limit))
    log_query("compound", q)
    return jsonify({"query": q, "count": len(rows), "results": rows})

@app.route("/api/compound/<np_id>")
def compound_detail(np_id):
    """天然产物详情：来源物种+靶点"""
    compound = query("SELECT * FROM compound WHERE np_id=?", (np_id,))
    if not compound:
        return jsonify({"error": "not found"}), 404
    species = query("""
        SELECT s.org_name, s.species_name, s.family_name
        FROM species s JOIN compound_species cs ON s.org_id=cs.org_id
        WHERE cs.np_id=?
    """, (np_id,))
    targets = query("""
        SELECT t.target_name, t.target_type, ct.activity_type,
               ct.activity_value, ct.activity_units
        FROM compound_target ct JOIN target t ON ct.target_id=t.target_id
        WHERE ct.np_id=? AND ct.activity_value IS NOT NULL
        ORDER BY ct.activity_value ASC LIMIT 20
    """, (np_id,))
    diseases = query("""
        SELECT DISTINCT td.disease, td.clinical_status, td.icd11
        FROM compound_target ct
        JOIN target t ON ct.target_id=t.target_id
        JOIN ttd_target_disease td ON td.target_name LIKE '%' || t.target_name || '%'
        WHERE ct.np_id=? AND td.disease != ''
        LIMIT 10
    """, (np_id,))
    log_query("detail", np_id, np_id)
    return jsonify({
        "compound": compound[0],
        "species": species,
        "targets": targets,
        "diseases": diseases
    })

_interpret_cache = {}

@app.route("/api/interpret/<np_id>")
def interpret(np_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if not rate_limit(ip):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    if np_id in _interpret_cache:
        return jsonify({"interpretation": _interpret_cache[np_id], "cached": True})
    compound = query("SELECT * FROM compound WHERE np_id=?", (np_id,))
    if not compound:
        return jsonify({"error": "not found"}), 404
    targets = query("""
        SELECT t.target_name, t.target_type, ct.activity_type, ct.activity_value, ct.activity_units
        FROM compound_target ct JOIN target t ON ct.target_id=t.target_id
        WHERE ct.np_id=? AND ct.activity_value IS NOT NULL
        ORDER BY ct.activity_value ASC LIMIT 10
    """, (np_id,))
    c = compound[0]
    target_lines = "\n".join([f"- {t['target_name']} ({t['target_type']}): {t['activity_type']} = {t['activity_value']} {t['activity_units']}" for t in targets])
    prompt = f"""天然产物：{c['name']}（{np_id}）
存在于 {c['num_of_organism']} 个物种，已知 {c['num_of_target']} 个靶点。

活性最强的前10个靶点：
{target_lines}

请用中文简洁解读：1）该化合物的主要药理作用方向；2）最值得关注的靶点及其临床意义；3）在中药研究中的潜在价值。200字以内。"""
    result = call_ai(prompt)
    _interpret_cache[np_id] = result
    log_query("interpret", np_id, np_id)
    return jsonify({"interpretation": result})

@app.route("/api/interpret/stream/<np_id>")
def interpret_stream(np_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if not rate_limit(ip):
        return jsonify({"error": "请求过于频繁"}), 429
    compound = query("SELECT * FROM compound WHERE np_id=?", (np_id,))
    if not compound:
        return jsonify({"error": "not found"}), 404
    targets = query("""
        SELECT t.target_name, t.target_type, ct.activity_type, ct.activity_value, ct.activity_units
        FROM compound_target ct JOIN target t ON ct.target_id=t.target_id
        WHERE ct.np_id=? AND ct.activity_value IS NOT NULL
        ORDER BY ct.activity_value ASC LIMIT 10
    """, (np_id,))
    c = compound[0]
    target_lines = "\n".join([f"- {t['target_name']} ({t['target_type']}): {t['activity_type']} = {t['activity_value']} {t['activity_units']}" for t in targets])
    prompt = f"""天然产物：{c['name']}（{np_id}）\n存在于 {c['num_of_organism']} 个物种，已知 {c['num_of_target']} 个靶点。\n\n活性最强的前10个靶点：\n{target_lines}\n\n请用中文简洁解读：1）主要药理作用方向；2）最值得关注的靶点及临床意义；3）中药研究潜在价值。200字以内。"""

    def generate():
        resp = requests.post(AI_URL,
            headers={"Authorization": f"Bearer {AI_KEY}", "content-type": "application/json"},
            json={"model": "kimi-k2-5", "stream": True, "messages": [{"role": "user", "content": prompt}]},
            stream=True, timeout=60)
        for line in resp.iter_lines():
            if not line or line == b"data: [DONE]": continue
            if line.startswith(b"data: "):
                chunk = json.loads(line[6:])
                text = chunk["choices"][0]["delta"].get("content", "")
                if text:
                    yield f"data: {text}\n\n"
        yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    app.run(debug=True, port=5001)
