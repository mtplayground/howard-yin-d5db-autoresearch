from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from app.db.models import KnowledgeItem

BIB_ENV_RE = re.compile(r"\\begin\{thebibliography\}\{[^}]*\}.*?\\end\{thebibliography\}", re.DOTALL)
CITE_RE = re.compile(r"\\cite[a-zA-Z*]*?(?:\[[^\]]*\])?(?:\[[^\]]*\])?\{([^}]+)\}")
RELATED_WORK_RE = re.compile(r"(\\section\*?\{Related Work\})", re.IGNORECASE)
END_DOCUMENT = "\\end{document}"
LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


@dataclass(frozen=True)
class BibliographyEntry:
    key: str
    title: str
    authors: list[str]
    year: str | None
    url: str | None
    source: str | None = None
    source_id: str | None = None
    knowledge_item_id: str | None = None
    raw: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "url": self.url,
            "source": self.source,
            "source_id": self.source_id,
            "knowledge_item_id": self.knowledge_item_id,
            "raw": self.raw or {},
        }


@dataclass(frozen=True)
class BibliographyAssembly:
    entries: list[BibliographyEntry]
    cited_keys: list[str]
    bibtex: str
    thebibliography: str


def citation_key_for_knowledge_item(item: KnowledgeItem) -> str:
    year = _year(item.published_at or item.source_updated_at)
    first_author = _first_author_slug(item.authors)
    title_slug = _slug(item.title, default="work")
    digest = hashlib.sha1(f"{item.source}:{item.source_id}:{item.title}".encode("utf-8")).hexdigest()[:8]
    parts = [part for part in [first_author, year, title_slug, digest] if part]
    return _clean_key("-".join(parts))


def assemble_bibliography(
    knowledge_items: Sequence[KnowledgeItem],
    model_entries: Sequence[dict[str, Any]],
) -> BibliographyAssembly:
    entries: list[BibliographyEntry] = []
    seen: dict[str, int] = {}

    for raw_entry in model_entries:
        entry = _entry_from_model(raw_entry)
        if entry is not None:
            _append_or_merge(entries, seen, entry)

    for item in knowledge_items:
        _append_or_merge(entries, seen, _entry_from_knowledge_item(item))

    entries = _dedupe_keys(entries)
    cited_keys = [entry.key for entry in entries]
    return BibliographyAssembly(
        entries=entries,
        cited_keys=cited_keys,
        bibtex=_bibtex(entries),
        thebibliography=_thebibliography(entries),
    )


def inject_bibliography(latex_source: str, assembly: BibliographyAssembly) -> str:
    if not assembly.entries:
        return latex_source
    with_citations = _ensure_related_work_citations(latex_source, assembly.cited_keys)
    if BIB_ENV_RE.search(with_citations):
        return BIB_ENV_RE.sub(lambda _: assembly.thebibliography, with_citations, count=1)
    end_at = with_citations.rfind(END_DOCUMENT)
    if end_at == -1:
        return with_citations.rstrip() + "\n\n" + assembly.thebibliography + "\n"
    return with_citations[:end_at].rstrip() + "\n\n" + assembly.thebibliography + "\n" + with_citations[end_at:]


def _entry_from_model(raw_entry: dict[str, Any]) -> BibliographyEntry | None:
    title = _text(raw_entry.get("title"))
    if not title:
        return None
    key = _clean_key(_text(raw_entry.get("key")) or _fallback_key(title, _text(raw_entry.get("url"))))
    authors = _authors(raw_entry.get("authors") or raw_entry.get("author"))
    year = _text(raw_entry.get("year"))
    url = _text(raw_entry.get("url") or raw_entry.get("doi"))
    return BibliographyEntry(
        key=key,
        title=title,
        authors=authors,
        year=year,
        url=url,
        source=_text(raw_entry.get("source")),
        source_id=_text(raw_entry.get("source_id")),
        knowledge_item_id=_text(raw_entry.get("knowledge_item_id")),
        raw=dict(raw_entry),
    )


def _entry_from_knowledge_item(item: KnowledgeItem) -> BibliographyEntry:
    return BibliographyEntry(
        key=citation_key_for_knowledge_item(item),
        title=item.title,
        authors=list(item.authors or []),
        year=_year(item.published_at or item.source_updated_at),
        url=item.url,
        source=item.source,
        source_id=item.source_id,
        knowledge_item_id=str(item.id),
        raw={
            "code_repository_url": item.code_repository_url,
            "canonical_key": item.canonical_key,
        },
    )


def _append_or_merge(entries: list[BibliographyEntry], seen: dict[str, int], entry: BibliographyEntry) -> None:
    identity = _identity(entry)
    if identity not in seen:
        seen[identity] = len(entries)
        entries.append(entry)
        return
    index = seen[identity]
    existing = entries[index]
    entries[index] = BibliographyEntry(
        key=existing.key,
        title=existing.title or entry.title,
        authors=existing.authors or entry.authors,
        year=existing.year or entry.year,
        url=existing.url or entry.url,
        source=existing.source or entry.source,
        source_id=existing.source_id or entry.source_id,
        knowledge_item_id=existing.knowledge_item_id or entry.knowledge_item_id,
        raw={**(entry.raw or {}), **(existing.raw or {})},
    )


def _identity(entry: BibliographyEntry) -> str:
    if entry.url:
        return f"url:{entry.url.strip().lower()}"
    if entry.source and entry.source_id:
        return f"source:{entry.source.lower()}:{entry.source_id.lower()}"
    return f"title:{entry.title.strip().casefold()}"


def _dedupe_keys(entries: Sequence[BibliographyEntry]) -> list[BibliographyEntry]:
    counts: dict[str, int] = {}
    deduped: list[BibliographyEntry] = []
    for entry in entries:
        base_key = entry.key
        count = counts.get(base_key, 0)
        counts[base_key] = count + 1
        key = base_key if count == 0 else f"{base_key}-{count + 1}"
        deduped.append(
            BibliographyEntry(
                key=key,
                title=entry.title,
                authors=entry.authors,
                year=entry.year,
                url=entry.url,
                source=entry.source,
                source_id=entry.source_id,
                knowledge_item_id=entry.knowledge_item_id,
                raw=entry.raw,
            )
        )
    return deduped


def _ensure_related_work_citations(latex_source: str, cited_keys: Sequence[str]) -> str:
    missing = [key for key in cited_keys if key not in _existing_cite_keys(latex_source)]
    if not missing:
        return latex_source
    citation = "\nWe organize the related work around the automatically assembled references~\\cite{" + ",".join(missing) + "}.\n"
    match = RELATED_WORK_RE.search(latex_source)
    if match:
        insert_at = match.end()
        return latex_source[:insert_at] + citation + latex_source[insert_at:]
    begin_document = latex_source.find("\\begin{document}")
    if begin_document == -1:
        return citation.lstrip("\n") + latex_source
    insert_at = begin_document + len("\\begin{document}")
    return latex_source[:insert_at] + "\n\\section{Related Work}" + citation + latex_source[insert_at:]


def _existing_cite_keys(latex_source: str) -> set[str]:
    keys: set[str] = set()
    for match in CITE_RE.finditer(latex_source):
        keys.update(key.strip() for key in match.group(1).split(",") if key.strip())
    return keys


def _thebibliography(entries: Sequence[BibliographyEntry]) -> str:
    rows = [rf"\begin{{thebibliography}}{{{max(1, len(entries))}}}"]
    for entry in entries:
        details = []
        if entry.authors:
            details.append(_latex_escape(", ".join(entry.authors)))
        if entry.year:
            details.append(_latex_escape(entry.year))
        details.append(rf"\emph{{{_latex_escape(entry.title)}}}")
        if entry.url:
            details.append(rf"\url{{{_latex_url(entry.url)}}}")
        rows.append(rf"\bibitem{{{entry.key}}} " + ". ".join(details) + ".")
    rows.append(r"\end{thebibliography}")
    return "\n".join(rows)


def _bibtex(entries: Sequence[BibliographyEntry]) -> str:
    return "\n\n".join(_bibtex_entry(entry) for entry in entries)


def _bibtex_entry(entry: BibliographyEntry) -> str:
    fields = {
        "title": entry.title,
        "author": " and ".join(entry.authors) if entry.authors else None,
        "year": entry.year,
        "url": entry.url,
    }
    body = "\n".join(
        f"  {field} = {{{_bibtex_escape(value)}}},"
        for field, value in fields.items()
        if value
    )
    return f"@misc{{{entry.key},\n{body}\n}}"


def _fallback_key(title: str, url: str | None) -> str:
    digest_source = f"{title}:{url or ''}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(title, default='ref')}-{digest}"


def _clean_key(value: str) -> str:
    key = re.sub(r"[^A-Za-z0-9:_-]+", "-", value.strip())
    key = re.sub(r"-+", "-", key).strip("-")
    return key or "ref"


def _slug(value: str, *, default: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value.lower())
    return "-".join(words[:3]) or default


def _first_author_slug(authors: Sequence[str]) -> str:
    if not authors:
        return ""
    parts = re.findall(r"[A-Za-z0-9]+", authors[0].lower())
    return parts[-1] if parts else ""


def _year(value: datetime | None) -> str | None:
    if value is None:
        return None
    return str(value.year)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def _authors(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value) or ""] if _text(value) else []
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        text = _text(item)
        if text:
            authors.append(text)
    return authors


def _latex_escape(value: str) -> str:
    return "".join(LATEX_SPECIALS.get(char, char) for char in value)


def _latex_url(value: str) -> str:
    return value.replace("\\", "%5C").replace("}", "%7D").replace("{", "%7B")


def _bibtex_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
