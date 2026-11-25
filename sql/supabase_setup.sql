-- Execute no SQL Editor do Supabase.
-- 1) Tabela de administradores (quem pode acessar o painel)
create table if not exists public.admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  criado_em timestamptz default now()
);
alter table public.admins enable row level security;
drop policy if exists "admin le o proprio registro" on public.admins;
create policy "admin le o proprio registro" on public.admins
  for select to authenticated using (auth.uid() = user_id);

-- 2) Log de auditoria das ações do admin
create table if not exists public.audit_log (
  id bigserial primary key,
  user_id uuid not null,
  acao text not null,
  detalhe text,
  criado_em timestamptz default now()
);
alter table public.audit_log enable row level security;
drop policy if exists "admin insere log" on public.audit_log;
create policy "admin insere log" on public.audit_log
  for insert to authenticated
  with check (auth.uid() = user_id and exists (select 1 from public.admins a where a.user_id = auth.uid()));
drop policy if exists "admin le log" on public.audit_log;
create policy "admin le log" on public.audit_log
  for select to authenticated
  using (exists (select 1 from public.admins a where a.user_id = auth.uid()));

-- 3) Depois de criar o usuário em Authentication > Users, torne-o admin:
-- insert into public.admins (user_id) select id from auth.users where email = 'SEU_EMAIL_AQUI';
