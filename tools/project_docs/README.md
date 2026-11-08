# Project documentation pack generator

Generates the editable Chinese project-documentation pack for the AI talent platform:

- 65 Word documents covering initiation, planning, design, execution/testing, acceptance, closure/operations, and commercial templates.
- 1 editable training PPTX plus a Word speaker-note version.
- Companion audit artifacts: document manifest CSV, static endpoint CSV, current ORM reference DDL, static facts JSON, and a generation validation report.

Generated binaries/artifacts are intentionally **not committed**. They are written to `deliverables/` (ignored by the root `.gitignore`) and can be regenerated from source.

## Regenerate

From the repository root:

```bash
python -m venv .venv-docs
.venv-docs/bin/pip install python-docx python-pptx pillow \
  fastapi==0.133.0 sqlalchemy==2.0.47 pydantic-settings==2.13.1 python-jose==3.5.0 \
  bcrypt==5.0.0 httpx==0.28.1 langchain-core==0.3.84 langchain-text-splitters==0.3.11 \
  langgraph==0.2.67 jieba==0.42.1 numpy==1.26.4 redis==5.3.1 aiomysql==0.3.2 \
  asyncpg==0.31.0 python-multipart==0.0.22 aiofiles==25.1.0 apscheduler==3.11.3

.venv-docs/bin/python tools/project_docs/facts.py
.venv-docs/bin/python -m tools.project_docs.build
```

On Windows, use `.venv-docs\Scripts\python.exe` instead of `.venv-docs/bin/python`.

## Layout

- `facts.py`: static/metadata collection only. It does not run the application lifespan, business tests, performance tests, or external-service calls.
- `content_data.py`: shared requirement/WBS/risk/test/UAT/page baseline data.
- `content_management.py`, `content_technical.py`, `content_delivery.py`: document content by lifecycle phase.
- `docx_common.py`: Word layout helpers (cover, control/approval page, tables, notes, signatures).
- `build.py`: builds the Word pack, PPTX, companion artifacts, validation report, and ZIP.

## Content boundaries

- Unknown budget, owner, date, amount, test result, and signature fields remain marked as pending; they are not fabricated.
- Historical Markdown status/test numbers are treated as reference only, not inherited as current acceptance evidence.
- Contract/NDA/invoice-registration/payment/reconciliation documents are drafts/templates requiring legal/finance review; the invoice file is not a tax invoice.
- The reference DDL is compiled from current ORM metadata for review only; it is not an approved production migration script.
