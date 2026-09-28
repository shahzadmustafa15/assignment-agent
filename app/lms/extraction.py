"""Read-only table snapshots using headings and controls observed on the real LMS."""
from app.lms.base import LMSError
from app.lms.session import login_page

HEADERS = (
    'Assign. No.', 'Title', 'Assignment (Solution File) Remarks', 'Added Submission',
    'Marks Obtained', 'Returned Submission (Comments)', 'Action', 'Deadline',
)

# Generic DOM traversal; columns are matched by their headings, never nth-child.
# Only the confirmed assignment table and selected course/semester labels are read.
TABLE_SCRIPT = """() => {
 const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
 const visible = e => !!(e.getClientRects().length);
 const deadlineSources = cell => {
   const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
   const sources = []; let node;
   while (node = walker.nextNode()) {
     if (!clean(node.textContent)) continue;
     let element = node.parentElement; const context = []; let hidden = false;
     while (element) {
       const style = getComputedStyle(element);
       hidden = hidden || element.hidden || element.getAttribute('aria-hidden') === 'true'
         || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse';
       context.push({tag:element.tagName.toLowerCase(), title:element.getAttribute('title') || '',
         label:element.getAttribute('aria-label') || ''});
       if (element === cell) break;
       element = element.parentElement;
     }
     sources.push({raw:node.textContent.trim(), hidden, context});
   }
   return sources;
 };
 const tables = Array.from(document.querySelectorAll('table')).filter(visible).map(t => {
   const headers = Array.from(t.querySelectorAll('th')).filter(h => h.closest('table') === t).map(h => clean(h.innerText));
   if (!headers.includes('Assign. No.') || !headers.includes('Deadline')) return null;
   return {headers, rows: Array.from(t.rows).filter(r => r.closest('table') === t && visible(r))
     .filter(r => Array.from(r.cells).some(c => c.tagName === 'TD')).map(r =>
       Array.from(r.cells).map((c, index) => ({deadline_sources: headers[index] === 'Deadline' ? deadlineSources(c) : undefined, text: clean(c.innerText), raw_text: c.innerText, colspan: c.colSpan, rowspan: c.rowSpan,
         links: Array.from(c.querySelectorAll('a')).filter(visible).map(a => ({text: clean(a.innerText),
           href: a.getAttribute('href') || '', has_handler: !!a.getAttribute('onclick')})),
         buttons: Array.from(c.querySelectorAll('button,input[type=submit],input[type=button]')).filter(visible).map(b => clean(b.innerText)),
         forms: Array.from(c.querySelectorAll('form')).map(f => ({method: f.method}))}))) };
 }).filter(Boolean);
 const selected = id => { const e = document.getElementById(id); if (!e || !e.value) return '';
   return clean(e.selectedOptions[0]?.textContent); };
 return {tables, course: selected('courseId'), semester: selected('semesterId')};
}"""


def read_assignment_table(page):
    if login_page(page):
        from app.lms.background import AuthExpired
        raise AuthExpired('LMS session expired; authenticate again.')
    snapshot = page.evaluate(TABLE_SCRIPT)
    tables = snapshot.get('tables', [])
    if len(tables) != 1:
        raise LMSError('Expected one Assignments table. Select a course Assignments page; the DOM may have changed.')
    table = tables[0]
    if len(table['headers']) != len(HEADERS) or set(table['headers']) != set(HEADERS):
        raise LMSError('Assignment table headings changed; scanning stopped safely.')
    table.update(course=snapshot.get('course', ''), semester=snapshot.get('semester', ''), page_url=page.url)
    return table
