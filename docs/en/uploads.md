# Project Materials (Uploads)

Projects can be enriched with supporting materials (documents and images) that serve as citable sources during debate rounds. The orchestrator queries the ChromaDB collection `project_documents` per turn (strictly scoped per project) and injects matching excerpts into the prompt as a `PROJECT-MATERIAL` block.

## Supported Formats

| Type | Extensions | Processing Method |
|------|------------|-------------------|
| Text Document | `.txt`, `.md` | Directly parsed |
| PDF Document | `.pdf` | Text extraction via `pypdf` |
| Word Document | `.docx` | Paragraph & table extraction via `python-docx` |
| Image | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` | Metadata processed via Pillow; **description text** is indexed |

Maximum file size: 20 MB (`UPLOAD_MAX_BYTES`). Uploaded files reside under `/data/uploads/{project_id}/` with metadata stored in PostgreSQL (`project_documents`).

!!! tip "Images"
    Without an active OCR pipeline, the user-supplied **description** is the sole indexable text for images. Uploading an image without a description makes it unfindable during debates.

## API Endpoints

```
POST   /api/v2/projects/{project_id}/documents          (multipart: file, description)
GET    /api/v2/projects/{project_id}/documents
GET    /api/v2/projects/{project_id}/documents/{doc_id}/download
DELETE /api/v2/projects/{project_id}/documents/{doc_id}
```

## Chunking & Retrieval

- **Chunks:** ~1,200 characters with 200-character overlaps, split along paragraph and sentence boundaries.
- **Retrieval:** Top-4 chunks fetched per turn using `Motion + last contribution` as query.
- **Deletion:** Deleting a document removes the file, database record, and vector index chunks.
