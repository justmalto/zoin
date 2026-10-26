begin;
create or replace function public.mail_worker(p_key text,p_action text,p_payload jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' as $$
declare uid uuid:=nullif(p_payload->>'user_id','')::uuid; rid uuid:=nullif(p_payload->>'run_id','')::uuid;
 aid uuid:=nullif(p_payload->>'application_id','')::uuid; result jsonb; settings public.mail_sync_settings; job public.applications;
 item jsonb; k text; changed boolean; previous text; target text; total_changed int:=0; full_run boolean:=false;
begin
 if length(coalesce(p_key,''))<40 or not exists(select 1 from zoin_private.mail_worker_config where key_hash=encode(extensions.digest(p_key,'sha256'),'hex')) then
  raise exception 'Worker access denied' using errcode='42501';
 end if;
 if p_action='connect' then
  select * into settings from public.mail_sync_settings where user_id=uid for update;
  if settings.email_address is distinct from p_payload->>'email_address' then
   update public.applications set mail_before_uid=null,last_mail_synced_at=null,last_mail_attempt_at=null,mail_sync_error=null,confirmation_pending=(status='found') where user_id=uid;
  end if;
  update public.mail_sync_runs set status='failed',finished_at=now(),error_code='connection_changed' where id=(select active_run_id from public.mail_sync_settings where user_id=uid) and status='running';
  insert into zoin_private.mail_credentials values(uid,p_payload->>'ciphertext') on conflict(user_id) do update set ciphertext=excluded.ciphertext;
  insert into public.mail_sync_settings(user_id,email_address) values(uid,p_payload->>'email_address')
  on conflict(user_id) do update set last_synced_at=case when public.mail_sync_settings.email_address is distinct from excluded.email_address then null else public.mail_sync_settings.last_synced_at end,email_address=excluded.email_address,last_error=null,connected_at=now(),active_run_id=null,lease_until=null,active_full_cycle=false,cycle_started_at=null,cycle_include_found=false;
  return '{}';
 elsif p_action='disconnect' then
  delete from zoin_private.mail_credentials where user_id=uid;
  delete from public.mail_sync_settings where user_id=uid;return '{}';
 elsif p_action='settings' then
  update public.mail_sync_settings set interval_days=(p_payload->>'interval_days')::int,enabled=(p_payload->>'enabled')::boolean,
   next_sync_at=now()+make_interval(days=>(p_payload->>'interval_days')::int) where user_id=uid;return '{}';
 elsif p_action='due' then
  select coalesce(jsonb_agg(user_id),'[]') into result from (select s.user_id from public.mail_sync_settings s
   where (lease_until is null or lease_until<now()) and ((active_run_id is not null and lease_until<now()) or (enabled and next_sync_at<=now()) or exists(select 1 from public.applications a where a.user_id=s.user_id and a.confirmation_pending and (a.last_mail_attempt_at is null or a.last_mail_attempt_at<now()-interval '15 minutes')))
   order by next_sync_at limit 10) q;return result;
 elsif p_action='claim' then
  select * into settings from public.mail_sync_settings where user_id=uid for update;
  if not found then return jsonb_build_object('state','disconnected');end if;
  if settings.lease_until>now() then return jsonb_build_object('state','busy');end if;
  -- Recover an interrupted worker, retaining a visible failure rather than an eternal Running row.
  update public.mail_sync_runs set status='failed',finished_at=now(),error_code='worker_interrupted' where id=settings.active_run_id and status='running';
  insert into public.mail_sync_runs(user_id,trigger) values(uid,p_payload->>'trigger') returning id into rid;
  full_run:=aid is null and (p_payload->>'trigger'='manual' or (p_payload->>'trigger'='scheduled' and settings.enabled and (settings.next_sync_at<=now() or settings.cycle_started_at is not null)));
  update public.mail_sync_settings set active_run_id=rid,lease_until=now()+interval '30 minutes',last_attempt_at=now(),active_full_cycle=full_run,
   cycle_started_at=case when full_run then case when p_payload->>'trigger'='manual' then now() else coalesce(cycle_started_at,now()) end else cycle_started_at end,
   cycle_include_found=case when full_run and p_payload->>'trigger'='manual' then true else cycle_include_found end where user_id=uid;
  select jsonb_build_object('state','claimed','run_id',rid,'email_address',settings.email_address,'ciphertext',c.ciphertext) into result from zoin_private.mail_credentials c where c.user_id=uid;return result;
 elsif p_action='jobs' then
  select * into settings from public.mail_sync_settings where user_id=uid;
  select coalesce(jsonb_agg(to_jsonb(q)),'[]') into result from (select a.* from public.applications a
   where a.user_id=uid and ((aid is not null and a.id=aid and a.status in ('found','applied')) or
    (aid is null and ((a.status='found' and a.confirmation_pending) or
     (settings.active_full_cycle and ((a.status='applied' and a.receipt_confirmed_at is not null) or (a.status='found' and settings.cycle_include_found))
      and (a.last_mail_attempt_at is null or a.last_mail_attempt_at<settings.cycle_started_at or a.mail_before_uid is not null)))))
   order by a.last_mail_attempt_at nulls first,a.created_at limit 21) q;return result;
 elsif p_action='record' then
  perform 1 from public.mail_sync_settings where user_id=uid and active_run_id=rid and lease_until>now() for update;
  if not found then raise exception 'Sync lease expired' using errcode='42501';end if;
  select * into job from public.applications where id=aid and user_id=uid for update;
  if not found then raise exception 'Application not found' using errcode='P0002';end if;
  if job.company is distinct from p_payload->>'expected_company' or job.job_title is distinct from p_payload->>'expected_title' or job.job_id is distinct from p_payload->>'expected_job_id' then
   raise exception 'Job changed during sync';
  end if;
  for item in select value from jsonb_array_elements(coalesce(p_payload->'matches','[]')) loop
   k:=item->>'message_key';changed:=false;previous:=job.status;target:=item->>'suggested_status';
   if exists(select 1 from public.mail_sync_messages where application_id=aid and message_key=k) then continue;end if;
   if job.status='found' and target='applied' and (item->>'receipt_valid')::boolean
    and not ((item->>'message_uid')::bigint=any(job.ignored_receipt_uids))
    and not (coalesce(item->>'message_id','')<>'' and item->>'message_id'=any(job.ignored_receipt_ids)) then
    update public.applications set status='applied',receipt_confirmed_at=now(),receipt_message_uid=(item->>'message_uid')::bigint,
     receipt_message_id=item->>'message_id',receipt_evidence=item->'evidence',applied_at=coalesce(submitted_at,now()) where id=aid returning * into job;changed:=true;
   end if;
   -- Further updates remain reviewable suggestions. Scheduled eligibility is exactly Applied.
   insert into public.mail_sync_messages(user_id,application_id,message_key,category,confidence,suggested_status,changed,sender,subject,interview_details,deadline_text)
   values(uid,aid,k,item->>'category',(item->>'confidence')::float,target,changed,left(item->>'sender',300),left(item->>'subject',500),left(item->>'interview_details',1000),left(item->>'deadline_text',1000));
   if changed then
    total_changed:=total_changed+1;
    insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note,metadata)
    values(uid,aid,previous,job.status,'application_received','email','Application-received email matched during sync',jsonb_build_object('message_key',k,'sync_run_id',rid));
   end if;
  end loop;
  update public.applications set last_mail_attempt_at=now(),mail_sync_error=p_payload->>'error_code',
   last_mail_synced_at=case when p_payload->>'error_code' is null then now() else last_mail_synced_at end,
   mail_before_uid=case when p_payload->>'error_code' is null then nullif(p_payload->>'before_uid','')::bigint else mail_before_uid end,
   confirmation_pending=case when p_payload->>'error_code' is null then (status='found' and nullif(p_payload->>'before_uid','') is not null) else confirmation_pending end where id=aid returning * into job;
  update public.mail_sync_runs set checked_jobs=checked_jobs+1,changed_jobs=changed_jobs+total_changed where id=rid and user_id=uid;
  update public.mail_sync_settings set lease_until=now()+interval '30 minutes' where user_id=uid and active_run_id=rid;
  return to_jsonb(job);
 elsif p_action='finish' then
  select * into settings from public.mail_sync_settings where user_id=uid and active_run_id=rid for update;
  if not found then return '{}';end if;
  update public.mail_sync_runs set status=p_payload->>'status',finished_at=now(),error_code=p_payload->>'error_code' where id=rid and user_id=uid;
  update public.mail_sync_settings set active_run_id=null,lease_until=null,active_full_cycle=false,last_error=p_payload->>'error_code',
   last_synced_at=case when settings.active_full_cycle and p_payload->>'status'='success' then now() else last_synced_at end,
   cycle_started_at=case when settings.active_full_cycle and p_payload->>'status'<>'partial' then null else cycle_started_at end,
   cycle_include_found=case when settings.active_full_cycle and p_payload->>'status'<>'partial' then false else cycle_include_found end,
   next_sync_at=case when not settings.active_full_cycle then next_sync_at when p_payload->>'status'='partial' then now()+interval '15 minutes' else now()+make_interval(days=>interval_days) end where user_id=uid;
  return '{}';
 elsif p_action='cleanup' then
  update public.mail_sync_runs r set status='failed',finished_at=now(),error_code='worker_interrupted' where r.status='running' and r.started_at<now()-interval '30 minutes' and not exists(select 1 from public.mail_sync_settings s where s.active_run_id=r.id and s.lease_until>now());
  delete from public.mail_sync_messages m where not m.changed and m.processed_at<now()-interval '7 days';
  delete from public.mail_sync_runs where changed_jobs=0 and started_at<now()-interval '7 days' and status<>'running';return '{}';
 else raise exception 'Unknown worker operation';end if;
end $$;

commit;
