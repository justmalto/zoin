-- Temporary fixture data and changes are rolled back.
begin;
insert into auth.users(id, email) values
('e1010101-0000-4000-8000-000000000001','zoin-check-a@example.invalid'),
('e1010101-0000-4000-8000-000000000002','zoin-check-b@example.invalid');
insert into public.applications(id,user_id,company,job_title,job_description) values
('e2020202-0000-4000-8000-000000000001','e1010101-0000-4000-8000-000000000001','A','Engineer','Test description'),
('e2020202-0000-4000-8000-000000000002','e1010101-0000-4000-8000-000000000002','B','Engineer','Test description');
insert into public.resumes(id,user_id,file_name,file_path,mime_type,content_hash,resume_text) values
('e3030303-0000-4000-8000-000000000001','e1010101-0000-4000-8000-000000000001','a.txt','e1010101-0000-4000-8000-000000000001/a.txt','text/plain',repeat('a',64),'A'),
('e3030303-0000-4000-8000-000000000002','e1010101-0000-4000-8000-000000000002','b.txt','e1010101-0000-4000-8000-000000000002/b.txt','text/plain',repeat('b',64),'B');
select set_config('request.jwt.claim.sub','e1010101-0000-4000-8000-000000000001',true);
set local role authenticated;
do $$
begin
  if (select count(*) from public.applications)<>1 then raise exception 'RLS visibility failed'; end if;
  if (select count(*) from public.profiles)<>1 then raise exception 'Profile RLS failed'; end if;
  perform public.apply_application_receipt('e2020202-0000-4000-8000-000000000001',42,'<fixture>','["Application received"]'::jsonb);
  if (select count(*) from public.application_events)<>1 then raise exception 'Timeline failed'; end if;
  begin
    perform public.apply_application_receipt('e2020202-0000-4000-8000-000000000002',42,'<fixture>','["Application received"]'::jsonb);
    raise exception 'Cross-owner RPC was allowed';
  exception when no_data_found then null; end;
  begin
    update public.applications set status='offer';
    raise exception 'Direct status update was allowed';
  exception when insufficient_privilege then null; end;
  begin
    update public.applications set selected_resume_id='e3030303-0000-4000-8000-000000000002';
    raise exception 'Cross-owner resume selection was allowed';
  exception when foreign_key_violation then null; end;
  begin
    perform public.activate_resume('e3030303-0000-4000-8000-000000000002');
    raise exception 'Cross-owner activation was allowed';
  exception when no_data_found then null; end;
  if (select status from public.applications where id='e2020202-0000-4000-8000-000000000001')<>'applied' then raise exception 'Email confirmation failed'; end if;
  perform public.correct_application_receipt('e2020202-0000-4000-8000-000000000001');
  perform public.apply_application_receipt('e2020202-0000-4000-8000-000000000001',42,'<fixture>','["Application received"]'::jsonb);
  if (select status from public.applications where id='e2020202-0000-4000-8000-000000000001')<>'found' then raise exception 'Ignored receipt reapplied'; end if;
  begin
    perform public.transition_application('e2020202-0000-4000-8000-000000000001','applied');
    raise exception 'Manual applied was allowed';
  exception when raise_exception then
    if sqlerrm='Manual applied was allowed' then raise; end if;
  end;
  insert into public.resumes(user_id,file_name,file_path,mime_type,content_hash,resume_text)
  select auth.uid(),'test-'||n,auth.uid()||'/quota-'||n,'text/plain',md5(n::text)||md5(n::text),'Synthetic resume' from generate_series(1,9) n;
  begin
    insert into public.resumes(user_id,file_name,file_path,mime_type,content_hash,resume_text)
    values(auth.uid(),'overflow',auth.uid()||'/overflow','text/plain',repeat('c',64),'Synthetic');
    raise exception 'Eleventh resume was allowed';
  exception when raise_exception then
    if sqlerrm not like 'Saved resume limit reached%' then raise; end if;
  end;
  perform public.replace_resume('e3030303-0000-4000-8000-000000000001',jsonb_build_object('file_name','replaced.txt','file_path',auth.uid()||'/replaced.txt','mime_type','text/plain','content_hash',repeat('d',64),'resume_text','Replaced synthetic resume'),'[{"section":"general","chunk_index":0,"content":"Replaced synthetic resume"}]'::jsonb);
  if (select count(*) from public.resumes)<>10 then raise exception 'Replacement changed library count'; end if;
  perform public.activate_resume('e3030303-0000-4000-8000-000000000001');
  if not (select is_active from public.resumes where id='e3030303-0000-4000-8000-000000000001') then raise exception 'Activation failed'; end if;
end $$;
reset role;
select 'PASS: ownership, profile creation, lifecycle audit, status permissions, resume ownership and activation' as result;
rollback;
