"""E-commerce poliglota: Supabase (auth) + MongoDB (produtos) + Redis (cache)."""
import json
import os
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import quote

import redis
import requests
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)
from pymongo import MongoClient

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-troque-isto")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "5500000000000")

mongo = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                    serverSelectionTimeoutMS=3000)
products = mongo[os.getenv("MONGO_DB", "loja")]["produtos"]

rds = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                           decode_responses=True)

CATALOG_KEY = "cache:catalogo"
CATALOG_TTL = 60
RECENT_KEY = "recentes:{sid}"


# ---------- helpers ----------
def redis_ok():
    try:
        rds.ping()
        return True
    except redis.RedisError:
        return False


def serialize(doc):
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    doc.pop("criado_em", None)
    return doc


def get_catalog():
    """Lista produtos do MongoDB, com cache no Redis."""
    if redis_ok():
        cached = rds.get(CATALOG_KEY)
        if cached:
            return json.loads(cached), True
    items = [serialize(d) for d in products.find().sort("criado_em", -1)]
    if redis_ok():
        rds.setex(CATALOG_KEY, CATALOG_TTL, json.dumps(items))
    return items, False


def invalidate_catalog():
    if redis_ok():
        rds.delete(CATALOG_KEY)


def get_product(pid):
    try:
        doc = products.find_one({"_id": ObjectId(pid)})
    except InvalidId:
        return None
    return serialize(doc) if doc else None


def session_id():
    if "sid" not in session:
        session["sid"] = os.urandom(8).hex()
    return session["sid"]


def push_recent(pid):
    if not redis_ok():
        return
    key = RECENT_KEY.format(sid=session_id())
    rds.lrem(key, 0, pid)
    rds.lpush(key, pid)
    rds.ltrim(key, 0, 4)
    rds.expire(key, 60 * 60 * 24)


def get_recent():
    if not redis_ok():
        return []
    ids = rds.lrange(RECENT_KEY.format(sid=session_id()), 0, 4)
    result = []
    for pid in ids:
        p = get_product(pid)
        if p:
            result.append(p)
    return result


def brl(v):
    return f"{float(v):.2f}".replace(".", ",")


app.jinja_env.filters["brl"] = brl


# ---------- Supabase ----------
def supabase_login(email, password):
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password}, timeout=10)
    return r.json() if r.status_code == 200 else None


def is_admin(user_id, token):
    """Consulta a tabela public.admins (PostgreSQL) com o JWT do usuário (RLS)."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/admins",
        params={"user_id": f"eq.{user_id}", "select": "user_id"},
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
        timeout=10)
    return r.status_code == 200 and len(r.json()) > 0


def log_event(token, user_id, acao, detalhe):
    """Registra auditoria no PostgreSQL (Supabase)."""
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/audit_log",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"user_id": user_id, "acao": acao, "detalhe": detalhe}, timeout=10)
    except requests.RequestException:
        pass


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            flash("Faça login como administrador.", "error")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


# ---------- cliente ----------
@app.route("/")
def catalog():
    items, from_cache = get_catalog()
    q = request.args.get("q", "").strip().lower()
    if q:
        items = [p for p in items if q in p["nome"].lower()]
    return render_template("catalog.html", items=items, from_cache=from_cache,
                           recent=get_recent(), q=q)


@app.route("/produto/<pid>")
def product_detail(pid):
    p = get_product(pid)
    if not p:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("catalog"))
    push_recent(pid)
    return render_template("product.html", p=p)


def cart():
    return session.setdefault("cart", {})


@app.route("/carrinho")
def cart_view():
    lines, total = [], 0.0
    for pid, qty in cart().items():
        p = get_product(pid)
        if p:
            sub = p["preco"] * qty
            total += sub
            lines.append({"p": p, "qty": qty, "sub": sub})
    return render_template("cart.html", lines=lines, total=total)


@app.post("/carrinho/add/<pid>")
def cart_add(pid):
    if not get_product(pid):
        flash("Produto não encontrado.", "error")
        return redirect(url_for("catalog"))
    c = cart()
    c[pid] = c.get(pid, 0) + 1
    session.modified = True
    flash("Item adicionado ao carrinho.", "ok")
    return redirect(request.referrer or url_for("catalog"))


@app.post("/carrinho/remove/<pid>")
def cart_remove(pid):
    c = cart()
    if pid in c:
        c[pid] -= 1
        if c[pid] <= 0:
            del c[pid]
        session.modified = True
    return redirect(url_for("cart_view"))


@app.post("/carrinho/delete/<pid>")
def cart_delete(pid):
    cart().pop(pid, None)
    session.modified = True
    return redirect(url_for("cart_view"))


@app.post("/comprar")
def checkout():
    parts, total = [], 0.0
    for pid, qty in cart().items():
        p = get_product(pid)
        if p:
            parts.append(f"{qty}x {p['nome']}")
            total += p["preco"] * qty
    if not parts:
        flash("Carrinho vazio.", "error")
        return redirect(url_for("cart_view"))
    msg = f"Olá, gostaria de pedir: {', '.join(parts)}. Total: R$ {brl(total)}"
    return redirect(f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(msg)}")


# ---------- admin ----------
@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = supabase_login(request.form["email"], request.form["password"])
        if not data:
            flash("E-mail ou senha inválidos.", "error")
        else:
            uid, token = data["user"]["id"], data["access_token"]
            if is_admin(uid, token):
                session["admin"] = {"id": uid, "email": data["user"]["email"],
                                    "token": token}
                log_event(token, uid, "login", data["user"]["email"])
                return redirect(url_for("admin_panel"))
            flash("Usuário não é administrador.", "error")
    return render_template("login.html")


@app.route("/admin/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("catalog"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    if request.method == "POST":
        try:
            preco = float(request.form["preco"].replace(",", "."))
            estoque = int(request.form.get("estoque") or 0)
        except ValueError:
            flash("Preço ou estoque inválido.", "error")
            return redirect(url_for("admin_panel"))
        nome = request.form["nome"].strip()
        if not nome or preco < 0:
            flash("Nome e preço válidos são obrigatórios.", "error")
            return redirect(url_for("admin_panel"))
        # modelo flexível: atributos extras no formato chave=valor por linha
        attrs = {}
        for line in request.form.get("atributos", "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    attrs[k.strip()] = v.strip()
        products.insert_one({
            "nome": nome,
            "descricao": request.form.get("descricao", "").strip(),
            "preco": preco,
            "imagem": request.form.get("imagem", "").strip(),
            "categoria": request.form.get("categoria", "").strip(),
            "estoque": estoque,
            "atributos": attrs,
            "criado_em": datetime.now(timezone.utc),
        })
        invalidate_catalog()
        a = session["admin"]
        log_event(a["token"], a["id"], "cadastro_produto", nome)
        flash("Produto cadastrado!", "ok")
        return redirect(url_for("admin_panel"))
    items, _ = get_catalog()
    return render_template("admin.html", items=items)


@app.post("/admin/excluir/<pid>")
@admin_required
def admin_delete(pid):
    try:
        products.delete_one({"_id": ObjectId(pid)})
    except InvalidId:
        pass
    invalidate_catalog()
    a = session["admin"]
    log_event(a["token"], a["id"], "exclusao_produto", pid)
    flash("Produto excluído.", "ok")
    return redirect(url_for("admin_panel"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
