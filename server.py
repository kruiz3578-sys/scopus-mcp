"""Servidor MCP para buscar en Scopus (API de Elsevier).

Variables de entorno:
  SCOPUS_API_KEY      (obligatoria) tu API key de dev.elsevier.com
  SCOPUS_INSTTOKEN    (opcional) institutional token, si tu biblioteca te lo dio
  MCP_SECRET          (obligatoria) texto largo y aleatorio; forma parte de la URL
                      del conector para que nadie más pueda usar tu key
  UNPAYWALL_EMAIL     (opcional) tu correo; Unpaywall lo pide para identificar
                      las consultas. Sin él, la herramienta de texto completo
                      no funciona.
  PORT                (opcional) puerto, por defecto 8000
"""
import io
import os

import httpx
from mcp.server.fastmcp import FastMCP
from pypdf import PdfReader

from scopus_common import ahora_lima, hoy_lima, parse_abstract_response, to_record

API_KEY = os.environ.get("SCOPUS_API_KEY", "")
INST_TOKEN = os.environ.get("SCOPUS_INSTTOKEN", "")
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "")
MAX_PDF_BYTES = 25 * 1024 * 1024
SECRET = os.environ.get("MCP_SECRET", "")
PORT = int(os.environ.get("PORT", "8000"))

if not API_KEY:
    raise SystemExit("Falta la variable de entorno SCOPUS_API_KEY")
if len(SECRET) < 16:
    raise SystemExit("MCP_SECRET debe tener al menos 16 caracteres")

BASE = "https://api.elsevier.com/content"

mcp = FastMCP(
    "scopus",
    host="0.0.0.0",
    port=PORT,
    streamable_http_path=f"/{SECRET}/mcp",
    stateless_http=True,
)


def _headers() -> dict:
    h = {"X-ELS-APIKey": API_KEY, "Accept": "application/json"}
    if INST_TOKEN:
        h["X-ELS-Insttoken"] = INST_TOKEN
    return h



@mcp.tool()
async def scopus_search(
    query: str,
    max_results: int = 25,
    sort: str = "relevance",
    include_abstract: bool = False,
) -> dict:
    """Busca documentos en Scopus con sintaxis avanzada de Scopus.

    Ejemplo de query:
      TITLE-ABS-KEY("blockchain" AND (voting OR election*)) AND PUBYEAR > 2017
      AND LANGUAGE(english) AND DOCTYPE(ar)

    Args:
        query: ecuación de búsqueda en sintaxis Scopus.
        max_results: cuántos resultados devolver (máx. 200 por llamada).
        sort: "relevance", "date" (más recientes primero) o "citations".
        include_abstract: si True pide la vista COMPLETE (incluye abstract y
            palabras clave; máx. 25 por página y requiere acceso institucional).

    Devuelve el total de resultados en Scopus, la ecuación y la fecha de la
    búsqueda (datos que PRISMA pide registrar) y la lista de documentos.
    """
    max_results = max(1, min(max_results, 200))
    sort_map = {
        "relevance": "-relevancy",
        "date": "-coverDate",
        "citations": "-citedby-count",
    }
    view = "COMPLETE" if include_abstract else "STANDARD"
    page_size = min(max_results, 25 if include_abstract else 200)

    results: list[dict] = []
    total = None
    cursor = "*"
    async with httpx.AsyncClient(timeout=30) as client:
        while len(results) < max_results:
            params = {
                "query": query,
                "count": min(page_size, max_results - len(results)),
                "sort": sort_map.get(sort, "-relevancy"),
                "view": view,
                "cursor": cursor,
            }
            r = await client.get(f"{BASE}/search/scopus", headers=_headers(), params=params)
            if r.status_code != 200:
                return {
                    "error": f"Scopus respondió {r.status_code}",
                    "detail": r.text[:500],
                    "query": query,
                }
            data = r.json()["search-results"]
            if total is None:
                total = int(data.get("opensearch:totalResults", 0))
            entries = data.get("entry", [])
            if not entries or (len(entries) == 1 and "error" in entries[0]):
                break
            results.extend(to_record(e) for e in entries)
            next_cursor = (data.get("cursor") or {}).get("@next")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    return {
        "database": "Scopus",
        "query": query,
        "fecha_busqueda": hoy_lima(),
        "searched_at": ahora_lima(),
        "total_results_in_scopus": total,
        "returned": len(results),
        "nota": (
            "Los registros usan los nombres de columna de la plantilla PRISMA (hoja 1.3). "
            "Para listas largas usa el script export_to_template.py en vez del chat."
        ),
        "results": results,
    }


@mcp.tool()
async def scopus_count(query: str) -> dict:
    """Devuelve solo el número de resultados de una ecuación de búsqueda.

    Útil para probar y ajustar la ecuación antes de descargar los documentos,
    y para registrar el conteo de identificación del diagrama PRISMA.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE}/search/scopus",
            headers=_headers(),
            params={"query": query, "count": 1},
        )
    if r.status_code != 200:
        return {"error": f"Scopus respondió {r.status_code}", "detail": r.text[:500]}
    total = int(r.json()["search-results"].get("opensearch:totalResults", 0))
    return {
        "query": query,
        "fecha_busqueda": hoy_lima(),
        "searched_at": ahora_lima(),
        "total_results_in_scopus": total,
    }


@mcp.tool()
async def scopus_get_abstract(doi: str) -> dict:
    """Obtiene los metadatos y el abstract de un documento a partir de su DOI."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE}/abstract/doi/{doi}",
            headers=_headers(),
            params={"view": "META_ABS"},
        )
    if r.status_code != 200:
        return {"error": f"Scopus respondió {r.status_code}", "detail": r.text[:500]}
    payload = r.json()
    extra = parse_abstract_response(payload)
    resp = payload.get("abstracts-retrieval-response", {})
    core = resp.get("coredata", {})
    return {
        "publisher": extra["publisher"],
        "language": extra["language"],
        "title": core.get("dc:title"),
        "journal": core.get("prism:publicationName"),
        "date": core.get("prism:coverDate"),
        "doi": core.get("prism:doi"),
        "cited_by": core.get("citedby-count"),
        "abstract": core.get("dc:description"),
        "keywords": [
            k.get("$") for k in (resp.get("authkeywords") or {}).get("author-keyword", [])
        ]
        if isinstance(resp.get("authkeywords"), dict)
        else None,
    }


@mcp.tool()
async def open_access_fulltext(doi: str, offset: int = 0, max_chars: int = 30000) -> dict:
    """Busca una versión legal en acceso abierto de un artículo (vía Unpaywall)
    y devuelve el texto del PDF.

    Si no existe versión abierta, lo indica; en ese caso hay que subir el PDF
    manualmente. El texto se entrega por tramos: usa `offset` para pedir el
    siguiente (el resultado indica `next_offset`).

    Args:
        doi: DOI del artículo (ej. 10.1109/ACCESS.2020.1234567).
        offset: carácter desde el cual empezar a devolver texto.
        max_chars: tamaño del tramo (máx. 60000).
    """
    if not UNPAYWALL_EMAIL:
        return {"error": "Falta la variable de entorno UNPAYWALL_EMAIL en el servidor."}
    doi = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
    max_chars = max(1000, min(max_chars, 60000))

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(
            f"https://api.unpaywall.org/v2/{doi}", params={"email": UNPAYWALL_EMAIL}
        )
        if r.status_code == 404:
            return {"doi": doi, "open_access": False, "note": "Unpaywall no conoce este DOI."}
        if r.status_code != 200:
            return {"error": f"Unpaywall respondió {r.status_code}", "detail": r.text[:300]}
        info = r.json()
        if not info.get("is_oa"):
            return {
                "doi": doi,
                "title": info.get("title"),
                "open_access": False,
                "note": "No hay versión legal en acceso abierto. Sube el PDF manualmente.",
            }

        loc = info.get("best_oa_location") or {}
        meta = {
            "doi": doi,
            "title": info.get("title"),
            "open_access": True,
            "oa_status": info.get("oa_status"),
            "license": loc.get("license"),
            "version": loc.get("version"),
            "landing_page": loc.get("url"),
        }
        pdf_url = loc.get("url_for_pdf")
        if not pdf_url:
            return {**meta, "note": "Hay versión abierta pero sin PDF directo; abre landing_page."}

        pr = await client.get(pdf_url, headers={"User-Agent": f"scopus-mcp ({UNPAYWALL_EMAIL})"})
        if pr.status_code != 200:
            return {**meta, "pdf_url": pdf_url, "note": f"No se pudo descargar el PDF ({pr.status_code})."}
        if len(pr.content) > MAX_PDF_BYTES:
            return {**meta, "pdf_url": pdf_url, "note": "El PDF supera 25 MB; descárgalo y súbelo."}

    try:
        reader = PdfReader(io.BytesIO(pr.content))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:  # PDF corrupto, protegido o no es PDF
        return {**meta, "pdf_url": pdf_url, "note": f"No se pudo leer el PDF: {e}"}

    if not text.strip():
        return {**meta, "pdf_url": pdf_url, "note": "PDF sin texto extraíble (posible escaneo)."}

    chunk = text[offset : offset + max_chars]
    end = offset + len(chunk)
    return {
        **meta,
        "pdf_url": pdf_url,
        "total_chars": len(text),
        "offset": offset,
        "next_offset": end if end < len(text) else None,
        "text": chunk,
    }


if __name__ == "__main__":
    print(f"Conector listo en la ruta /{SECRET[:4]}…/mcp (puerto {PORT})")
    mcp.run(transport="streamable-http")
