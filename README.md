# NexusHub AI — local functional MVP

A starting implementation of the company's AI-powered departmental intranet concept. This is **not yet** a production-ready SharePoint or PowerPoint replacement.

## Run locally (Windows PowerShell)

```powershell
cd nexushub-ai
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Open **http://127.0.0.1:8000**. Using `.venv\Scripts\python.exe` avoids requiring PowerShell script activation (and does not change execution policy).

For macOS/Linux, substitute `.venv/bin/python`.

## AI integration

The initial site generator uses a **local keyword-based layout fallback**. It works without credentials but **is not an AI model**. To enable model-based initial site generation, set an OpenAI API key on your machine before starting the app:

```powershell
$env:OPENAI_API_KEY="your-api-key"
```

The optional model is configured using `NEXUS_AI_MODEL` (default `gpt-4.1-mini`). Model requests happen only on the server. If the model request fails, the local template generator is used and the UI identifies that mode. Subsequent natural-language site editing currently supports simple color changes and page creation only.

## Demonstrable workflow

1. Register a user and create a company.
2. Create a department.
3. Describe your site, generate pages, edit components, save and publish.
4. Open Nexus Slides, create a presentation, add slides and text objects, change layouts, save.
5. Embed the presentation on a department site page; publish the site again.
6. Register another user and grant them editor or viewer access by email through Team access.

Data is stored in `nexushub.sqlite3` (created on first start). The database is separate from the zip until you actually run the app.

## Security model / limits

- API sessions with hashed passwords; session tokens persist client-side in localStorage (local development only, not production hardened).
- Organization membership and department membership are checked in API handlers. Company admins may access all departments in their organization; all other users require explicit department membership.
- AI generation is scoped to authorized departments. Prompts do not include private cross-tenant organizational content.
- Editing a site changes it back to draft. **Draft vs published status is represented but separate read-only published snapshot URLs are not yet implemented**.
- SQLite is used to make the MVP easy to run; production should use Postgres, persistent object storage, server-set secure HttpOnly cookies, identity provider/SSO, tenant isolation defense-in-depth, rate limiting, validation of all content schema fields, CSRF protection, concurrency controls, and comprehensive audits/tests.
- Document libraries, calendar, announcements, employee directory, fully visual drag/drop, collaboration, PPTX/PDF import/export, AI slide creation, AI RAG, custom layout components and full published site hosting **are not yet implemented**. The UI labels unimplemented features explicitly.
- Slide text elements are editable via the inspector, not yet by dragging on the canvas.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Suggested next phases

1. PostgreSQL, migrations, session security, invite flow, and more thorough tenant-isolation tests.
2. Real document libraries, data widgets, published snapshots and drag/drop page editor.
3. Full structured AI site editing with change previews and approvals.
4. Mature presentation canvas with drag/resize, rich text, PPTX/PDF export and AI slides.
5. Permission-filtered knowledge retrieval, audit observability, SSO and production hardening.
