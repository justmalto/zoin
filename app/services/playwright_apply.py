"""First adapter: explicit local mock only. Real employer adapters are not enabled."""
import os
from pathlib import Path
from urllib.parse import urlsplit

BROWSER_CACHE=Path(__file__).resolve().parents[2]/'.local'/'playwright'

def configure_browser_cache():
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(BROWSER_CACHE))
    temporary=BROWSER_CACHE.parent/'tmp'
    temporary.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.environ.setdefault('TMPDIR',str(temporary))

class LocalMockAdapter:
    def __init__(self,origin):
        p=urlsplit(origin)
        if p.scheme!='http' or p.hostname!='127.0.0.1' or p.username or p.password or p.query or p.fragment or p.path not in ('','/') or not p.port:raise ValueError('Use an explicit local mock origin.')
        self.origin=origin.rstrip('/')
    def preflight(self):
        import importlib.util
        from fastapi import HTTPException
        if not importlib.util.find_spec('playwright'):
            raise HTTPException(503,'Playwright is not installed locally; no submission was attempted.')
        configure_browser_cache()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).is_file():
                raise HTTPException(503,'Project-local Chromium is not installed; no submission was attempted.')
    def supports(self,job):return job.get('source')=='local_mock' and job['url']==self.origin+'/apply'
    def questions(self,job):
        if not self.supports(job):raise ValueError('Unsupported target')
        configure_browser_cache()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(service_workers='block')
            context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.origin+'/') else route.abort())
            page=context.new_page();page.set_default_timeout(10000)
            try:
                page.goto(job['url'],wait_until='domcontentloaded')
                if page.get_by_text('CAPTCHA',exact=False).count():raise ValueError('Manual challenge')
                fields=page.locator('[data-agent-question]').evaluate_all("""nodes => nodes.map(el => ({key:el.name,label:el.labels?.[0]?.textContent.replace(el.textContent,'').trim()||el.getAttribute('aria-label'),type:el.tagName==='SELECT'?'select':'text',options:el.tagName==='SELECT'?Array.from(el.options).map(o=>o.value).filter(Boolean):[],required:el.required}))""")
                return [q|{'context':{'employer':job['company'],'country':job.get('location') or 'unspecified','form_url':job['url']}} for q in fields]
            finally:browser.close()

    def submit(self,job,applicant,resume,answers=None):
        if not self.supports(job):raise ValueError('Unsupported target')
        configure_browser_cache()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(accept_downloads=False,service_workers='block')
            context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.origin+'/') else route.abort())
            page=context.new_page();page.set_default_timeout(10000)
            try:
                page.goto(job['url'],wait_until='domcontentloaded')
                if page.get_by_text('CAPTCHA',exact=False).count():return {'state':'needs_user_action'}
                page.get_by_label('Full name',exact=True).fill(applicant['name'])
                page.get_by_label('Email',exact=True).fill(applicant['email'])
                page.get_by_label('Resume',exact=True).set_input_files({'name':resume['name'],'mimeType':resume['mime_type'],'buffer':resume['bytes']})
                for field in page.locator('[data-agent-question]').all():
                    key=field.get_attribute('name')
                    value=(answers or {}).get(key)
                    if value is None and field.get_attribute('required') is not None:return {'state':'needs_user_action'}
                    if value is not None:
                        if field.evaluate('el => el.tagName')=='SELECT':field.select_option(value)
                        else:field.fill(value)
                page.get_by_role('button',name='Submit application',exact=True).click()
                receipt=page.get_by_role('status').inner_text()
                if not receipt.startswith('Application received: '):raise ValueError('Unrecognized receipt')
                return {'state':'submitted','reference':receipt.removeprefix('Application received: ')}
            finally:browser.close()
