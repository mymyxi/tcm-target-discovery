import sqlite3
from flask import Flask, request, jsonify, send_from_directory
import os
import requests
from collections import defaultdict
import time

app = Flask(__name__)
DB = "/Users/mx/Desktop/中药靶点发现计划/tcm.db"
STATIC = "/Users/mx/Desktop/中药靶点发现计划"

# 速率限制：每IP每分钟最多5次
_rate = defaultdict(list)
def rate_limit(ip, limit=5, window=60):
    now = time.time()
    _rate[ip] = [t for t in _rate[ip] if now - t < window]
    if len(_rate[ip]) >= limit:
        return False
    _rate[ip].append(now)
    return True

AI_KEY = "18fe96b2-ca14-4e3a-bf4a-5a6fc0c5aaee"
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

@app.route("/api/search/herb")
def search_herb():
    """搜索草药，返回相关天然产物"""
    q = request.args.get("q", "")
    # 中文转拉丁名
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
    return jsonify({"query": q, "count": len(rows), "results": rows})

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
    return jsonify({
        "compound": compound[0],
        "species": species,
        "targets": targets
    })

@app.route("/api/interpret/<np_id>")
def interpret(np_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if not rate_limit(ip):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
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
    return jsonify({"interpretation": call_ai(prompt)})

if __name__ == "__main__":
    app.run(debug=True, port=5001)
