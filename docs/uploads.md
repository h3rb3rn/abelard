# Projekt-Material (Dokumente & Bilder)

Projekte können mit Material angereichert werden, das den Debatten-Agenten als
zitierfähige Quelle zur Verfügung steht. Der Orchestrator ruft pro Turn die
semantisch relevantesten Ausschnitte ab (ChromaDB-Collection
`project_documents`, strikt pro Projekt gescoped) und stellt sie den Agenten
als `PROJEKT-MATERIAL`-Block im Prompt bereit.

## Unterstützte Formate

| Typ | Endungen | Verarbeitung |
|-----|----------|--------------|
| Dokument | `.txt`, `.md` | Direkt als Text |
| Dokument | `.pdf` | Text-Extraktion via `pypdf` |
| Dokument | `.docx` | Absätze + Tabellen via `python-docx` |
| Bild | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` | Metadaten via Pillow; die **Beschreibung** wird indexiert |

Maximalgröße: 20 MB (`UPLOAD_MAX_BYTES`). Ablage unter `/data/uploads/{project_id}/`
(Docker-Volume `debate-uploads`), Metadaten in PostgreSQL (`project_documents`).

!!! tip "Bilder"
    Da keine OCR/Vision-Pipeline läuft, ist bei Bildern die Beschreibung der
    einzige indexierbare Text. Ohne Beschreibung ist ein Bild in der Debatte
    nicht auffindbar (Badge „nicht indexiert" im Dashboard).

## API-Endpunkte

Alle Endpunkte erfordern JWT-Authentifizierung und prüfen die Projekt-Ownership.

```
POST   /api/v2/projects/{project_id}/documents          (multipart: file, description)
GET    /api/v2/projects/{project_id}/documents
GET    /api/v2/projects/{project_id}/documents/{doc_id}/download
DELETE /api/v2/projects/{project_id}/documents/{doc_id}
```

Beispiel:

```bash
curl -X POST "http://localhost:8106/api/v2/projects/$PROJECT_ID/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@studie.pdf" \
  -F "description=Meta-Studie zur Motion, Kapitel 3 relevant"
```

## Chunking & Retrieval

- Chunks: ~1200 Zeichen mit 200 Zeichen Überlappung, geschnitten an Absatz-/Satzgrenzen
- Retrieval: Top-4 Chunks pro Turn, Query = Motion + letzter Redebeitrag
- Beim Löschen eines Dokuments werden Datei, DB-Eintrag und alle Index-Chunks entfernt

## Dashboard

Im Projektbereich öffnet der Button **„📎 Material"** ein Modal mit Upload-Formular,
Materialliste, Download- und Löschfunktion.
