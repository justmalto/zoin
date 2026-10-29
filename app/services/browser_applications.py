"""Read public ATS data; fill actual hosted forms through an isolated browser.

Every HTTP connection is DNS-validated and pinned. Inspection cannot POST.
No credentials, CAPTCHA solving, employer API keys, or browser sessions persist.
"""
import asyncio
from types import SimpleNamespace
import http.client
import json
import re
import socket
import ssl
import time
from contextlib import asynccontextmanager
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit, parse_qs, urljoin, quote, urlencode
from html.parser import HTMLParser
from fastapi import HTTPException
from services.job_link import public_target, fetch_posting
from services.playwright_apply import LocalMockAdapter, configure_browser_cache


BROWSERS=BoundedSemaphore(1)

class FormBlocked(Exception):
    def __init__(self,code,message):
        self.code=code;self.message=message
        super().__init__(message)


def greenhouse_host(host):
    return host in ('boards.greenhouse.io','job-boards.greenhouse.io','boards-api.greenhouse.io') or bool(host and host.endswith('.greenhouse.io'))


def greenhouse_reference(url):
    p=urlsplit(url)
    if not greenhouse_host(p.hostname):return None
    query=parse_qs(p.query)
    if query.get('for') and query.get('token'):
        board,identifier=query['for'][0],query['token'][0]
    else:
        match=re.fullmatch(r'/([^/]+)/jobs/(\d+)/?',p.path)
        if not match:return None
        board,identifier=match.groups()
    return (board,identifier) if re.fullmatch(r'[A-Za-z0-9_-]{1,100}',board) and identifier.isdigit() else None


def public_questions(url):
    ref=greenhouse_reference(url)
    if not ref:return {}
    try:
        data,_=fetch_posting(f'https://boards-api.greenhouse.io/v1/boards/{quote(ref[0])}/jobs/{ref[1]}?questions=true',json_response=True)
        questions=json.loads(data).get('questions',[])
        return {field['name']:question for question in questions for field in question.get('fields',[]) if isinstance(field.get('name'),str)}
    except Exception:return {}  # Rendered employer fields remain authoritative.


def resolved_form_url(url):
    """Resolve documented Greenhouse embeds from an observed board script + job ID."""
    ref=greenhouse_reference(url)
    if not ref:
        p=urlsplit(url);query=parse_qs(p.query)
        identifier=(query.get('gh_jid') or [None])[0]
        if not identifier:
            match=re.search(r'/jobs/(\d+)/?$',p.path)
            identifier=match.group(1) if match else None
        if not identifier or not identifier.isdigit():return url
        class Sources(HTMLParser):
            board=None
            def handle_starttag(self,tag,attrs):
                values=dict(attrs);src=values.get('src') or values.get('href') or ''
                target=urlsplit(urljoin(url,src))
                if greenhouse_host(target.hostname) and target.path=='/embed/job_board/js':
                    self.board=(parse_qs(target.query).get('for') or [None])[0]
        try:
            html,_=fetch_posting(url);parser=Sources();parser.feed(html)
            board=parser.board
            if not board or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',board):return url
            payload,_=fetch_posting(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{identifier}?questions=true',json_response=True)
            if str(json.loads(payload).get('id'))!=identifier:return url
            ref=(board,identifier)
        except Exception:return url
    params={'for':ref[0],'token':ref[1]}
    source=(parse_qs(urlsplit(url).query).get('gh_src') or [None])[0]
    if source and len(source)<=200:params['gh_src']=source
    return 'https://job-boards.greenhouse.io/embed/job_app?'+urlencode(params)


class PublicTransport:
    """The browser cannot resolve hosts itself; all HTTP uses this pinned transport."""
    def __init__(self,seconds=75):
        self.deadline=time.monotonic()+seconds;self.count=0;self.bytes=0
        self.allow_writes=False;self.allow_uploads=False;self.form_filling=False;self.form_origin=None;self.greenhouse=False;self.failure=None;self.error_type=None;self.targets={};self.network_slots=asyncio.Semaphore(4);self.state_lock=Lock()

    def write_allowed(self,url,method):
        p=urlsplit(url)
        captcha=p.hostname in ('www.google.com','www.recaptcha.net') and p.path.startswith('/recaptcha/')
        signed_upload=bool(p.hostname and p.hostname.endswith('.amazonaws.com') and 'X-Amz-Signature=' in p.query)
        provider=greenhouse_host(p.hostname) or captcha or (p.hostname=='maps.googleapis.com' and p.path.startswith('/maps/'))
        same_origin=(p.scheme,p.netloc)==self.form_origin
        if method in ('GET','HEAD','OPTIONS'):
            return not self.form_filling or (provider or signed_upload if self.greenhouse else same_origin)
        if self.greenhouse and self.form_filling and captcha:return True
        if self.allow_uploads and self.greenhouse:
            if method=='PUT' and signed_upload:return True
            if greenhouse_host(p.hostname) and re.search(r'/(?:uploads?|attachments?|files?)(?:/|$)',p.path):return True
        if not self.allow_writes:return False
        return provider if self.greenhouse else same_origin

    def handle(self,route):
        request=route.request
        connection=None
        try:
            if len(request.url)>2000:raise FormBlocked('REQUEST_TOO_LARGE','The site generated a request exceeding the allowed URL size.')
            if request.url.startswith(('data:','blob:')):route.continue_();return
            if request.resource_type in ('image','media','font','eventsource'):route.abort();return
            if not self.write_allowed(request.url,request.method):route.abort();return
            with self.state_lock:self.count+=1
            if self.count>180 or self.bytes>35_000_000 or time.monotonic()>self.deadline:raise FormBlocked('SITE_TIMEOUT','The site exceeded the browser time or page-size limit.')
            parsed=urlsplit(request.url);key=(parsed.scheme,parsed.netloc)
            if key not in self.targets:
                host,ip,url=public_target(request.url);self.targets[key]=(host,ip)
            else:
                host,ip=self.targets[key];url=request.url
            remaining=max(1,min(12,self.deadline-time.monotonic()))
            connection=http.client.HTTPSConnection(host,timeout=remaining,context=ssl.create_default_context())
            connection._create_connection=lambda address,timeout,source_address=None:socket.create_connection((ip,443),timeout,source_address)
            headers={k:v for k,v in request.all_headers().items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
            headers['Accept-Encoding']='identity'
            body=request.post_data_buffer
            if body and len(body)>12*1024*1024:raise FormBlocked('UPLOAD_TOO_LARGE','The application upload exceeds the allowed size.')
            p=urlsplit(url)
            connection.request(request.method,p.path+('?' +p.query if p.query else ''),body=body,headers=headers)
            response=connection.getresponse();chunks=bytearray();response_deadline=min(self.deadline,time.monotonic()+15)
            while True:
                if time.monotonic()>response_deadline:raise FormBlocked('SITE_TIMEOUT','A site resource did not finish responding within the browser time limit.')
                chunk=response.read1(min(65536,8_000_001-len(chunks)))
                if not chunk:break
                chunks.extend(chunk)
                if len(chunks)>8_000_000:break
            payload=bytes(chunks)
            if len(payload)>8_000_000:raise FormBlocked('PAGE_TOO_LARGE','A site resource exceeds the browser size limit.')
            with self.state_lock:self.bytes+=len(payload)
            if self.bytes>35_000_000:raise FormBlocked('PAGE_TOO_LARGE','The job site exceeded the total browser page-size limit.')
            outgoing={}
            for key,value in response.getheaders():
                key=key.lower()
                if key in ('content-length','transfer-encoding','connection'):continue
                outgoing[key]=outgoing[key]+'\n'+value if key in outgoing and key=='set-cookie' else value
            # Preserve Content-Encoding: the browser decodes the original response bytes.
            route.fulfill(status=response.status,headers=outgoing,body=payload)
        except Exception as error:
            self.error_type=type(error).__name__
            self.failure=error if isinstance(error,FormBlocked) else FormBlocked('SITE_ACCESS_BLOCKED','A site request could not be completed securely. The host may be unavailable or blocked.')
            try:route.abort()
            except Exception:pass
        finally:
            if connection:connection.close()


    async def handle_async(self,route):
        # Read Playwright objects on their event loop; only socket I/O runs in threads.
        request=route.request
        headers=await request.all_headers()
        snapshot=SimpleNamespace(url=request.url,method=request.method,resource_type=request.resource_type,
            post_data_buffer=request.post_data_buffer,all_headers=lambda:headers)
        class BufferedRoute:
            action='abort';payload={}
            def __init__(self):self.request=snapshot
            def abort(self):self.action='abort'
            def continue_(self):self.action='continue'
            def fulfill(self,**payload):self.action='fulfill';self.payload=payload
        buffered=BufferedRoute()
        async with self.network_slots:
            await asyncio.to_thread(self.handle,buffered)
        try:
            if buffered.action=='fulfill':await route.fulfill(**buffered.payload)
            elif buffered.action=='continue':await route.continue_()
            else:await route.abort()
        except Exception:
            try:await route.abort()
            except Exception:pass


FIELD_SCRIPT=r'''form => Array.from(form.querySelectorAll('input,select,textarea')).filter(el => {
 if(el.type==='file')return true; const style=getComputedStyle(el);const rect=el.getBoundingClientRect();if(!el.id&&!el.name&&(el.getAttribute('aria-hidden')==='true'||style.opacity==='0'||rect.width<=1||rect.height<=1))return false;return el.type!=='hidden' && !['submit','button','reset'].includes(el.type) && style.display!=='none' && style.visibility!=='hidden' && (el.type==='file'||el.getClientRects().length);
}).map(el=>{
 const label=el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby')||'').split(' ').map(id=>document.getElementById(id)?.textContent||'').join(' ').trim() || Array.from(el.labels||[]).map(l=>l.textContent.replace(el.textContent,'')).join(' ').trim() || el.closest('fieldset')?.querySelector('legend')?.textContent || '';
 return {key:el.id||el.name,label:label.trim().replace(/\s+/g,' '),name:el.name,id:el.id,kind:el.tagName==='SELECT'?'select':el.type||'text',combo:el.getAttribute('role')==='combobox',required:el.required||el.getAttribute('aria-required')==='true'||/\*/.test(label),options:el.tagName==='SELECT'?Array.from(el.options).filter(o=>o.value).map(o=>({value:o.value,label:o.textContent.trim()})):[],accept:el.accept||''};
})'''


def base_answer(field,applicant):
    key=(field.get('name') or field['key']).casefold()
    if key=='email':return applicant.get('email')
    if key in ('name','full_name'):return applicant.get('name')
    return None



def question_definitions(fields,job,metadata=None):
    questions=[];seen=set()
    for field in fields:
        key=field['key'];kind=field['kind'];label=field['label']
        if not key and not field['required']:continue
        if not key or key in seen:raise FormBlocked('UNSUPPORTED_FIELD','The form has missing or ambiguous field identifiers; manual completion is needed.')
        seen.add(key)
        if kind=='file':
            if field['required'] and not re.search(r'resume|résumé|\bcv\b',key+' '+label,re.I):raise FormBlocked('ADDITIONAL_DOCUMENT_REQUIRED','The employer requires another document, such as a cover letter. Complete that upload manually.')
            continue
        if not field['required']:continue
        if re.search(r'password|passcode|one.time|verification code',label+' '+key,re.I):raise FormBlocked('LOGIN_REQUIRED','This application requires sign-in or verification that is not configured for the agent. Complete it manually on the employer site.')
        if kind in ('checkbox','radio'):
            raise FormBlocked('CONSENT_OR_CUSTOM_CONTROL','The form requires a consent checkbox or a custom choice control that needs manual completion.')
        if not label:raise FormBlocked('UNLABELLED_FIELD','A required field has no readable label. The agent cannot safely infer its meaning.')
        options=[o['label'] for o in field['options']]
        if field['combo'] and metadata:
            item=metadata.get(field.get('name')) or metadata.get(key) or {}
            options=[str(v['label']) for f in item.get('fields',[]) for v in f.get('values',[]) if isinstance(v.get('label'),str)]
        if len(options)>400:raise FormBlocked('TOO_MANY_OPTIONS','A required choice field exceeds the supported option limit. Complete it manually.')
        questions.append({'key':key,'label':label,'type':'select' if options else 'text','options':options,'required':True,
                          'context':{'country':job.get('location') or 'unspecified','employer':job['company'],'form_url':job['url']}})
    if len(questions)>50:raise FormBlocked('FORM_TOO_LONG','This application exceeds the supported question limit. Complete it manually.')
    return questions


class LiveBrowserAdapter:
    def supports(self,job):
        try:
            p=urlsplit(job['url'])
            return p.scheme=='https' and bool(p.hostname) and not p.username and not p.password and p.port in (None,443)
        except (ValueError,KeyError):return False

    def preflight(self):LocalMockAdapter('http://127.0.0.1:1').preflight()

    @asynccontextmanager
    async def session(self):
        if not BROWSERS.acquire(blocking=False):raise FormBlocked('BROWSER_BUSY','Another application form is being checked. Retry shortly.')
        try:
            configure_browser_cache()
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser=await p.chromium.launch(headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND','--disable-background-networking','--disable-quic','--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=<-loopback>','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
                context=await browser.new_context(accept_downloads=False,service_workers='block')
                transport=PublicTransport();await context.route('**/*',transport.handle_async)
                async def close_socket(ws):await ws.close()
                await context.route_web_socket('**/*',close_socket)
                page=await context.new_page();page.set_default_timeout(12000)
                try:yield page,transport
                finally:
                    await context.unroute_all(behavior='wait')
                    await browser.close()
        finally:BROWSERS.release()

    async def challenge(self,page):
        try:text=await page.locator('body').inner_text(timeout=8000)
        except Exception:raise FormBlocked('FORM_LOADING_TIMEOUT','The job site did not finish rendering its application page within the time limit. Retry inspection or open the link manually.') from None
        if re.search(r'verify (?:that )?you are (?:a )?human|complete (?:the )?(?:captcha|security check)|access denied|checking your browser',text,re.I):
            raise FormBlocked('CAPTCHA_OR_ACCESS_CHECK','The site requires a CAPTCHA or security check. Complete this application manually on the employer site; the agent will not bypass the check.')
        if await page.locator('input[type=password]:visible').count():raise FormBlocked('LOGIN_REQUIRED','This application requires an employer-site login. The agent does not share your browser session; complete this application manually on the employer site.')
        if re.search(r'(?:job|position|posting) (?:is |has been )?(?:no longer available|closed|filled)|no longer accepting applications',text,re.I):raise FormBlocked('JOB_CLOSED','The employer says this posting is closed or is no longer accepting applications.')

    async def open_form(self,page,transport,job):
        if not self.supports(job):raise FormBlocked('INVALID_JOB_LINK','Use a public HTTPS application link.')
        try:await asyncio.to_thread(public_target,job['url'])
        except Exception:raise FormBlocked('UNSAFE_JOB_LINK','Use a public HTTPS application link. Private addresses and nonstandard ports are blocked.') from None
        target=await asyncio.to_thread(resolved_form_url,job['url'])
        response=await page.goto(target,wait_until='domcontentloaded',timeout=35000)
        if response and response.status in (404,410):raise FormBlocked('JOB_CLOSED','The job page is unavailable (404/410). It may have been removed.')
        if response and response.status in (401,403,429):raise FormBlocked('SITE_ACCESS_BLOCKED',f'The site refused the browser request (HTTP {response.status}). Open the posting manually.')
        try:await page.wait_for_load_state('networkidle',timeout=10000)
        except Exception:pass
        await self.challenge(page)
        for frame in page.frames:
            if frame!=page.main_frame and greenhouse_host(urlsplit(frame.url).hostname):
                await asyncio.to_thread(public_target,frame.url);await page.goto(frame.url,wait_until='domcontentloaded',timeout=25000)
                try:await page.wait_for_load_state('networkidle',timeout=8000)
                except Exception:pass
                break
        await self.challenge(page)
        for _ in range(2):
            forms=page.locator('form');scored=[]
            for i in range(min(await forms.count(),12)):
                candidate=forms.nth(i)
                if not await candidate.is_visible():continue
                score=(await candidate.locator('input[type=file]').count())*4+await candidate.locator('input[type=email],input[name=email]').count()
                if score>=4:scored.append((score,i))
            if len(scored)>1:raise FormBlocked('AMBIGUOUS_FORM','The page contains multiple résumé forms. Choose the intended application form manually.')
            if scored:
                _,index=max(scored);form=forms.nth(index)
                action=urljoin(page.url,await form.get_attribute('action') or page.url);await asyncio.to_thread(public_target,action)
                transport.greenhouse=greenhouse_host(urlsplit(page.url).hostname)
                if not transport.greenhouse and urlsplit(action).netloc!=urlsplit(page.url).netloc:raise FormBlocked('CROSS_SITE_FORM','The form submits to another host without a supported adapter. Open it manually.')
                transport.form_origin=(urlsplit(page.url).scheme,urlsplit(page.url).netloc)
                return form
            links=page.get_by_role('link',name=re.compile(r'^apply(?: now| for (?:this )?(?:job|position))?$',re.I))
            if await links.count()==1:
                href=await links.get_attribute('href')
                if href:
                    target=urljoin(page.url,href);await asyncio.to_thread(public_target,target);await page.goto(target,wait_until='domcontentloaded',timeout=20000)
                    try:await page.wait_for_load_state('networkidle',timeout=6000)
                    except Exception:pass
                    await self.challenge(page);continue
            buttons=page.get_by_role('button',name=re.compile(r'^apply(?: now| for (?:this )?(?:job|position))?$',re.I))
            if await buttons.count()==1:
                await buttons.click()
                try:await page.wait_for_load_state('networkidle',timeout=6000)
                except Exception:pass
                if len(page.context.pages)>1:
                    popup=page.context.pages[-1]
                    try:await popup.wait_for_load_state('domcontentloaded',timeout=6000)
                    except Exception:pass
                    target=popup.url
                    if target.startswith('https:'):
                        await asyncio.to_thread(public_target,target);await popup.close();await page.goto(target,wait_until='domcontentloaded',timeout=20000)
                await self.challenge(page);continue
            break
        await self.challenge(page)
        raise FormBlocked('APPLICATION_FORM_NOT_FOUND','The page loaded, but no supported application form was found. It may require sign-in or a site-specific application flow.')

    async def fields(self,form):return await form.evaluate(FIELD_SCRIPT)

    async def choice_metadata(self,form,page,fields,metadata):
        # Render fixed custom dropdowns exactly as the applicant sees them.
        # Country/city remain searchable text; never truncate a virtualized location list.
        for field in fields:
            if not field['required'] or not field['combo'] or field['key'] in ('country','candidate-location','location'):continue
            item=metadata.get(field.get('name')) or metadata.get(field['key']) or {}
            if any(f.get('values') for f in item.get('fields',[])):continue
            candidates=[]
            for element in await form.locator('input').all():
                if (await element.get_attribute('id') or await element.get_attribute('name'))==field['key']:candidates.append(element)
            if len(candidates)!=1:continue
            element=candidates[0]
            await element.press('ArrowDown')
            options=page.get_by_role('option')
            try:await options.first.wait_for(state='visible',timeout=2000)
            except Exception:continue
            names=[name.strip() for name in await options.all_text_contents() if name.strip()]
            if 0<len(names)<=30:metadata[field['key']]={'fields':[{'values':[{'label':name} for name in names]}]}
            await element.press('Escape')
        return metadata

    def questions(self,job):return asyncio.run(self._questions(job))

    async def _questions(self,job):
        try:
            async with self.session() as (page,transport):
                form=await self.open_form(page,transport,job)
                fields=await self.fields(form)
                metadata=await asyncio.to_thread(public_questions,page.url)
                metadata=await self.choice_metadata(form,page,fields,metadata)
                return question_definitions(fields,job,metadata)
        except FormBlocked:raise
        except Exception:raise FormBlocked('SITE_FORM_UNREADABLE','The browser could not finish reading the application form. Retry inspection or open the posting manually.') from None

    async def fill(self,form,field,value,page):
        matches=[]
        for element in await form.locator('input,select,textarea').all():
            if (await element.get_attribute('id') or await element.get_attribute('name'))==field['key']:matches.append(element)
        if len(matches)!=1:raise FormBlocked('FORM_CHANGED','The employer form changed. Inspect it again before continuing.')
        element=matches[0]
        if field['kind']=='select':
            options=[o for o in field['options'] if o['label']==value]
            if len(options)!=1:raise FormBlocked('OPTION_CHANGED',f'Choose a current employer option for “{field["label"]}”.')
            await element.select_option(options[0]['value'])
        elif field['combo']:
            await element.fill(value);options=page.get_by_role('option')
            try:await options.first.wait_for(state='visible',timeout=6000)
            except Exception:raise FormBlocked('OPTION_UNAVAILABLE',f'The site could not resolve “{field["label"]}”. Review the value on the employer site.') from None
            names=await options.all_text_contents()
            exact=[name for name in names if name.strip().casefold()==value.strip().casefold()]
            prefix=[name for name in names if re.match(re.escape(value.strip())+r'(?:\s|,|\+)',name.strip(),re.I)]
            matches=exact or prefix
            if len(matches)!=1:raise FormBlocked('AMBIGUOUS_OPTION',f'The site could not resolve “{field["label"]}” to one exact choice. Use the full option text from the employer site.')
            await page.get_by_role('option',name=matches[0],exact=True).click()
        else:await element.fill(value)

    def submit(self,job,applicant,resume,answers=None):return asyncio.run(self._submit(job,applicant,resume,answers))

    async def _submit(self,job,applicant,resume,answers):
        started=False
        try:
            async with self.session() as (page,transport):
                form=await self.open_form(page,transport,job)
                fields=await self.fields(form);metadata=await asyncio.to_thread(public_questions,page.url)
                question_definitions(fields,job,metadata)
                transport.form_filling=True
                for field in fields:
                    if field['kind']=='file' or not field['key']:continue
                    value=(answers or {}).get(field['key'])
                    if value is None:value=base_answer(field,applicant)
                    if value is None:
                        if field['required']:raise FormBlocked('MISSING_ANSWER',f'Answer “{field["label"]}” in this job before continuing.')
                        continue
                    await self.fill(form,field,value,page)
                await self.challenge(page)
                uploads=[f for f in fields if f['kind']=='file' and re.search(r'resume|résumé|\bcv\b',f['key']+' '+f['label'],re.I)]
                if len(uploads)!=1:raise FormBlocked('RESUME_UPLOAD_UNSUPPORTED','The résumé upload control could not be identified unambiguously. Upload it manually.')
                buttons=form.get_by_role('button',name=re.compile(r'^(?:submit(?: application)?|apply(?: now)?|send application)$',re.I))
                if await buttons.count()!=1:raise FormBlocked('SUBMIT_CONTROL_UNSUPPORTED','The final application button could not be identified unambiguously. Complete the form manually.')
                transport.allow_uploads=True;upload=None
                for element in await form.locator('input[type=file]').all():
                    if (await element.get_attribute('id') or await element.get_attribute('name'))==uploads[0]['key']:upload=element
                await upload.set_input_files({'name':resume['name'],'mimeType':resume['mime_type'],'buffer':resume['bytes']})
                try:await page.wait_for_load_state('networkidle',timeout=15000)
                except Exception:pass
                await self.challenge(page)
                if not await buttons.is_enabled():raise FormBlocked('UPLOAD_OR_VALIDATION_PENDING','The employer has not enabled submission after the upload. Review the form manually or retry after checking its requirements.')
                if not await form.evaluate('form => form.checkValidity()'):raise FormBlocked('FORM_VALIDATION_FAILED','The employer form still has invalid or missing fields. Open it to review the required values.')
                transport.allow_writes=True
                started=True
                await buttons.click()
                await page.wait_for_function(r'''() => !document.querySelector('form input[type=file]') && /thank you for applying|application (?:has been |was |successfully )?(?:received|submitted)|successfully (?:applied|submitted)/i.test(document.body.innerText)''',timeout=20000)
                return {'state':'submitted','reference':page.url[:200]}
        except FormBlocked:raise
        except Exception:
            if started:raise
            raise FormBlocked('FORM_FILL_FAILED','The browser could not complete one of the employer’s controls before submission. Review the values or complete the form manually.') from None


class ApplicationBrowser:
    def __init__(self,mock_origin=''):
        self.mock=LocalMockAdapter(mock_origin) if mock_origin else None
        self.live=LiveBrowserAdapter()
    def delegate(self,job):return self.mock if self.mock and self.mock.supports(job) else self.live
    def requires_real_consent(self,job):return self.delegate(job) is self.live
    def supports(self,job):return self.delegate(job).supports(job)
    def preflight(self):self.live.preflight()
    def questions(self,job):return self.delegate(job).questions(job)
    def submit(self,job,applicant,resume,answers=None):
        target=self.delegate(job)
        return target.submit(job,applicant,resume,answers=answers)


def application_browser():
    from core.config import get_settings
    return ApplicationBrowser(get_settings().discovery_mock_origin)
