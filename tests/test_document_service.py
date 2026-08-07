"""Tests fuer den DocumentService (Uploads, Extraktion, Chunking, Index)."""

import pytest

from config import settings
from services import document_service
from services.document_service import (
    DocumentIndex,
    UploadValidationError,
    chunk_text,
    extract_content,
    safe_filename,
    validate_upload,
)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path / "chroma"))


class TestValidation:
    def test_allowed_document(self) -> None:
        assert validate_upload("studie.pdf", 1024) == ".pdf"

    def test_allowed_image(self) -> None:
        assert validate_upload("foto.JPG", 1024) == ".jpg"

    def test_rejects_unknown_extension(self) -> None:
        with pytest.raises(UploadValidationError):
            validate_upload("malware.exe", 100)

    def test_rejects_oversize(self) -> None:
        with pytest.raises(UploadValidationError):
            validate_upload("big.pdf", settings.upload_max_bytes + 1)

    def test_rejects_empty(self) -> None:
        with pytest.raises(UploadValidationError):
            validate_upload("leer.txt", 0)


class TestSafeFilename:
    def test_strips_path_components(self) -> None:
        assert "/" not in safe_filename("../../etc/passwd")
        assert safe_filename("../../etc/passwd") == "passwd"

    def test_keeps_umlauts(self) -> None:
        assert safe_filename("Begründung.pdf") == "Begründung.pdf"


class TestChunking:
    def test_empty_text(self) -> None:
        assert chunk_text("") == []

    def test_short_text_single_chunk(self) -> None:
        assert chunk_text("Kurzer Text.") == ["Kurzer Text."]

    def test_long_text_overlapping_chunks(self) -> None:
        text = " ".join(f"Satz Nummer {i} über das Debattenthema." for i in range(200))
        chunks = chunk_text(text, size=500, overlap=100)
        assert len(chunks) > 3
        assert all(len(c) <= 500 for c in chunks)
        # Vollstaendigkeit: letzter Satz ist enthalten
        assert "Nummer 199" in chunks[-1]


class TestExtraction:
    def test_txt_extraction(self, tmp_path) -> None:
        f = tmp_path / "notiz.txt"
        f.write_text("Inhalt der Notiz über Ethik.", encoding="utf-8")
        result = extract_content(f)
        assert result.kind == "document"
        assert "Ethik" in result.text

    def test_description_prepended(self, tmp_path) -> None:
        f = tmp_path / "notiz.md"
        f.write_text("# Titel\nInhalt.", encoding="utf-8")
        result = extract_content(f, description="Kontext der Studie")
        assert result.text.startswith("Kontext der Studie")

    def test_image_uses_description(self, tmp_path) -> None:
        from PIL import Image

        f = tmp_path / "bild.png"
        Image.new("RGB", (10, 10), color="red").save(f)
        result = extract_content(f, description="Diagramm zur CO2-Entwicklung")
        assert result.kind == "image"
        assert result.text == "Diagramm zur CO2-Entwicklung"
        assert result.meta.get("width") == 10

    def test_store_file_writes_bytes(self) -> None:
        path = document_service.store_file("proj-1", "doc-1", "a.txt", b"hallo")
        assert path.read_bytes() == b"hallo"
        assert "proj-1" in str(path)


class TestDocumentIndex:
    @pytest.mark.asyncio
    async def test_index_and_search_scoped_by_project(self, tmp_path) -> None:
        index = DocumentIndex(persist_dir=str(tmp_path / "chroma"))
        n = await index.index_document(
            doc_id="d1", project_id="p1", filename="studie.txt", kind="document",
            text="Die Studie zeigt deutliche Effekte von Bildung auf Demokratie.",
        )
        assert n == 1
        hits_p1 = await index.search("p1", "Bildung Demokratie", top_k=3)
        assert len(hits_p1) == 1
        assert hits_p1[0]["filename"] == "studie.txt"
        # Anderes Projekt sieht nichts
        hits_p2 = await index.search("p2", "Bildung Demokratie", top_k=3)
        assert hits_p2 == []

    @pytest.mark.asyncio
    async def test_remove_document(self, tmp_path) -> None:
        index = DocumentIndex(persist_dir=str(tmp_path / "chroma"))
        await index.index_document(
            doc_id="d2", project_id="p1", filename="alt.txt", kind="document",
            text="Veralteter Inhalt der geloescht wird.",
        )
        await index.remove_document("d2")
        hits = await index.search("p1", "Veralteter Inhalt", top_k=3)
        assert hits == []

    @pytest.mark.asyncio
    async def test_empty_text_not_indexed(self, tmp_path) -> None:
        index = DocumentIndex(persist_dir=str(tmp_path / "chroma"))
        n = await index.index_document(doc_id="d3", project_id="p1", filename="bild.png", kind="image", text="")
        assert n == 0
