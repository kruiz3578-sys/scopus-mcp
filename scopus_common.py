"""Utilidades compartidas por el servidor MCP y el script de exportación.

Convierten los registros que devuelve Scopus al formato de la plantilla PRISMA
(hoja «1.3 Identificación»), sin modificar la plantilla.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")

# Columnas de la hoja 1.3 Identificación de la plantilla, en su orden exacto.
TEMPLATE_COLUMNS = [
    "N°",
    "Autor(es)",
    "Título",
    "Año",
    "Revista / Fuente",
    "Resumen (Abstract)",
    "Palabras clave",
    "Editorial",
    "Idioma",
    "Tipo de documento",
    "DOI",
    "País del estudio",
    "Base de datos",
]

DOC_TYPES_ES = {
    "Article": "Artículo",
    "Conference Paper": "Conference paper",
    "Review": "Revisión",
    "Book Chapter": "Capítulo de libro",
    "Book": "Libro",
    "Letter": "Carta",
    "Note": "Nota",
    "Short Survey": "Revisión breve",
    "Data Paper": "Data paper",
    "Erratum": "Fe de erratas",
    "Retracted": "Retractado",
}

LANG_ES = {
    "eng": "Inglés",
    "spa": "Español",
    "por": "Portugués",
    "fre": "Francés",
    "fra": "Francés",
    "ger": "Alemán",
    "deu": "Alemán",
    "ita": "Italiano",
    "chi": "Chino",
    "zho": "Chino",
    "rus": "Ruso",
    "jpn": "Japonés",
    "kor": "Coreano",
}


def hoy_lima() -> str:
    """Fecha de hoy en Lima, formato dd/mm/aaaa (como en la hoja 1.2)."""
    return datetime.now(LIMA).strftime("%d/%m/%Y")


def ahora_lima() -> str:
    return datetime.now(LIMA).strftime("%d/%m/%Y %H:%M") + " (hora de Lima)"


def format_authors(entry: dict) -> str | None:
    """«Apellido N.; Apellido N.» con la lista completa si Scopus la entrega;
    si no (vista básica), solo el primer autor."""
    authors = entry.get("author")
    if isinstance(authors, dict):
        authors = [authors]
    names = [
        a.get("authname")
        for a in (authors or [])
        if isinstance(a, dict) and a.get("authname")
    ]
    if names:
        return "; ".join(names)
    return entry.get("dc:creator") or None


def format_keywords(raw) -> str | None:
    """Scopus separa con « | »; la plantilla usa «; »."""
    if not raw:
        return None
    parts = [p.strip() for p in str(raw).split("|") if p.strip()]
    return "; ".join(parts) or None


def doc_type_es(entry: dict) -> str | None:
    d = entry.get("subtypeDescription")
    return DOC_TYPES_ES.get(d, d)


def year_of(entry: dict):
    cd = entry.get("prism:coverDate") or ""
    try:
        return int(cd[:4])
    except ValueError:
        return None


def free_to_read(entry: dict) -> dict:
    flag = entry.get("openaccessFlag")
    if flag is not None:
        flag = str(flag).lower() == "true"
    labels = []
    lab = entry.get("freetoreadLabel")
    if isinstance(lab, dict):
        vals = lab.get("value")
        if isinstance(vals, dict):
            vals = [vals]
        if isinstance(vals, list):
            labels = [v.get("$") for v in vals if isinstance(v, dict) and v.get("$")]
    return {"acceso_abierto": flag, "lectura_libre": "; ".join(labels) or None}


def to_record(entry: dict) -> dict:
    """Entrada de la API de búsqueda → registro con las columnas de la plantilla
    (sin «N°») más datos extra en minúsculas."""
    links = entry.get("link") or []
    url = next(
        (l.get("@href") for l in links if isinstance(l, dict) and l.get("@ref") == "scopus"),
        None,
    )
    rec = {
        "Autor(es)": format_authors(entry),
        "Título": entry.get("dc:title"),
        "Año": year_of(entry),
        "Revista / Fuente": entry.get("prism:publicationName"),
        "Resumen (Abstract)": entry.get("dc:description"),
        "Palabras clave": format_keywords(entry.get("authkeywords")),
        "Editorial": None,  # no viene en la búsqueda; ver parse_abstract_response
        "Idioma": None,  # idem
        "Tipo de documento": doc_type_es(entry),
        "DOI": entry.get("prism:doi"),
        "País del estudio": None,  # se completa al leer el estudio
        "Base de datos": "Scopus",
        "scopus_id": (entry.get("dc:identifier") or "").replace("SCOPUS_ID:", "") or None,
        "citas": entry.get("citedby-count"),
        "enlace": url,
    }
    rec.update(free_to_read(entry))
    return rec


def parse_abstract_response(data: dict) -> dict:
    """Respuesta de Abstract Retrieval → editorial e idioma (si existen)."""
    resp = (data or {}).get("abstracts-retrieval-response") or {}
    core = resp.get("coredata") or {}
    lang = resp.get("language")
    code = lang.get("@xml:lang") if isinstance(lang, dict) else None
    idioma = None
    if code:
        idioma = LANG_ES.get(code, code.upper())
    return {"publisher": core.get("dc:publisher"), "language": idioma}
