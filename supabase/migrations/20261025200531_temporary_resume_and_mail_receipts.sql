begin;
-- The library cap is enforced even for concurrent/direct Data API inserts.
create function public.zoin_resume_library_limit() returns trigger language plpgsql security definer set search_path='' as $$
begin
 perform 1 from public.profiles where id=new.user_id for update;
 if (select count(*) from public.resumes where user_id=new.user_id)>=10 then
  raise exception 'Saved resume limit reached (10). Replace an existing resume.' using errcode='P0001';
 end if;
 return new;
end $$;
create trigger resume_library_limit before insert on public.resumes for each row execute function public.zoin_resume_library_limit();
revoke all on function public.zoin_resume_library_limit() from public,anon,authenticated;

create function public.replace_resume(p_resume_id uuid,p_payload jsonb,p_chunks jsonb) returns public.resumes
language plpgsql security definer set search_path='' as $$
declare uid uuid:=auth.uid(); result public.resumes;
begin
 if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
 select * into result from public.resumes where id=p_resume_id and user_id=uid for update;
 if not found then raise exception 'Resume not found' using errcode='P0002'; end if;
 update public.resumes set file_name=p_payload->>'file_name',file_path=p_payload->>'file_path',mime_type=p_payload->>'mime_type',
 content_hash=p_payload->>'content_hash',resume_text=p_payload->>'resume_text' where id=p_resume_id and user_id=uid returning * into result;
 delete from public.resume_chunks where resume_id=p_resume_id and user_id=uid;
 insert into public.resume_chunks(resume_id,user_id,section,chunk_index,content)
 select p_resume_id,uid,x.section,x.chunk_index,x.content from jsonb_to_recordset(p_chunks) as x(section text,chunk_index integer,content text);
 delete from public.resume_recommendations where resume_id=p_resume_id and user_id=uid;
 return result;
end $$;
revoke all on function public.replace_resume(uuid,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.replace_resume(uuid,jsonb,jsonb) to authenticated;

alter table public.applications add column submitted_at timestamptz;
alter table public.applications add column receipt_confirmed_at timestamptz;
alter table public.applications add column receipt_message_uid bigint;
alter table public.applications add column receipt_message_id text;
alter table public.applications add column receipt_evidence jsonb;
alter table public.applications add column ignored_receipt_uids bigint[] not null default '{}';
alter table public.applications add column ignored_receipt_ids text[] not null default '{}';
alter table public.applications drop constraint applications_status_check;
alter table public.applications add constraint applications_status_check check(status in ('found','draft','applied','assessment','interview','offer','rejected','withdrawn','failed'));
alter table public.applications alter column status set default 'found';
-- Retain the previously entered date and old timeline rather than claiming it was email-confirmed.
update public.applications set submitted_at=applied_at where applied_at is not null;
insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note)
select user_id,id,status,'found','status_change','migration','Found: no application-received email has been confirmed. Previous submission date retained.' from public.applications where status in ('draft','applied');
update public.applications set status='found',applied_at=null where status in ('draft','applied');
drop policy application_insert on public.applications;
create policy application_insert on public.applications for insert to authenticated with check((select auth.uid())=user_id and status='found');
revoke update(applied_at) on public.applications from authenticated;
grant update(submitted_at) on public.applications to authenticated;

create or replace function public.create_application(p_payload jsonb) returns public.applications language plpgsql security definer set search_path='' as $$
declare uid uuid:=auth.uid(); result public.applications; selected uuid:=nullif(p_payload->>'selected_resume_id','')::uuid;
begin
 if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
 if coalesce(p_payload->>'initial_status','found')<>'found' then raise exception 'New jobs must start as Found'; end if;
 if selected is not null and not exists(select 1 from public.resumes where id=selected and user_id=uid) then raise exception 'Resume not found' using errcode='P0002'; end if;
 insert into public.applications(user_id,company,job_title,job_description,job_id,application_url,location,job_type,selected_resume_id,cover_letter,status,submitted_at)
 values(uid,btrim(p_payload->>'company'),btrim(p_payload->>'job_title'),btrim(p_payload->>'job_description'),nullif(p_payload->>'job_id',''),nullif(p_payload->>'application_url',''),nullif(p_payload->>'location',''),nullif(p_payload->>'job_type',''),selected,p_payload->>'cover_letter','found',nullif(p_payload->>'submitted_at','')::timestamptz) returning * into result;
 insert into public.application_events(user_id,application_id,to_status,event_type,source,note) values(uid,result.id,'found','created','user','Job found; awaiting an application-received email');
 return result;
end $$;

create or replace function public.transition_application(p_application_id uuid,p_to_status text,p_note text default null,p_source text default 'user') returns public.applications language plpgsql security definer set search_path='' as $$
declare result public.applications; previous text; uid uuid:=auth.uid();
begin
 if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
 if p_source is distinct from 'user' or length(p_note)>5000 then raise exception 'Invalid transition'; end if;
 select * into result from public.applications where id=p_application_id and user_id=uid for update;
 if not found then raise exception 'Application not found' using errcode='P0002'; end if;
 previous:=result.status;
 if not coalesce(case previous
  when 'found' then p_to_status in ('assessment','interview','offer','rejected','withdrawn')
  when 'draft' then p_to_status in ('found','withdrawn')
  when 'applied' then p_to_status in ('assessment','interview','offer','rejected','withdrawn')
  when 'assessment' then p_to_status in ('interview','offer','rejected','withdrawn')
  when 'interview' then p_to_status in ('offer','rejected','withdrawn') else false end,false) then raise exception 'Invalid lifecycle transition'; end if;
 update public.applications set status=p_to_status where id=p_application_id and user_id=uid returning * into result;
 insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note) values(uid,p_application_id,previous,p_to_status,'status_change','user',p_note);
 return result;
end $$;

-- These owner-scoped RPCs record personal tracker state; email identity/evidence is checked by the API.
create function public.apply_application_receipt(p_application_id uuid,p_uid bigint,p_message_id text,p_evidence jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare uid uuid:=auth.uid(); result public.applications; previous text; changed boolean:=false;
begin
 if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
 if p_uid<1 or p_evidence is null or jsonb_typeof(p_evidence)<>'array' or jsonb_array_length(p_evidence)=0 then raise exception 'Receipt evidence required'; end if;
 select * into result from public.applications where id=p_application_id and user_id=uid for update;
 if not found then raise exception 'Application not found' using errcode='P0002'; end if;
 if result.receipt_confirmed_at is not null or p_uid=any(result.ignored_receipt_uids) or (coalesce(p_message_id,'')<>'' and p_message_id=any(result.ignored_receipt_ids)) then
  return jsonb_build_object('application',to_jsonb(result),'updated',false);
 end if;
 previous:=result.status; changed:=previous in ('found','draft');
 update public.applications set receipt_confirmed_at=now(),receipt_message_uid=p_uid,receipt_message_id=p_message_id,receipt_evidence=p_evidence,
 status=case when changed then 'applied' else status end,applied_at=coalesce(applied_at,submitted_at,now()) where id=p_application_id and user_id=uid returning * into result;
 insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note,metadata)
 values(uid,p_application_id,previous,result.status,'application_received','email','Application-received email matched automatically',jsonb_build_object('message_uid',p_uid,'message_id',p_message_id,'evidence',p_evidence));
 return jsonb_build_object('application',to_jsonb(result),'updated',changed);
end $$;
create function public.correct_application_receipt(p_application_id uuid) returns public.applications language plpgsql security definer set search_path='' as $$
declare uid uuid:=auth.uid(); result public.applications; previous text;
begin
 if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
 select * into result from public.applications where id=p_application_id and user_id=uid for update;
 if not found then raise exception 'Application not found' using errcode='P0002'; end if;
 previous:=result.status;
 update public.applications set ignored_receipt_uids=case when receipt_message_uid is null then ignored_receipt_uids else array_append(ignored_receipt_uids,receipt_message_uid) end,
 ignored_receipt_ids=case when coalesce(receipt_message_id,'')='' then ignored_receipt_ids else array_append(ignored_receipt_ids,receipt_message_id) end,
 receipt_confirmed_at=null,receipt_message_uid=null,receipt_message_id=null,receipt_evidence=null,applied_at=null,
 status=case when status='applied' then 'found' else status end where id=p_application_id and user_id=uid returning * into result;
 insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note) values(uid,p_application_id,previous,result.status,'receipt_corrected','user','Incorrect automatic email confirmation removed; this message will be ignored');
 return result;
end $$;
revoke all on function public.apply_application_receipt(uuid,bigint,text,jsonb),public.correct_application_receipt(uuid) from public,anon,authenticated;
grant execute on function public.apply_application_receipt(uuid,bigint,text,jsonb),public.correct_application_receipt(uuid) to authenticated;
commit;
