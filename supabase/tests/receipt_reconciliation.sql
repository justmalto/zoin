-- All synthetic rows, the temporary capability hash, and lifecycle writes roll back.
begin;
insert into auth.users(id,email) values
 ('b0000000-0000-4000-8000-000000000001','receipt-qa-1@example.invalid'),
 ('b0000000-0000-4000-8000-000000000002','receipt-qa-2@example.invalid');
insert into public.applications(id,user_id,company,job_title,job_description,status,submitted_at) values
 ('b0000000-0000-4000-8000-000000000011','b0000000-0000-4000-8000-000000000001','Receipt QA','Engineer','Synthetic job description for receipt tests.','found',now()),
 ('b0000000-0000-4000-8000-000000000012','b0000000-0000-4000-8000-000000000001','Receipt QA','Other role','Synthetic job description for receipt tests.','found',now()),
 ('b0000000-0000-4000-8000-000000000013','b0000000-0000-4000-8000-000000000002','Receipt QA','Engineer','Synthetic job description for receipt tests.','found',now());
insert into public.mail_sync_runs(id,user_id,trigger) values
 ('b0000000-0000-4000-8000-000000000021','b0000000-0000-4000-8000-000000000001','job_saved');
insert into public.mail_sync_settings(user_id,email_address,active_run_id,lease_until) values
 ('b0000000-0000-4000-8000-000000000001','receipt-qa-1@example.invalid','b0000000-0000-4000-8000-000000000021',now()+interval '10 minutes');
insert into public.mail_sync_messages(user_id,application_id,message_key,category,confidence,suggested_status,changed) values
 ('b0000000-0000-4000-8000-000000000001','b0000000-0000-4000-8000-000000000011',repeat('c',64),'confirmation',1,'applied',false);
-- MVCC keeps the real capability visible to concurrent sessions; this change never commits.
update zoin_private.mail_worker_config set key_hash=encode(extensions.digest(repeat('receipt-fixture-',4),'sha256'),'hex');
do $$
declare payload jsonb; jobs jsonb; result jsonb; capability text:=repeat('receipt-fixture-',4);
begin
 payload:=jsonb_build_object('user_id','b0000000-0000-4000-8000-000000000001','application_id','b0000000-0000-4000-8000-000000000011',
  'run_id','b0000000-0000-4000-8000-000000000021','expected_company','Receipt QA','expected_title','Engineer','expected_job_id',null,
  'matches',jsonb_build_array(jsonb_build_object('message_key',repeat('c',64),'message_uid',42,'message_id','<receipt-qa>',
   'category','confirmation','confidence',1,'suggested_status','applied','receipt_valid',true,'evidence',jsonb_build_array('We received your application.'))));
 jobs:=public.mail_worker(capability,'jobs',payload);
 if (jobs->0->>'receipt_company_candidates')::int<>2 then raise exception 'QA ambiguity not detected';end if;
 update public.applications set submitted_at=now()-interval '8 days' where id='b0000000-0000-4000-8000-000000000012';
 jobs:=public.mail_worker(capability,'jobs',payload);
 if (jobs->0->>'receipt_company_candidates')::int<>1 then raise exception 'QA owner/date scope failed';end if;
 begin
  perform public.mail_worker('wrong-key','record',payload);
  raise exception 'QA wrong capability accepted';
 exception when insufficient_privilege then null;end;
 begin
  perform public.mail_worker(capability,'record',payload||'{"application_id":"b0000000-0000-4000-8000-000000000013"}');
  raise exception 'QA foreign job accepted';
 exception when no_data_found then null;end;
 result:=public.mail_worker(capability,'record',payload);
 if result->>'status'<>'applied' or result->>'receipt_confirmed_at' is null then raise exception 'QA unchanged receipt not reconciled';end if;
 perform public.mail_worker(capability,'record',payload);
 if (select count(*) from public.mail_sync_messages where application_id='b0000000-0000-4000-8000-000000000011')<>1 then raise exception 'QA duplicate message log';end if;
 if (select count(*) from public.application_events where application_id='b0000000-0000-4000-8000-000000000011' and event_type='application_received')<>1 then raise exception 'QA duplicate event';end if;
 if (select changed_jobs from public.mail_sync_runs where id='b0000000-0000-4000-8000-000000000021')<>1 then raise exception 'QA duplicate counter';end if;
 update public.applications set status='found',receipt_confirmed_at=null,ignored_receipt_uids=array[42]::bigint[] where id='b0000000-0000-4000-8000-000000000011';
 result:=public.mail_worker(capability,'record',payload);
 if result->>'status'<>'found' then raise exception 'QA user correction overridden';end if;
 -- A previously unrecorded, ignored UID must also remain Found.
 result:=public.mail_worker(capability,'record',jsonb_set(payload,'{matches,0,message_key}',to_jsonb(repeat('d',64))));
 if result->>'status'<>'found' then raise exception 'QA ignored receipt accepted';end if;
end $$;
select 'PASS: reconciliation, ambiguity/owner scope, capability, idempotency and user correction' as result;
rollback;
