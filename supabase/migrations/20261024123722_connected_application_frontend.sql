alter table public.applications add column if not exists job_id text check(length(job_id)<=300);
alter table public.profiles add column if not exists avatar_path text check(avatar_path is null or split_part(avatar_path,'/',1)=id::text);
grant update(job_id,applied_at) on public.applications to authenticated;
grant update(avatar_path) on public.profiles to authenticated;

create or replace function public.create_application(p_payload jsonb)
returns public.applications language plpgsql security definer set search_path='' as $$
declare uid uuid:=auth.uid(); result public.applications; target text:=coalesce(p_payload->>'initial_status','draft'); selected uuid:=nullif(p_payload->>'selected_resume_id','')::uuid;
begin
 if uid is null then raise exception 'Authentication required' using errcode='42501'; end if;
 if target not in ('draft','applied') then raise exception 'Invalid initial status'; end if;
 if selected is not null and not exists(select 1 from public.resumes where id=selected and user_id=uid) then raise exception 'Resume not found' using errcode='P0002'; end if;
 insert into public.applications(user_id,company,job_title,job_description,job_id,application_url,location,job_type,selected_resume_id,cover_letter,status,applied_at)
 values(uid,btrim(p_payload->>'company'),btrim(p_payload->>'job_title'),btrim(p_payload->>'job_description'),nullif(p_payload->>'job_id',''),nullif(p_payload->>'application_url',''),nullif(p_payload->>'location',''),nullif(p_payload->>'job_type',''),selected,p_payload->>'cover_letter',target,
 case when target='applied' then coalesce(nullif(p_payload->>'applied_at','')::timestamptz,now()) else null end)
 returning * into result;
 insert into public.application_events(user_id,application_id,from_status,to_status,event_type,source,note)
 values(uid,result.id,null,target,'created','user','Application created');
 return result;
end $$;
revoke all on function public.create_application(jsonb) from public,anon,authenticated;
grant execute on function public.create_application(jsonb) to authenticated;

create or replace function public.zoin_invalidate_recommendations() returns trigger language plpgsql set search_path='' as $$
begin
 delete from public.resume_recommendations where application_id=new.id and user_id=new.user_id;
 return new;
end $$;
create trigger invalidate_recommendations after update of job_description on public.applications
for each row when (old.job_description is distinct from new.job_description) execute function public.zoin_invalidate_recommendations();
revoke all on function public.zoin_invalidate_recommendations() from public,anon,authenticated;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('avatars','avatars',false,2097152,array['image/png','image/jpeg','image/webp']);
create policy avatar_select on storage.objects for select to authenticated using(bucket_id='avatars' and (storage.foldername(name))[1]=(select auth.uid())::text);
create policy avatar_insert on storage.objects for insert to authenticated with check(bucket_id='avatars' and (storage.foldername(name))[1]=(select auth.uid())::text);
create policy avatar_delete on storage.objects for delete to authenticated using(bucket_id='avatars' and (storage.foldername(name))[1]=(select auth.uid())::text);
