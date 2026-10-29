-- Synthetic identities and records are rolled back, including on a failed assertion.
begin;
insert into auth.users(id,email) values ('a0000000-0000-4000-8000-000000000001','zoin-qa-answers-1@example.invalid'),('a0000000-0000-4000-8000-000000000002','zoin-qa-answers-2@example.invalid');
insert into public.applications(id,user_id,company,job_title,job_description,status) values
 ('a0000000-0000-4000-8000-000000000011','a0000000-0000-4000-8000-000000000001','Synthetic employer','Synthetic job','Synthetic description for testing answer isolation.','found'),
 ('a0000000-0000-4000-8000-000000000012','a0000000-0000-4000-8000-000000000002','Synthetic employer','Synthetic job','Synthetic description for testing answer isolation.','found');
set local role authenticated;
select set_config('request.jwt.claims','{"sub":"a0000000-0000-4000-8000-000000000001","role":"authenticated"}',true);
insert into public.application_agent_questions(application_id,user_id,revision,source_revision,questions) values
 ('a0000000-0000-4000-8000-000000000011','a0000000-0000-4000-8000-000000000001',repeat('a',64),repeat('b',64),jsonb_build_array(jsonb_build_object('key','q1','label','Eligible in India?','type','select','options',jsonb_build_array('Yes','No'),'required',true,'context',jsonb_build_object('country','India','employer','Synthetic employer'),'memory_key',repeat('c',64))));
do $$
declare stamp timestamptz;
begin
 select updated_at into stamp from public.applications where id='a0000000-0000-4000-8000-000000000011';
 begin
  perform public.save_application_agent_answers('a0000000-0000-4000-8000-000000000011',repeat('a',64),stamp,'[{"key":"q1","value":"Maybe","remember":true}]');
  raise exception 'QA invalid option accepted';
 exception when others then if sqlerrm='QA invalid option accepted' then raise; end if; end;
 if exists(select 1 from public.application_answer_memory) then raise exception 'QA invalid answer wrote memory'; end if;
 perform public.save_application_agent_answers('a0000000-0000-4000-8000-000000000011',repeat('a',64),stamp,'[{"key":"q1","value":"Yes","remember":true}]');
 if (select count(*) from public.application_answer_memory)<>1 then raise exception 'QA memory not saved'; end if;
 if (select state from public.application_agent_questions where application_id='a0000000-0000-4000-8000-000000000011')<>'ready' then raise exception 'QA state not ready'; end if;
 perform public.save_application_agent_answers('a0000000-0000-4000-8000-000000000011',repeat('a',64),stamp,'[{"key":"q1","value":"No","remember":false}]');
 if exists(select 1 from public.application_answer_memory) then raise exception 'QA unchecked answer remembered'; end if;
 begin
  insert into public.application_agent_questions(application_id,user_id,revision,source_revision) values ('a0000000-0000-4000-8000-000000000012','a0000000-0000-4000-8000-000000000001',repeat('a',64),repeat('b',64));
  raise exception 'QA foreign application insert allowed';
 exception when insufficient_privilege then null; end;
end $$;
select set_config('request.jwt.claims','{"sub":"a0000000-0000-4000-8000-000000000002","role":"authenticated"}',true);
do $$ begin
 if exists(select 1 from public.application_agent_questions) or exists(select 1 from public.application_answer_memory) then raise exception 'QA foreign answers visible'; end if;
 begin
  perform public.save_application_agent_answers('a0000000-0000-4000-8000-000000000011',repeat('a',64),now(),'[]');
  raise exception 'QA foreign answer write allowed';
 exception when others then if sqlerrm='QA foreign answer write allowed' then raise; end if; end;
end $$;
select 'answer transaction, validation and cross-account isolation passed' as result;
rollback;
