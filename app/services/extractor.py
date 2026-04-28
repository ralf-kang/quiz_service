"""Convert uploaded files into plaintext, then split into chunks."""
import io
import re
from typing import Iterable

from pypdf import PdfReader
import docx

try:  # pdfplumber 는 PDF 의 표/이미지 인식을 위해 사용. 미설치면 graceful fallback.
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore


def _table_to_markdown(table: list[list[str | None]]) -> str:
    """pdfplumber 가 뽑은 2 차원 표를 markdown 표로 변환."""
    if not table:
        return ""
    rows: list[list[str]] = []
    for row in table:
        if row is None:
            continue
        cells = [(c or "").replace("\n", " ").replace("|", "/").strip() for c in row]
        # 완전 빈 행 스킵
        if not any(cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    sep = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _from_pdf(data: bytes) -> str:
    """PDF → 텍스트.

    페이지마다 `## p.<n>` 헤더를 붙이고, 표가 있으면 `[TABLE]` 마커와 함께
    markdown 표로 본문에 합친다. 텍스트가 비어 있는데 이미지가 있는
    페이지는 `[IMAGE_PAGE: n]` 마커를 남겨 LLM 이 인지하도록 한다.
    pdfplumber 가 없으면 pypdf fallback 으로 단순 텍스트만 추출.
    """
    if pdfplumber is None:
        return _from_pdf_pypdf(data)

    parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                section: list[str] = [f"## p.{idx}"]

                # 본문 텍스트
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                text = text.strip()
                if text:
                    section.append(text)

                # 표 추출 → markdown
                tables: list[list[list[str | None]]] = []
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                for t in tables:
                    md = _table_to_markdown(t)
                    if md:
                        section.append("\n[TABLE]\n" + md)

                # 이미지 마커: 텍스트가 거의 없는데 이미지가 있으면 추출 한계 표시
                has_images = False
                try:
                    has_images = bool(getattr(page, "images", None))
                except Exception:
                    has_images = False
                if has_images and len(text) < 40:
                    section.append(
                        f"[IMAGE_PAGE: {idx}] (이미지 위주 페이지 — 텍스트 추출 한계)"
                    )

                # 헤더만 있고 알맹이 없는 페이지는 스킵
                if len(section) > 1:
                    parts.append("\n\n".join(section))
    except Exception:
        # pdfplumber 가 못 읽으면 pypdf 로 회귀
        return _from_pdf_pypdf(data)

    return "\n\n".join(parts)


def _from_pdf_pypdf(data: bytes) -> str:
    """pypdf fallback — 페이지별 헤더만 붙여 텍스트를 합친다."""
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        if text:
            parts.append(f"## p.{idx}\n\n{text}")
    return "\n\n".join(parts)


def _from_docx(data: bytes) -> str:
    f = io.BytesIO(data)
    doc = docx.Document(f)
    return "\n".join(p.text for p in doc.paragraphs)


def _from_text(data: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(filename: str, mime: str | None, data: bytes) -> str:
    name = (filename or "").lower()
    mime = (mime or "").lower()

    if name.endswith(".pdf") or "pdf" in mime:
        return _from_pdf(data)
    if name.endswith(".docx") or "officedocument.wordprocessingml" in mime:
        return _from_docx(data)
    # md, txt, anything else → treat as text
    return _from_text(data)


_PARA_SPLIT = re.compile(r"\n\s*\n")


def chunk_text(text: str, size: int = 1000) -> Iterable[str]:
    """Split into ~size-char chunks, preferring paragraph boundaries."""
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        # If a single paragraph is huge, hard-split it
        if len(p) > size:
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_len = [], 0
            for i in range(0, len(p), size):
                chunks.append(p[i : i + size])
            continue

        if buf_len + len(p) + 2 > size and buf:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [], 0
        buf.append(p)
        buf_len += len(p) + 2

    if buf:
        chunks.append("\n\n".join(buf))
    return chunks
