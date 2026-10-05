import base64, json, os, re, tempfile
from datetime import datetime
from pathlib import Path
import requests
import fitz
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
OLLAMA = os.getenv('OLLAMA_URL','http://localhost:11434')
MODEL = os.getenv('OLLAMA_MODEL','qwen3.5:4b')
app = FastAPI(title='Aakhri Tareekh')
app.mount('/static', StaticFiles(directory=STATIC), name='static')

PROMPT = '''You are Aakhri Tareekh, a strict and conservative college-notice deadline extractor.

Read ONLY the supplied notice image/page.

CRITICAL RULES:

1. Extract a deadline ONLY when an actual date is visibly written on THIS PAGE and is connected to a required action, payment, submission, registration, examination, application, fee, or other time-sensitive event.

2. NEVER treat a reference to another page, annexure, section, table, website, attachment, or document as a deadline.
Examples:
- "Payment due dates are given in Annexure 1." -> NOT a deadline.
- "See Annexure 2 for procedure." -> NOT a deadline.
- "Details are given on the website." -> NOT a deadline.

3. NEVER invent or infer a date from context.
If the page says that dates exist elsewhere but does not show the actual date on this page, return no deadline for that statement.

4. NEVER change a visible year.
If the image visibly says 2025, use 2025.
If it visibly says 2026, use 2026.
If the year is missing, conflicting, unreadable, or uncertain, set date to null and status to "unclear".

5. A deadline must have evidence.
The evidence must quote or closely reproduce the exact sentence, row, bullet, or table text on THIS PAGE that supports the deadline.

6. Do not convert ordinary dates into deadlines.
For example:
- notice issue date
- circular date
- academic year
- entry year
- date mentioned only as background
are NOT deadlines unless the text explicitly connects that date to an action or due requirement.

7. If there are no actual deadlines on this page, return:
"deadlines": []

8. Be especially careful with dates near words such as:
"by", "before", "on or before", "due", "last date", "deadline", "up to", "from", "till", "payment", "submit", "registration", "confirm", "apply".

9. If a date range is explicitly shown, preserve the range in the evidence and create the appropriate deadline entry.

10. Do not summarize or combine multiple unrelated dates into one deadline.

11. NEVER infer a date that is not explicitly visible on THIS PAGE.

12. For a date range such as "15th July 2026 to 19th July 2026", preserve the range exactly in date_text.
    Do NOT convert it into 15 July, 19 July, 25 July, or any other single date.

13. The normalized "date" field may be populated only when there is one unambiguous calendar date explicitly visible.
    For a range, set "date" to null and keep the full range in "date_text".

14. The "date" and "date_text" must be directly supported by the same evidence text.

15. Only treat a year as conflicting when the conflict occurs within the evidence for the SAME deadline.
    Do not let an unrelated date or year elsewhere on the page make an otherwise clear deadline unclear.
    If the deadline's own evidence clearly shows one year, use that year.
    If the deadline's own evidence contains conflicting or ambiguous years, use date=null and status="unclear".

16. The summary must not resolve, correct, or infer conflicting dates.
    If the page contains conflicting years, describe the uncertainty without choosing a year.

17. If the evidence contains exactly one explicit full calendar date and there is no ambiguity within that evidence, ALWAYS populate the "date" field in YYYY-MM-DD format.
    For example, "1st July 2026" must produce "date": "2026-07-01".
    "Up to 14th July 2026" must produce "date": "2026-07-14".
    A date range must produce "date": null.

18. A single explicit date remains a valid date even when preceded by words such as "from", "starting from", "up to", "by", "before", or "on".
For example:
- "Online fee collection will start from 1st July 2026" means date = "2026-07-01".
- "Up to 14th July 2026" means date = "2026-07-14".
These are single dates, NOT date ranges.
Only mark a date as unclear when the date itself is ambiguous, conflicting, unreadable, or part of a multi-date range.

Return ONLY valid JSON matching this schema:

{
  "title": "string or null",
  "deadlines": [
    {
      "date": "YYYY-MM-DD or null",
      "date_text": "exact visible date or date range",
      "time": "string or null",
      "type": "string",
      "action": "string or null",
      "applies_to": ["string"],
      "fee": "string or null",
      "status": "clear or unclear",
      "evidence": "exact or near-exact supporting text from THIS PAGE"
    }
  ],
  "documents": ["string"],
  "summary": "string or null"
}

IMPORTANT:
- Never guess.
- Never silently correct a date.
- Never create a deadline from a cross-reference.
- Never use information from another page.
- If uncertain, use null and "unclear".
'''

def image_b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()

def call_ollama(img_path):
    payload={'model':MODEL,'messages':[{'role':'user','content':PROMPT,'images':[image_b64(img_path)]}], 'stream':False, 'think':False}
    r=requests.post(f'{OLLAMA}/api/chat',json=payload,timeout=600)
    r.raise_for_status()
    data=r.json()
    content=data.get('message',{}).get('content','').strip()
    m=re.search(r'```(?:json)?\s*(.*?)\s*```',content,re.S)
    if m: content=m.group(1)
    try: return json.loads(content)
    except Exception:
        return {'title':None,'deadlines':[],'documents':[],'summary':content,'raw':content}

def make_temp_path(suffix):
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(name)

def render_pdf(path, page_num):
    doc=fitz.open(path)
    if page_num < 1 or page_num > len(doc): raise ValueError(f'Page must be 1-{len(doc)}')
    page=doc[page_num-1]
    pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5), alpha=False)
    out=make_temp_path('.png')
    pix.save(str(out)); doc.close()
    return out, len(doc) if False else None

@app.get('/')
def index(): return FileResponse(STATIC/'index.html')

@app.post('/api/pages')
async def pages(file: UploadFile=File(...)):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix != '.pdf':
        return {'pages': 1}
    tmp=make_temp_path('.pdf')
    tmp.write_bytes(await file.read())
    try:
        doc=fitz.open(str(tmp)); n=len(doc); doc.close()
        return {'pages': n}
    finally:
        try: tmp.unlink()
        except: pass

@app.post('/api/analyze')
async def analyze(file: UploadFile=File(...), page: int=Form(1)):
    suffix=Path(file.filename or '').suffix.lower()
    tmp=make_temp_path(suffix or '.bin')
    tmp.write_bytes(await file.read())
    try:
        if suffix == '.pdf':
            doc=fitz.open(str(tmp)); pages=len(doc); doc.close()
            img,_=render_pdf(tmp,page)
        elif suffix in {'.png','.jpg','.jpeg','.webp'}:
            pages=1; img=tmp
        else:
            return JSONResponse({'error':'Use PDF, PNG, JPG, or WEBP.'},status_code=400)
        result=call_ollama(img)
	# Safety guard: never convert a date range into a single calendar date.
        for d in result.get('deadlines', []):
            text = ' '.join([
                str(d.get('date_text') or ''),
                str(d.get('evidence') or '')
            ]).lower()

            # Safety guard: preserve ranges as unclear, but recover a
            # single explicit 2026 calendar date when the model left date null.
            date_pattern = r'\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\b'
            visible_dates = list(dict.fromkeys(re.findall(date_pattern, text)))

            range_markers = [' to ', ' till ', ' until ', ' - ', '–']

            # Safety guard: if this deadline says 2025 while the page also contains 2026,
            # do not confirm the 2025 date.
            page_text = json.dumps(result).lower()

            if '2025' in text and '2026' in page_text:
                d['date'] = None
                d['status'] = 'unclear'

            # Never convert a date range into one calendar date.
            elif len(visible_dates) >= 2 and any(marker in text for marker in range_markers):
                d['date'] = None
                d['status'] = 'unclear'

            # Recover one explicit 2026 date if the model failed to normalize it.
            elif len(visible_dates) == 1 and visible_dates[0].endswith('2026'):
                 raw_date = visible_dates[0]

                 for fmt in (
                     '%d %B %Y',
                     '%dst %B %Y',
                     '%dnd %B %Y',
                     '%drd %B %Y',
                     '%dth %B %Y',
                 ):
                     try:
                         cleaned = re.sub(r'(st|nd|rd|th)', '', raw_date)
                         parsed = datetime.strptime(cleaned, '%d %B %Y')
                         d['date'] = parsed.strftime('%Y-%m-%d')
                         d['status'] = 'clear'
                         break
                     except ValueError:
                         pass
        result['source_file']=file.filename
        result['page']=page
        result['pages']=pages
        return result
    except requests.exceptions.ConnectionError:
        return JSONResponse({'error':'Ollama is not reachable. Start Ollama and make sure qwen3.5:4b is installed.'},status_code=503)
    except Exception as e:
        return JSONResponse({'error':str(e)},status_code=500)
    finally:
        try: tmp.unlink()
        except: pass

def ics_escape(s): return str(s).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')

@app.post('/api/calendar')
async def calendar(payload: dict):
    d=payload.get('date'); title=payload.get('title') or 'College deadline'
    if not d: return JSONResponse({'error':'No confirmed date to add.'},status_code=400)
    try: dt=datetime.strptime(d,'%Y-%m-%d').strftime('%Y%m%d')
    except: return JSONResponse({'error':'Date must be YYYY-MM-DD.'},status_code=400)
    uid=f'aakhri-{dt}-{abs(hash(title))}@localhost'
    ics=f'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Aakhri Tareekh//EN\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\r\nDTSTART;VALUE=DATE:{dt}\r\nSUMMARY:{ics_escape(title)}\r\nDESCRIPTION:{ics_escape(payload.get('description','Extracted from a college notice by Aakhri Tareekh.'))}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''
    p=Path(tempfile.mkstemp(suffix='.ics')[1]); p.write_text(ics,encoding='utf-8')
    return FileResponse(p,media_type='text/calendar',filename='aakhri-tareekh-deadline.ics',background=None)
