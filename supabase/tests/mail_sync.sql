begin;
update zoin_private.mail_worker_config set key_hash=encode(extensions.digest(repeat('fixture-only-',5),'sha256'),'hex');
insert into auth.users(id,email) values ('e4040404-0000-4000-8000-000000000001','sync-a@example.invalid'),('e4040404-0000-4000-8000-000000000002','sync-b@example.invalid');
insert into public.applications(id,user_id,company,job_title,job_description) values
('e5050505-0000-4000-8000-000000000001','e4040404-0000-4000-8000-000000000001','Example','Engineer','Synthetic description'),
('e5050505-0000-4000-8000-000000000002','e4040404-0000-4000-8000-000000000002','Other','Analyst','Synthetic description');
update public.applications set confirmation_pending=false where user_id='e4040404-0000-4000-8000-000000000001';
set local role anon;
do $$
declare key text:=repeat('fixture-only-',5); uid text:='e4040404-0000-4000-8000-000000000001'; aid text:='e5050505-0000-4000-8000-000000000001'; c jsonb; r jsonb;
begin
 begin perform public.mail_worker('incorrect','due');raise exception 'Worker accepted invalid key';exception when insufficient_privilege then null;end;
 perform public.mail_worker(key,'connect',jsonb_build_object('user_id',uid,'email_address','sync-a@example.invalid','ciphertext','encrypted-fixture'));
 perform public.mail_worker(key,'settings',jsonb_build_object('user_id',uid,'interval_days',7,'enabled',true));
 begin perform public.mail_worker(key,'settings',jsonb_build_object('user_id',uid,'interval_days',8,'enabled',true));raise exception 'Accepted invalid interval';exception when check_violation then null;end;
 r:=public.mail_worker(key,'jobs',jsonb_build_object('user_id',uid,'trigger','scheduled'));
 if jsonb_array_length(r)<>1 then raise exception 'Pending Found confirmation was skipped';end if;
 c:=public.mail_worker(key,'claim',jsonb_build_object('user_id',uid,'trigger','manual'));
 if c->>'state'<>'claimed' then raise exception 'Claim failed';end if;
 r:=public.mail_worker(key,'jobs',jsonb_build_object('user_id',uid,'trigger','manual'));
 if jsonb_array_length(r)<>1 then raise exception 'Manual sync skipped Found';end if;
 r:=public.mail_worker(key,'claim',jsonb_build_object('user_id',uid,'trigger','manual'));
 if r->>'state'<>'busy' then raise exception 'Overlapping sync allowed';end if;
 r:=public.mail_worker(key,'record',jsonb_build_object('user_id',uid,'run_id',c->>'run_id','application_id',aid,'expected_company','Example','expected_title','Engineer','matches',jsonb_build_array(jsonb_build_object('message_key','fixture-message','category','confirmation','confidence',1,'suggested_status','applied','message_uid',42,'message_id','<fixture>','evidence',jsonb_build_array('Application received'),'receipt_valid',true))));
 if r->>'status'<>'applied' or r->>'last_mail_synced_at' is null then raise exception 'Receipt/timestamp failed';end if;
 r:=public.mail_worker(key,'jobs',jsonb_build_object('user_id',uid,'trigger','scheduled'));
 if jsonb_array_length(r)<>0 then raise exception 'Cycle repeated processed Applied job';end if;
 -- Idempotent receipt replays do not create another lifecycle event.
 perform public.mail_worker(key,'record',jsonb_build_object('user_id',uid,'run_id',c->>'run_id','application_id',aid,'expected_company','Example','expected_title','Engineer','matches',jsonb_build_array(jsonb_build_object('message_key','fixture-message','category','confirmation','confidence',1,'suggested_status','applied','message_uid',42,'receipt_valid',true))));
 begin perform public.mail_worker(key,'record',jsonb_build_object('user_id',uid,'run_id',c->>'run_id','application_id','e5050505-0000-4000-8000-000000000002'));raise exception 'Cross-owner record allowed';exception when no_data_found then null;end;
 perform public.mail_worker(key,'finish',jsonb_build_object('user_id',uid,'run_id',c->>'run_id','status','success','trigger','manual'));
 begin perform public.mail_worker(key,'record',jsonb_build_object('user_id',uid,'run_id',c->>'run_id','application_id',aid));raise exception 'Expired lease wrote';exception when insufficient_privilege then null;end;
end $$;
reset role;
select set_config('request.jwt.claim.sub','e4040404-0000-4000-8000-000000000002',true);
set local role authenticated;
do $$ begin
 if (select count(*) from public.mail_sync_settings)<>0 then raise exception 'Cross-owner settings read';end if;
 if (select count(*) from public.mail_sync_runs)<>0 then raise exception 'Cross-owner runs read';end if;
 if (select count(*) from public.mail_sync_messages)<>0 then raise exception 'Cross-owner messages read';end if;
 begin perform ciphertext from zoin_private.mail_credentials;raise exception 'Credential read allowed';exception when insufficient_privilege then null;end;
 begin update public.mail_sync_settings set enabled=false;raise exception 'Direct settings write allowed';exception when insufficient_privilege then null;end;
end $$;
reset role;
do $$ begin
 if (select count(*) from public.application_events where application_id='e5050505-0000-4000-8000-000000000001' and event_type='application_received')<>1 then raise exception 'Duplicate event';end if;
 if (select count(*) from public.mail_sync_runs where user_id='e4040404-0000-4000-8000-000000000001' and changed_jobs=1)<>1 then raise exception 'Changed count failed';end if;
end $$;
insert into public.mail_sync_runs(user_id,trigger,status,started_at,finished_at) values('e4040404-0000-4000-8000-000000000001','manual','success',now()-interval '8 days',now()-interval '8 days');
insert into public.mail_sync_messages(user_id,application_id,message_key,category,confidence,changed,processed_at) values
('e4040404-0000-4000-8000-000000000001','e5050505-0000-4000-8000-000000000001','old-no-change','other',1,false,now()-interval '8 days');
update public.mail_sync_messages set processed_at=now()-interval '8 days' where message_key='fixture-message';
select public.mail_worker(repeat('fixture-only-',5),'cleanup');
do $$ begin
 if exists(select 1 from public.mail_sync_messages where message_key='old-no-change') then raise exception 'Retention cleanup failed';end if;
 if not exists(select 1 from public.mail_sync_messages where message_key='fixture-message') then raise exception 'Changed log deleted';end if;
 if exists(select 1 from public.mail_sync_runs where user_id='e4040404-0000-4000-8000-000000000001' and changed_jobs=0 and started_at<now()-interval '7 days') then raise exception 'Run cleanup failed';end if;
end $$;
rollback;
select 'PASS: mail sync ownership, leases, receipt deduplication, schedule validation and retention' as result;
