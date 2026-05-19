# API overview

Run the API with:

```bash
uvicorn openblade.api.main:app --reload
```

Key endpoints:
- `GET /health`
- `GET /inventory/`
- `GET /cartridges/`
- `POST /cartridges/{barcode}/format/dry-run`
- `POST /cartridges/format/confirm`
- `POST /volume-groups/`
- `POST /archive/`
- `POST /restore/`
- `GET /jobs/{job_id}`
