# Loja Poliglota (Flask)

Aluno: Iago Frânquel Freitas Sousa - Quarto período - Manhã

| Banco | Uso |
|---|---|
| Supabase (PostgreSQL) | Login do admin (Auth), tabela `admins` (RLS), `audit_log` |
| MongoDB | Cadastro/armazenamento dos produtos (documentos flexíveis, `atributos` livres) |
| Redis | Cache do catálogo (TTL 60s) e lista "vistos recentemente" por visitante |

## Configuração
1. `docker compose up -d` (MongoDB + Redis) — ou use instalações locais/Atlas/Upstash.
2. Supabase: crie um projeto, rode [sql/supabase_setup.sql](sql/supabase_setup.sql) no SQL Editor,
   crie um usuário em *Authentication > Users* (desmarque confirmação de e-mail ou confirme-o) e execute o `insert` final do script para torná-lo admin.
3. `copy .env.example .env` e preencha `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `WHATSAPP_NUMBER`.
4. ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
5. Abra http://localhost:5000 — admin em `/admin/login`.

## Fluxo
- Admin loga (Supabase) → cadastra produto (MongoDB, invalida cache Redis) → aparece no catálogo.
- Cliente adiciona ao carrinho (sessão) → "Comprar via WhatsApp" abre `wa.me` com
  `Olá, gostaria de pedir: 1x Produto A, 2x Produto B. Total: R$ XX,XX`.
