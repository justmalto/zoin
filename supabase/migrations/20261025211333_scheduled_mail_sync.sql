begin;
create schema if not exists zoin_private;
revoke all on schema zoin_private from public,anon,authenticated;
create table zoin_private.mail_worker_config(singleton boolean primary key default true check(singleton),key_hash text not null);
create table zoin_private.mail_credentials(user_id uuid primary key references auth.users on delete cascade,ciphertext text not null);
alter table zoin_private.mail_credentials enable row level security;
alter table zoin_private.mail_worker_config enable row level security;
revoke all on all tables in schema zoin_private from public,anon,authenticated;
create table public.mail_sync_settings(
 user_id uuid primary key references auth.users on delete cascade,email_address text not null,
 interval_days int not null default 1 check(interval_days between 1 and 7),enabled boolean not null default true,
 next_sync_at timestamptz not null default now(),last_synced_at timestamptz,last_attempt_at timestamptz,last_error text,
 active_run_id uuid,lease_until timestamptz,connected_at timestamptz not null default now());
create table public.mail_sync_runs(
 id uuid primary key default gen_random_uuid(),user_id uuid not null references auth.users on delete cascade,
 trigger text not null check(trigger in ('scheduled','manual','job_saved')),started_at timestamptz not null default now(),finished_at timestamptz,
 status text not null default 'running' check(status in ('running','success','partial','failed')),
 checked_jobs int not null default 0,changed_jobs int not null default 0,error_code text);
create index mail_sync_runs_owner_date on public.mail_sync_runs(user_id,started_at desc);
create table public.mail_sync_messages(
 id uuid primary key default gen_random_uuid(),user_id uuid not null references auth.users on delete cascade,
 application_id uuid not null references public.applications on delete cascade,
 message_key text not null,category text not null,confidence double precision not null,
 suggested_status text,changed boolean not null default false,processed_at timestamptz not null default now(),
 unique(application_id,message_key));
create index mail_sync_messages_owner_date on public.mail_sync_messages(user_id,processed_at desc);
create index mail_sync_messages_cleanup on public.mail_sync_messages(processed_at) where not changed;
alter table public.mail_sync_settings enable row level security;
alter table public.mail_sync_runs enable row level security;
alter table public.mail_sync_messages enable row level security;
revoke all on public.mail_sync_settings,public.mail_sync_runs,public.mail_sync_messages from public,anon,authenticated;
grant select on public.mail_sync_settings,public.mail_sync_runs,public.mail_sync_messages to authenticated;
create policy own_mail_settings on public.mail_sync_settings for select to authenticated using((select auth.uid())=user_id);
create policy own_mail_runs on public.mail_sync_runs for select to authenticated using((select auth.uid())=user_id);
create policy own_mail_messages on public.mail_sync_messages for select to authenticated using((select auth.uid())=user_id);
alter table public.applications add column last_mail_synced_at timestamptz;
alter table public.applications add column last_mail_attempt_at timestamptz;
alter table public.applications add column mail_sync_error text;
alter table public.applications add column mail_before_uid bigint;
alter table public.applications add column confirmation_pending boolean not null default false;
create function zoin_private.queue_confirmation() returns trigger language plpgsql set search_path='' as $$
begin
 if new.status='found' then new.confirmation_pending=true;end if;
 return new;
end $$;
create trigger queue_job_confirmation before insert or update of company,job_title,job_id on public.applications for each row execute function zoin_private.queue_confirmation();
-- Only this narrow worker gateway can reach encrypted credentials or write sync metadata.
-- The random server capability is not a Supabase service-role key; only its SHA-256 hash is stored here.
create function public.mail_worker(p_key text,p_action text,p_payload jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' as $$
declare uid uuid:=nullif(p_payload->>'user_id','')::uuid; rid uuid:=nullif(p_payload->>'run_id','')::uuid;
 aid uuid:=nullif(p_payload->>'application_id','')::uuid; result jsonb; settings public.mail_sync_settings; job public.applications;
 item jsonb; k text; changed boolean; previous text; target text; total_changed int:=0;
begin
 if length(coalesce(p_key,''))<40 or not exists(select 1 from zoin_private.mail_worker_config where key_hash=encode(extensions.digest(p_key,'sha256'),'hex')) then
  raise exception 'Worker access denied' using errcode='42501';
 end if;
 if p_action='connect' then
  insert into zoin_private.mail_credentials values(uid,p_payload->>'ciphertext') on conflict(user_id) do update set ciphertext=excluded.ciphertext;
  insert into public.mail_sync_settings(user_id,email_address) values(uid,p_payload->>'email_address')
  on conflict(user_id) do update set email_address=excluded.email_address,last_error=null,connected_at=now();
  return '{}';
 elsif p_action='disconnect' then
  delete from zoin_private.mail_credentials where user_id=uid;
  delete from public.mail_sync_settings where user_id=uid;return '{}';
 elsif p_action='settings' then
  update public.mail_sync_settings set interval_days=(p_payload->>'interval_days')::int,enabled=(p_payload->>'enabled')::boolean,
   next_sync_at=now()+make_interval(days=>(p_payload->>'interval_days')::int) where user_id=uid;return '{}';
 elsif p_action='due' then
  select coalesce(jsonb_agg(user_id),'[]') into result from (select s.user_id from public.mail_sync_settings s
   where (lease_until is null or lease_until<now()) and ((enabled and next_sync_at<=now()) or exists(select 1 from public.applications a where a.user_id=s.user_id and a.confirmation_pending and (a.last_mail_attempt_at is null or a.last_mail_attempt_at<now()-interval '15 minutes')))
   order by next_sync_at limit 10) q;return result;
 elsif p_action='claim' then
  select * into settings from public.mail_sync_settings where user_id=uid for update;
  if not found then return jsonb_build_object('state','disconnected');end if;
  if settings.lease_until>now() then return jsonb_build_object('state','busy');end if;
  -- Recover an interrupted worker, retaining a visible failure rather than an eternal Running row.
  update public.mail_sync_runs set status='failed',finished_at=now(),error_code='worker_interrupted' where id=settings.active_run_id and status='running';
  insert into public.mail_sync_runs(user_id,trigger) values(uid,p_payload->>'trigger') returning id into rid;
  update public.mail_sync_settings set active_run_id=rid,lease_until=now()+interval '30 minutes',last_attempt_at=now() where user_id=uid;
  select jsonb_build_object('state','claimed','run_id',rid,'email_address',settings.email_address,'ciphertext',c.ciphertext) into result from zoin_private.mail_credentials c where c.user_id=uid;return result;
 elsif p_action='jobs' then
  select coalesce(jsonb_agg(to_jsonb(q)),'[]') into result from (select a.* from public.applications a
   where a.user_id=uid and ((aid is not null and a.id=aid and a.status in ('found','applied')) or
    (aid is null and ((a.status='applied' and a.receipt_confirmed_at is not null) or (a.status='found' and (p_payload->>'trigger'='manual' or a.confirmation_pending)))))
   order by a.last_mail_attempt_at nulls first,a.created_at limit 20) q;return result;
 elsif p_action='record' then
  perform 1 from public.mail_sync_settings where user_id=uid and active_run_id=rid and lease_until>now() for update;
  if not found then raise exception 'Sync lease expired' using errcode='42501';end if;
  select * into job from public.applications where id=aid and user_id=uid for update;
  if not found then raise exception 'Application not found' using errcode='P0002';end if;
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
   insert into public.mail_sync_messages(user_id,application_id,message_key,category,confidence,suggested_status,changed)
   values(uid,aid,k,item->>'category',(item->>'confidence')::float,target,changed);
   if changed then
    total_changed:=total_changed+1;
    insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note,metadata)
    values(uid,aid,previous,job.status,'application_received','email','Application-received email matched during sync',jsonb_build_object('message_key',k,'sync_run_id',rid));
   end if;
  end loop;
  update public.applications set last_mail_attempt_at=now(),mail_sync_error=p_payload->>'error_code',
   last_mail_synced_at=case when p_payload->>'error_code' is null then now() else last_mail_synced_at end,
   mail_before_uid=case when p_payload->>'error_code' is null then nullif(p_payload->>'before_uid','')::bigint else mail_before_uid end,
   confirmation_pending=case when p_payload->>'error_code' is null then false else confirmation_pending end where id=aid returning * into job;
  update public.mail_sync_runs set checked_jobs=checked_jobs+1,changed_jobs=changed_jobs+total_changed where id=rid and user_id=uid;
  update public.mail_sync_settings set lease_until=now()+interval '30 minutes' where user_id=uid and active_run_id=rid;
  return to_jsonb(job);
 elsif p_action='finish' then
  select * into settings from public.mail_sync_settings where user_id=uid and active_run_id=rid for update;
  if not found then return '{}';end if;
  update public.mail_sync_runs set status=p_payload->>'status',finished_at=now(),error_code=p_payload->>'error_code' where id=rid and user_id=uid;
  update public.mail_sync_settings set active_run_id=null,lease_until=null,last_error=p_payload->>'error_code',
   last_synced_at=case when p_payload->>'status'='success' then now() else last_synced_at end,
   next_sync_at=case when p_payload->>'trigger'='job_saved' then next_sync_at else now()+make_interval(days=>interval_days) end where user_id=uid;
  return '{}';
 elsif p_action='cleanup' then
  delete from public.mail_sync_messages where not changed and processed_at<now()-interval '7 days';
  delete from public.mail_sync_runs where changed_jobs=0 and started_at<now()-interval '7 days' and status<>'running';return '{}';
 else raise exception 'Unknown worker operation';end if;
end $$;
revoke all on function public.mail_worker(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.mail_worker(text,text,jsonb) to anon;
commit;
