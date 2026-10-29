-- Account-private employer questions. Answers and reusable memory commit together.
create table public.application_agent_questions (
 application_id uuid primary key references public.applications(id) on delete cascade,
 user_id uuid not null references auth.users(id) on delete cascade,
 revision text not null check(length(revision)=64),
 source_revision text not null check(length(source_revision)=64),
 questions jsonb not null default '[]' check(jsonb_typeof(questions)='array' and jsonb_array_length(questions)<=50),
 state text not null default 'needs_answers' check(state in ('needs_answers','ready')),
 updated_at timestamptz not null default now()
);
create index application_agent_questions_owner on public.application_agent_questions(user_id);
alter table public.application_agent_questions enable row level security;
create policy own_agent_questions on public.application_agent_questions for all to authenticated
 using ((select auth.uid())=user_id)
 with check ((select auth.uid())=user_id and exists(select 1 from public.applications a where a.id=application_id and a.user_id=(select auth.uid())));
revoke all on public.application_agent_questions from anon;
grant select,insert,update,delete on public.application_agent_questions to authenticated;

create table public.application_answer_memory (
 user_id uuid not null references auth.users(id) on delete cascade,
 question_key text not null check(length(question_key)=64),
 question_label text not null check(length(question_label) between 1 and 2000),
 context jsonb not null default '{}',
 answer text not null check(length(answer)<=4000),
 source_application_id uuid references public.applications(id) on delete set null,
 updated_at timestamptz not null default now(),
 primary key(user_id,question_key)
);
create index application_answer_memory_source on public.application_answer_memory(source_application_id);
alter table public.application_answer_memory enable row level security;
create policy own_answer_memory on public.application_answer_memory for all to authenticated
 using ((select auth.uid())=user_id)
 with check ((select auth.uid())=user_id and (source_application_id is null or exists(select 1 from public.applications a where a.id=source_application_id and a.user_id=(select auth.uid()))));
revoke all on public.application_answer_memory from anon;
grant select,insert,update,delete on public.application_answer_memory to authenticated;

create function public.save_application_agent_answers(p_application_id uuid,p_revision text,p_expected_updated_at timestamptz,p_answers jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare row public.application_agent_questions; app public.applications; q jsonb; a jsonb; result jsonb='[]'; seen text[]='{}'; val text;
begin
 select * into app from public.applications where id=p_application_id and user_id=(select auth.uid()) for update;
 if not found then raise exception 'Application not found'; end if;
 if app.status<>'found' or app.submitted_at is not null or app.receipt_confirmed_at is not null then raise exception 'Already submitted'; end if;
 if app.updated_at is distinct from p_expected_updated_at then raise exception 'Application changed'; end if;
 select * into row from public.application_agent_questions where application_id=p_application_id and user_id=(select auth.uid()) for update;
 if not found or row.revision<>p_revision then raise exception 'Question revision changed'; end if;
 if jsonb_typeof(p_answers)<>'array' or jsonb_array_length(p_answers)<>jsonb_array_length(row.questions) then raise exception 'Answer all current questions'; end if;
 for q in select * from jsonb_array_elements(row.questions) loop
  select value into a from jsonb_array_elements(p_answers) where value->>'key'=q->>'key';
  if a is null or (q->>'key')=any(seen) or (select count(*) from jsonb_array_elements(p_answers) where value->>'key'=q->>'key')<>1 then raise exception 'Invalid answer key'; end if;
  seen=array_append(seen,q->>'key');
  if jsonb_typeof(a->'value')<>'string' or jsonb_typeof(a->'remember')<>'boolean' then raise exception 'Invalid answer type'; end if;
  val=btrim(a->>'value');
  if length(val)>4000 or (coalesce((q->>'required')::boolean,true) and val='') then raise exception 'Required answer missing'; end if;
  if q->>'type'='select' and val<>'' and not (q->'options' ? val) then raise exception 'Invalid employer option'; end if;
  if (a->>'remember')::boolean and val<>'' then
   insert into public.application_answer_memory(user_id,question_key,question_label,context,answer,source_application_id)
   values ((select auth.uid()),q->>'memory_key',q->>'label',q->'context',val,p_application_id)
   on conflict(user_id,question_key) do update set answer=excluded.answer,source_application_id=excluded.source_application_id,updated_at=now();
  else
   delete from public.application_answer_memory where user_id=(select auth.uid()) and question_key=q->>'memory_key';
  end if;
  result=result || jsonb_build_array(q||jsonb_build_object('answer',val,'remember',(a->>'remember')::boolean,'source','confirmed','confirmed_at',now()));
 end loop;
 update public.application_agent_questions set questions=result,state='ready',updated_at=now() where application_id=p_application_id and user_id=(select auth.uid());
 return jsonb_build_object('state','ready','questions',result,'revision',p_revision);
end $$;
revoke all on function public.save_application_agent_answers(uuid,text,timestamptz,jsonb) from public,anon;
grant execute on function public.save_application_agent_answers(uuid,text,timestamptz,jsonb) to authenticated;
