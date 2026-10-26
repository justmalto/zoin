begin;
insert into auth.users(id,email) values('e6060606-0000-4000-8000-000000000001','audit@example.invalid');
insert into public.applications(id,user_id,company,job_title,job_description) values('e7070707-0000-4000-8000-000000000001','e6060606-0000-4000-8000-000000000001','Example','Engineer','Original JD');
insert into public.resumes(id,user_id,file_name,file_path,mime_type,content_hash,resume_text) values('e8080808-0000-4000-8000-000000000001','e6060606-0000-4000-8000-000000000001','a.txt','e6060606-0000-4000-8000-000000000001/a.txt','text/plain',repeat('a',64),'Original resume');
select set_config('request.jwt.claim.sub','e6060606-0000-4000-8000-000000000001',true);
set local role authenticated;
do $$
declare aid uuid:='e7070707-0000-4000-8000-000000000001'; rid uuid:='e8080808-0000-4000-8000-000000000001'; analysis jsonb:='{"match_score":80,"model_name":"test"}';
begin
 perform public.store_resume_recommendation(aid,rid,'Original JD',repeat('a',64),analysis);
 if (select count(*) from public.resume_recommendations)<>1 then raise exception 'Match save failed';end if;
 update public.applications set job_description='Edited JD' where id=aid;
 begin
  perform public.store_resume_recommendation(aid,rid,'Original JD',repeat('a',64),analysis);
  raise exception 'Stale JD accepted';
 exception when serialization_failure then null;end;
 perform public.store_resume_recommendation(aid,rid,'Edited JD',repeat('a',64),analysis);
 update public.resumes set resume_text='Edited resume',content_hash=repeat('b',64) where id=rid;
 if exists(select 1 from public.resume_recommendations) then raise exception 'Resume edit retained stale match';end if;
 begin
  perform public.store_resume_recommendation(aid,rid,'Edited JD',repeat('a',64),analysis);
  raise exception 'Stale resume accepted';
 exception when serialization_failure then null;end;
end $$;
reset role;
update zoin_private.mail_worker_config set key_hash=encode(extensions.digest(repeat('fixture-only-',5),'sha256'),'hex');
select public.mail_worker(repeat('fixture-only-',5),'connect','{"user_id":"e6060606-0000-4000-8000-000000000001","email_address":"audit@example.invalid","ciphertext":"fixture"}');
update public.applications set confirmation_pending=false where user_id='e6060606-0000-4000-8000-000000000001';
insert into public.applications(user_id,company,job_title,job_description,status,receipt_confirmed_at)
select 'e6060606-0000-4000-8000-000000000001','Cycle fixture','Engineer '||n,'Synthetic JD','applied',now() from generate_series(1,21)n;
update public.mail_sync_settings set enabled=true,next_sync_at=now()-interval '1 minute' where user_id='e6060606-0000-4000-8000-000000000001';
set local role anon;
do $$
declare k text:=repeat('fixture-only-',5); u text:='e6060606-0000-4000-8000-000000000001'; c jsonb; jobs jsonb; j jsonb; n int:=0;
begin
 c:=public.mail_worker(k,'claim',jsonb_build_object('user_id',u,'trigger','scheduled'));
 jobs:=public.mail_worker(k,'jobs',jsonb_build_object('user_id',u,'trigger','scheduled'));
 if jsonb_array_length(jobs)<>21 then raise exception 'Cycle omitted jobs';end if;
 for j in select value from jsonb_array_elements(jobs) limit 20 loop
  perform public.mail_worker(k,'record',jsonb_build_object('user_id',u,'run_id',c->>'run_id','application_id',j->>'id','expected_company',j->>'company','expected_title',j->>'job_title','expected_job_id',j->>'job_id','matches','[]'::jsonb));
 end loop;
 perform public.mail_worker(k,'finish',jsonb_build_object('user_id',u,'run_id',c->>'run_id','status','partial'));
 -- Claim supports continuation; production due polling waits fifteen minutes.
 c:=public.mail_worker(k,'claim',jsonb_build_object('user_id',u,'trigger','scheduled'));
 jobs:=public.mail_worker(k,'jobs',jsonb_build_object('user_id',u,'trigger','scheduled'));
 if jsonb_array_length(jobs)<>1 then raise exception 'Cycle repeated completed jobs';end if;
 j:=jobs->0;
 perform public.mail_worker(k,'record',jsonb_build_object('user_id',u,'run_id',c->>'run_id','application_id',j->>'id','expected_company',j->>'company','expected_title',j->>'job_title','matches','[]'::jsonb));
 perform public.mail_worker(k,'finish',jsonb_build_object('user_id',u,'run_id',c->>'run_id','status','success'));
end $$;
reset role;
do $$
declare before_time timestamptz; after_time timestamptz; c jsonb; k text:=repeat('fixture-only-',5); u text:='e6060606-0000-4000-8000-000000000001';
begin
 select next_sync_at into before_time from public.mail_sync_settings where user_id=u::uuid;
 if exists(select 1 from public.mail_sync_settings where user_id=u::uuid and (cycle_started_at is not null or last_synced_at is null)) then raise exception 'Cycle did not finish';end if;
 c:=public.mail_worker(k,'claim',jsonb_build_object('user_id',u,'trigger','manual','application_id','e7070707-0000-4000-8000-000000000001'));
 perform public.mail_worker(k,'finish',jsonb_build_object('user_id',u,'run_id',c->>'run_id','status','success'));
 select next_sync_at into after_time from public.mail_sync_settings where user_id=u::uuid;
 if before_time is distinct from after_time then raise exception 'Single-job sync postponed schedule';end if;
 update public.applications set confirmation_pending=true where id='e7070707-0000-4000-8000-000000000001';
 c:=public.mail_worker(k,'claim',jsonb_build_object('user_id',u,'trigger','scheduled'));
 if jsonb_array_length(public.mail_worker(k,'jobs',jsonb_build_object('user_id',u,'trigger','scheduled')))<>1 then raise exception 'Confirmation-only scan included Applied jobs';end if;
 perform public.mail_worker(k,'record',jsonb_build_object('user_id',u,'run_id',c->>'run_id','application_id','e7070707-0000-4000-8000-000000000001','expected_company','Example','expected_title','Engineer','before_uid',42,'matches','[]'::jsonb));
 if not (select confirmation_pending from public.applications where id='e7070707-0000-4000-8000-000000000001') then raise exception 'Pending confirmation lost before inbox pagination finished';end if;
end $$;
rollback;
select 'PASS: atomic match guards, resume invalidation, 21-job continuation, schedule isolation' as result;
