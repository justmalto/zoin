-- Zoin: initial migration for a NEW Supabase project.
-- Apply with the project database owner. No credentials or existing data included.
begin;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create table public.resumes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  file_name text not null,
  file_path text not null unique,
  mime_type text not null check (mime_type in ('application/pdf','text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  content_hash text not null check (content_hash ~ '^[a-f0-9]{64}$'),
  resume_text text not null,
  is_active boolean not null default false,
  created_at timestamptz not null default now(),
  unique(user_id, content_hash), unique(id,user_id),
  check (split_part(file_path, '/', 1) = user_id::text)
);
create unique index resumes_one_active_per_user on public.resumes(user_id) where is_active;
create index resumes_user_created on public.resumes(user_id,created_at desc);
create table public.resume_chunks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  resume_id uuid not null,
  section text not null,
  chunk_index integer not null check (chunk_index >= 0),
  content text not null,
  created_at timestamptz not null default now(),
  foreign key (resume_id,user_id) references public.resumes(id,user_id) on delete cascade,
  unique(resume_id,chunk_index)
);
create index resume_chunks_user on public.resume_chunks(user_id);
create table public.applications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  company text not null check (length(btrim(company)) between 1 and 300),
  job_title text not null check (length(btrim(job_title)) between 1 and 300),
  job_description text not null check (length(btrim(job_description)) > 0),
  application_url text,
  location text,
  job_type text,
  selected_resume_id uuid,
  cover_letter text,
  status text not null default 'draft' check (status in ('draft','applied','assessment','interview','offer','rejected','withdrawn','failed')),
  applied_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(id,user_id),
  foreign key (selected_resume_id,user_id) references public.resumes(id,user_id) on delete set null (selected_resume_id)
);
create index applications_user_updated on public.applications(user_id,updated_at desc);
create table public.application_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  application_id uuid not null,
  from_status text,
  to_status text,
  event_type text not null,
  source text not null,
  note text,
  metadata jsonb not null default '{}'::jsonb,
  happened_at timestamptz not null default now(),
  foreign key (application_id,user_id) references public.applications(id,user_id) on delete cascade
);
create index application_events_timeline on public.application_events(user_id,application_id,happened_at);
create table public.resume_recommendations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  application_id uuid not null,
  resume_id uuid not null,
  match_score double precision not null check (match_score >= 0 and match_score <= 100),
  analysis jsonb not null,
  model_name text not null,
  created_at timestamptz not null default now(),
  foreign key (application_id,user_id) references public.applications(id,user_id) on delete cascade,
  foreign key (resume_id,user_id) references public.resumes(id,user_id) on delete cascade,
  unique(application_id,resume_id)
);
create index recommendations_user on public.resume_recommendations(user_id);

-- Explicit ownership checks for every exposed record, including child records.
do $$
declare t text;
begin
  foreach t in array array['resumes','resume_chunks','applications','resume_recommendations'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on public.%I from anon, authenticated',t);
    execute format('grant select, insert, update, delete on public.%I to authenticated',t);
    execute format('create policy owner_access on public.%I for all to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id)',t);
  end loop;
end $$;
alter table public.profiles enable row level security;
revoke all on public.profiles from anon, authenticated;
grant select on public.profiles to authenticated;
grant update(display_name) on public.profiles to authenticated;
create policy profile_select on public.profiles for select to authenticated using ((select auth.uid())=id);
create policy profile_update on public.profiles for update to authenticated using ((select auth.uid())=id) with check ((select auth.uid())=id);
alter table public.application_events enable row level security;
revoke all on public.application_events from anon, authenticated;
grant select on public.application_events to authenticated;
create policy event_select on public.application_events for select to authenticated using ((select auth.uid())=user_id);

-- Application inserts can only start as drafts, even through direct Data API calls.
drop policy owner_access on public.applications;
create policy application_select on public.applications for select to authenticated using ((select auth.uid())=user_id);
create policy application_insert on public.applications for insert to authenticated with check ((select auth.uid())=user_id and status='draft');
create policy application_update on public.applications for update to authenticated using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id);
create policy application_delete on public.applications for delete to authenticated using ((select auth.uid())=user_id);

-- Restrict status writes to the lifecycle RPC and privileged backend.
revoke insert, update on public.applications from authenticated;
grant insert(status,user_id,company,job_title,job_description,application_url,location,job_type,selected_resume_id,cover_letter) on public.applications to authenticated;
grant update(company,job_title,job_description,application_url,location,job_type,selected_resume_id,cover_letter) on public.applications to authenticated;

create function public.zoin_touch_updated_at() returns trigger language plpgsql set search_path='' as $$
begin new.updated_at=now(); return new; end $$;
create trigger applications_touch before update on public.applications for each row execute function public.zoin_touch_updated_at();
create trigger profiles_touch before update on public.profiles for each row execute function public.zoin_touch_updated_at();

create function public.zoin_create_profile() returns trigger language plpgsql security definer set search_path='' as $$
begin
  insert into public.profiles(id,display_name) values(new.id,coalesce(new.raw_user_meta_data->>'name',''));
  return new;
end $$;
create trigger zoin_user_created after insert on auth.users for each row execute function public.zoin_create_profile();

create function public.activate_resume(p_resume_id uuid) returns public.resumes language plpgsql security definer set search_path='' as $$
declare result public.resumes; uid uuid:=auth.uid();
begin
  if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
  -- Serialize active-resume selection and reject cross-owner IDs.
  perform 1 from public.profiles where id=uid for update;
  if not exists(select 1 from public.resumes where id=p_resume_id and user_id=uid) then
    raise exception 'Resume not found' using errcode='P0002';
  end if;
  update public.resumes set is_active=false where user_id=uid and is_active;
  update public.resumes set is_active=true where id=p_resume_id and user_id=uid returning * into result;
  return result;
end $$;

create function public.transition_application(p_application_id uuid,p_to_status text,p_note text default null,p_source text default 'user') returns public.applications language plpgsql security definer set search_path='' as $$
declare result public.applications; previous text; uid uuid:=auth.uid();
begin
  if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
  if p_source is distinct from 'user' then raise exception 'Invalid source'; end if;
  if length(p_note)>5000 then raise exception 'Note too long'; end if;
  select * into result from public.applications where id=p_application_id and user_id=uid for update;
  if not found then raise exception 'Application not found' using errcode='P0002'; end if;
  previous:=result.status;
  if not coalesce(case previous
    when 'draft' then p_to_status in ('applied','withdrawn')
    when 'applied' then p_to_status in ('assessment','interview','offer','rejected','withdrawn')
    when 'assessment' then p_to_status in ('interview','offer','rejected','withdrawn')
    when 'interview' then p_to_status in ('offer','rejected','withdrawn')
    else false end,false) then raise exception 'Invalid lifecycle transition'; end if;
  update public.applications set status=p_to_status,
    applied_at=case when p_to_status='applied' then coalesce(applied_at,now()) else applied_at end
    where id=p_application_id and user_id=uid returning * into result;
  insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note)
    values(uid,p_application_id,previous,p_to_status,'status_change','user',p_note);
  return result;
end $$;
revoke all on function public.zoin_touch_updated_at(),public.zoin_create_profile(),public.activate_resume(uuid),public.transition_application(uuid,text,text,text) from public,anon,authenticated;
grant execute on function public.activate_resume(uuid),public.transition_application(uuid,text,text,text) to authenticated;

-- Private uploads partitioned by authenticated user's UUID.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('resumes','resumes',false,10485760,array['application/pdf','text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document']);
create policy resume_file_select on storage.objects for select to authenticated
using(bucket_id='resumes' and (storage.foldername(name))[1]=(select auth.uid())::text);
create policy resume_file_insert on storage.objects for insert to authenticated
with check(bucket_id='resumes' and (storage.foldername(name))[1]=(select auth.uid())::text);
create policy resume_file_delete on storage.objects for delete to authenticated
using(bucket_id='resumes' and (storage.foldername(name))[1]=(select auth.uid())::text);
commit;
