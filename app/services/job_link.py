"""Bounded public HTTPS job fetching; DNS is validated and pinned per connection."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from threading import BoundedSemaphore
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from services.groq_client import AIError
from services.job_extractor import extract_job

MAX_BYTES = 1_000_000
IMPORT_SLOTS = BoundedSemaphore(2)


def public_target(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').encode('idna').decode('ascii')
        if parts.scheme != 'https' or not host or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError()
        if len(url) > 2000 or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
            raise ValueError()
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError()
        return host, ips[0], urlunsplit(('https', parts.netloc, parts.path or '/', parts.query, ''))
    except (ValueError, UnicodeError, OSError):
        raise AIError('Use a public HTTPS job-posting link. Local, private and nonstandard-port addresses are not allowed.', 422) from None


def fetch_posting(url, *, json_response=False):
    deadline = time.monotonic() + 30
    for hop in range(4):
        host, ip, url = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIError('The job site took too long to respond. Paste the job description instead.', 504)
        connection = http.client.HTTPSConnection(host, timeout=min(10, remaining), context=ssl.create_default_context())
        # Connect to the validated IP, retaining the original hostname for TLS/SNI.
        connection._create_connection = lambda address, timeout, source_address=None: socket.create_connection((ip, 443), timeout, source_address)
        try:
            parts = urlsplit(url)
            connection.request('GET', urlunsplit(('', '', parts.path, parts.query, '')), headers={
                'User-Agent': 'ZoinJobImporter/1.0', 'Accept': 'application/json' if json_response else 'text/html, application/xhtml+xml', 'Accept-Encoding': 'identity',
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader('Location')
                if not target or hop == 3:
                    raise AIError('The job link redirects too many times. Paste the direct posting link or description.', 422)
                url = urljoin(url, target)
                continue
            if response.status != 200:
                raise AIError('The job site blocked access or the posting is unavailable. Paste the job description instead.', 422)
            content_type = response.getheader('Content-Type', '')
            if (json_response and 'application/json' not in content_type.lower()) or (not json_response and 'text/html' not in content_type.lower() and 'application/xhtml+xml' not in content_type.lower()):
                raise AIError('This link is not an HTML job posting. Paste the job description instead.', 422)
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise AIError('This job site returned an unsupported page format. Paste the description instead.', 422)
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise AIError('The job site took too long to respond. Paste the job description instead.', 504)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise AIError('The job page is too large to import. Paste the description instead.', 422)
            return bytes(data).decode('utf-8', errors='replace'), url
        except (OSError, http.client.HTTPException, UnicodeError, ValueError):
            raise AIError('Unable to read this job site securely. Paste the description or try another direct posting link.', 502) from None
        finally:
            connection.close()


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.scripts = []
        self.skip = 0
        self.script = None
        self.title = []
        self.location = []
        self.location_depth = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            if self.location_depth:
                self.location_depth += 1
            elif 'job__location' in (attrs.get('class') or '').split():
                self.location_depth = 1
        if tag == "title":
            self.in_title = True
        if tag == 'script':
            self.script = [] if (attrs.get('type') or '').lower() == 'application/ld+json' else None
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'noscript', 'form', 'head'):
            self.skip += 1
        if tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3') and not self.skip:
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag == "div" and self.location_depth:
            self.location_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script))
            self.script = None
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'noscript', 'form', 'head'):
            self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div', 'li') and not self.skip:
            self.text.append('\n')

    def handle_data(self, data):
        if self.location_depth:
            self.location.append(data)
        if self.in_title:
            self.title.append(data)
        if self.script is not None:
            self.script.append(data)
        if not self.skip:
            self.text.append(data)



class LinkedInText(HTMLParser):
    """Capture published job sections, excluding recommendations and sign-in UI."""
    selectors = {'topcard__title': 'title', 'topcard__org-name-link': 'company',
                 'topcard__flavor--bullet': 'location', 'show-more-less-html__markup': 'description'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = {}
        self.values = {}

    def handle_starttag(self, tag, attrs):
        for value in self.active.values():
            if value[0] == tag:
                value[1] += 1
        classes = (dict(attrs).get('class') or '').split()
        for cls, key in self.selectors.items():
            if cls in classes and key not in self.active and key not in self.values:
                self.active[key] = [tag, 1, []]
        if tag in ('p', 'div', 'li', 'br'):
            for value in self.active.values(): value[2].append('\n')

    def handle_endtag(self, tag):
        for key, value in list(self.active.items()):
            if tag in ('p', 'div', 'li'): value[2].append('\n')
            if value[0] == tag:
                value[1] -= 1
                if value[1] == 0:
                    self.values[key] = '\n'.join(' '.join(line.split()) for line in ''.join(value[2]).splitlines() if line.strip())
                    del self.active[key]

    def handle_data(self, data):
        for value in self.active.values(): value[2].append(data)



class GreenhouseText(LinkedInText):
    selectors = {'job__description': 'description'}


def clean_description(text, company=None):
    """Conservative linear-time cleanup: no model call and no sentence rewriting."""
    core = re.compile(r"(?:about (?:the |this )?role|role (?:details|overview|summary)|job (?:purpose|summary)|(?:key |primary |secondary )?responsibilities|requirements|qualifications|(?:required |preferred |minimum )?(?:skills|qualifications|experience)|what (?:you(?:’|')?ll (?:do|bring|need)|will you do(?: at .+)?|we(?:’|')?re looking for|we are looking for)|who (?:you are|are you)|what makes you .+|(?:key )?success metrics|education|experience|benefits|compensation|salary|what we offer|even better if you have|our stack)", re.I)
    clutter = re.compile(r"(?:about (?:us|the company)|(?:abc )?company (?:info|information)|what makes an ideal abc citizen|join us|(?:our )?(?:core )?values|our culture|life at .+|disclaimer|important|equal (?:employment )?opportunit(?:y|ies)(?: employer)?|privacy(?: notice| policy)?|recruitment (?:fraud|privacy)(?: notice)?|refer the links to know more.*|stay updated with life at.*)", re.I)
    ui = re.compile(r"(?:apply(?: now| for this job)?|back to jobs|view all jobs|share(?: this job)?|show (?:more|less)|save(?: job)?|sign in|create (?:a |an )?(?:job alert|account)|powered by (?:greenhouse|lever)|accept all cookies|manage cookies|cookie (?:policy|preferences))", re.I)
    lines = [' '.join(line.split()) for line in text.splitlines() if line.strip()]
    # Discard unheaded company introductions only when a clear role section follows.
    first_core = next((i for i, line in enumerate(lines) if core.fullmatch(line.strip(' :–—-#*?'))), None)
    if first_core and company and company.casefold() in lines[0].casefold():
        preamble = '\n'.join(lines[:first_core])
        if not re.search(r'\b(?:experience|salary|qualification|requirement|benefit)\b|[$₹€£]', preamble, re.I):
            lines = lines[first_core:]
    result = []
    skipping = False
    for line in lines:
        heading = line.strip(' :–—-#*?').strip()
        if core.fullmatch(heading):
            skipping = False
        elif clutter.fullmatch(heading) or (isinstance(company, str) and heading.casefold() == 'about ' + company.casefold()):
            skipping = True
            continue
        if skipping or ui.fullmatch(heading) or re.fullmatch(r'[-_–—=]{4,}', line) or line in (r'\n', r'\t'):
            continue
        if re.match(r'.{0,80}\bis an equal opportunity employer\b|.{0,80}\bmay collect personal information directly from you\b|(?:for further details,? )?(?:please )?(?:see|read) .{0,80}applicant privacy', line, re.I):
            continue
        result.append(line)
    return '\n'.join(result)


def clean_html(value):
    parser = PageText()
    parser.feed(value if isinstance(value, str) else '')
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.text).splitlines() if line.strip())


def postings(value, depth=0):
    if depth > 12:
        return
    if isinstance(value, list):
        for item in value:
            yield from postings(item, depth + 1)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
            yield value
        else:
            for key in ('@graph', 'mainEntity', 'itemListElement', 'item'):
                yield from postings(value.get(key), depth + 1)


def short(value, limit=300):
    return clean_html(value)[:limit] or None


def parse_posting(html, url):
    page = PageText()
    page.feed(html)
    candidates = []
    for script in page.scripts:
        try:
            candidates.extend(postings(json.loads(script)))
        except (ValueError, RecursionError):
            continue
    if len(candidates) > 1:
        raise AIError('This page contains multiple jobs. Paste the direct link to one job posting.', 422)
    item = candidates[0] if candidates else {}
    hostname = urlsplit(url).hostname or ''
    linkedin = hostname == 'linkedin.com' or hostname.endswith('.linkedin.com')
    if linkedin and not item:
        public_page = LinkedInText()
        public_page.feed(html)
        values = public_page.values
        if all(values.get(key) for key in ('title', 'company', 'description')):
            item = {'title': values['title'], 'description': escape(values['description']),
                    'hiringOrganization': {'name': values['company']},
                    'jobLocation': {'address': {'addressLocality': values.get('location')}}}
    description_value = item.get('description') or ''
    description = clean_html(unescape(description_value)) if item and isinstance(description_value, str) else clean_html(html)
    if not item and hostname.endswith('.greenhouse.io'):
        section = GreenhouseText()
        section.feed(html)
        description = section.values.get('description') or description
    original_description = description
    employer = item.get('hiringOrganization') or {}
    employer = employer.get('name') if isinstance(employer, dict) else employer
    description = clean_description(description, employer)
    if (hostname == 'linkedin.com' or hostname.endswith('.linkedin.com')) and not item:
        raise AIError('LinkedIn did not expose this job publicly. Paste the description or use the employer’s original posting link.', 422)
    if len(description) < 80:
        raise AIError('No readable job description was found. This site may require sign-in or JavaScript; paste the description instead.', 422)
    truncated = len(description) > 18000
    description = description[:18000]
    company = item.get('hiringOrganization') or {}
    identifier = item.get('identifier') or {}
    places = item.get('jobLocation') or []
    if isinstance(places, dict):
        places = [places]
    locations = []
    for place in places if isinstance(places, list) else []:
        address = place.get('address', {}) if isinstance(place, dict) else {}
        if isinstance(address, dict):
            locations.append(', '.join(str(address[k]) for k in ('addressLocality', 'addressRegion', 'addressCountry') if isinstance(address.get(k), str)))
    fields = {
        'job_title': short(item.get('title')),
        'company': short(company.get('name')) if isinstance(company, dict) else short(company),
        'job_id': short(identifier.get('value')) if isinstance(identifier, dict) else short(identifier),
        'location': '; '.join(filter(None, locations))[:300] or ('Remote' if item.get('jobLocationType') == 'TELECOMMUTE' else None),
        'employment_type': short(', '.join(item['employmentType']) if isinstance(item.get('employmentType'), list) and all(isinstance(x, str) for x in item['employmentType']) else item.get('employmentType'), 100),
    }
    # Greenhouse explicitly identifies role and employer in its document title.
    # This avoids spending Groq tokens on fields already published verbatim.
    hostname = urlsplit(url).hostname or ''
    if hostname.endswith('.greenhouse.io'):
        fields['location'] = fields['location'] or short(''.join(page.location))
        title_match = re.fullmatch(r'Job Application for (.+) at (.+)', ' '.join(''.join(page.title).split()))
        if title_match:
            fields['job_title'] = fields['job_title'] or title_match[1][:300]
            fields['company'] = fields['company'] or title_match[2][:300]
        job_match = re.search(r'/jobs/(\d+)/?$', urlsplit(url).path)
        if job_match:
            fields['job_id'] = fields['job_id'] or job_match[1]
    if hostname == 'linkedin.com' or hostname.endswith('.linkedin.com'):
        job_match = re.fullmatch(r'/jobs/view/(?:.*-)?(\d+)/?', urlsplit(url).path)
        if job_match:
            fields['job_id'] = fields['job_id'] or job_match[1]
    warnings = ['Review all imported details before saving.']
    if description != original_description[:18000]:
        warnings.append('Page controls and recognized company/legal boilerplate were removed. Review the description for completeness.')
    if not fields['job_title'] or not fields['company']:
        # The existing evidence-checked extractor only sees page text, never keys or browser cookies.
        extracted = extract_job(description)
        for key in fields:
            fields[key] = fields[key] or short(extracted.get(key), 100 if key == 'employment_type' else 300)
    missing = [label for key, label in [('job_title', 'job title'), ('company', 'company')] if not fields[key]]
    if missing:
        warnings.append('Enter the missing ' + ' and '.join(missing) + ' manually.')
    if truncated:
        warnings.append('The description was shortened to 18,000 characters. Check that the important requirements are included.')
    return fields | {'job_description': description, 'source_url': url, 'warnings': warnings,
                     'extraction_method': 'structured' if candidates else ('linkedin_public_page' if linkedin and item else 'page_text')}



def peoplestrong_posting(url):
    """Read the same anonymous JSON endpoint used by the public career page."""
    parts = urlsplit(url)
    host = parts.hostname or ''
    match = re.fullmatch(r'/job/detail/([A-Za-z0-9_-]{1,100})/?', parts.path)
    if not host.endswith('.peoplestrong.com') or not match:
        return None
    # Validate the original URL too (credentials/ports must never be stripped).
    public_target(url)
    code = match[1]
    endpoint = urlunsplit(('https', host,
        '/api/cp/rest/altone/cp/job/' + code + '/v2',
        'part=basic,organisational,descriprion&isReqId=false', ''))
    body, _ = fetch_posting(endpoint, json_response=True)
    try:
        payload = json.loads(body)
        item = payload.get('response') if isinstance(payload, dict) else None
        if not isinstance(item, dict) or item.get('requisitionCode') != code:
            raise ValueError()
        description = item.get('jobDescription')
        if not isinstance(description, str) or len(clean_html(description)) < 80:
            raise ValueError()
        if item.get('requisitionStatus') != 'OPEN':
            raise ValueError()
        # The employer hierarchy publishes the legal company at its second level.
        hierarchy = item.get('organizationUnitComplete')
        units = hierarchy.split('>') if isinstance(hierarchy, str) else []
        company = units[1].strip() if len(units) > 1 else None
        location = item.get('locationHierarchyComplete')
        location = ', '.join(reversed(location.split('>'))) if isinstance(location, str) else None
        posting = {'@type': 'JobPosting', 'title': item.get('jobTitle'),
            'description': description, 'hiringOrganization': {'name': company},
            'identifier': {'value': code}, 'employmentType': item.get('employmentType'),
            'jobLocation': {'address': {'addressLocality': location}}}
        # Escape script delimiters before feeding our existing structured parser.
        html = '<script type="application/ld+json">' + json.dumps(posting).replace('<', chr(92) + 'u003c') + '</script>'
        result = parse_posting(html, url)
        result['extraction_method'] = 'peoplestrong_public_api'
        return result
    except (ValueError, TypeError, RecursionError):
        raise AIError('PeopleStrong did not return an open job with a readable description. The posting may have expired; paste the description instead.', 422) from None



def normalize_job_url(url):
    """Convert LinkedIn selected-job/search URLs to their public detail page."""
    parts = urlsplit(url)
    host = parts.hostname or ''
    if host != 'linkedin.com' and not host.endswith('.linkedin.com'):
        return url
    public_target(url)  # Validate before discarding credentials, ports or tracking.
    match = re.fullmatch(r'/jobs/view/(?:.*-)?(\d+)/?', parts.path)
    code = match[1] if match else None
    if not code and parts.path.rstrip('/') in ('/jobs/search', '/jobs/search-results', '/jobs/collections/recommended'):
        values = parse_qs(parts.query).get('currentJobId', [])
        if len(values) == 1 and re.fullmatch(r'\d{1,20}', values[0]):
            code = values[0]
    if not code:
        raise AIError('Copy a LinkedIn job link with a selected job, or paste the job description. A general search page cannot be imported.', 422)
    return 'https://www.linkedin.com/jobs/view/' + code + '/'


def import_job_link(url):
    if not IMPORT_SLOTS.acquire(blocking=False):
        raise AIError("Job import is busy. Wait a moment and retry.", 503)
    try:
        url = normalize_job_url(url)
        supported = peoplestrong_posting(url)
        if supported is not None:
            return supported
        html, final_url = fetch_posting(url)
        return parse_posting(html, final_url)
    finally:
        IMPORT_SLOTS.release()
