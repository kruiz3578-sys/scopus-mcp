#!/usr/bin/env python3
"""Descarga una búsqueda completa de Scopus y la guarda en un Excel con las
columnas y el orden de la hoja «1.3 Identificación» de la plantilla PRISMA.

La plantilla NO se modifica: se genera un archivo aparte del que copias y
pegas los datos.

Uso:
  export SCOPUS_API_KEY="tu_key"            # (PowerShell: $env:SCOPUS_API_KEY="tu_key")
  export SCOPUS_INSTTOKEN="..."             # opcional
  python export_to_template.py --query-file ecuacion.txt \
      --filters "2015–2026; Article o Conference paper; inglés o español" \
      --out scopus_para_plantilla.xlsx [--enrich] [--no-abstracts] [--max 2000]

Opciones:
  --enrich        consulta cada registro por separado para intentar llenar
                  Editorial e Idioma (más lento; si tu acceso no los entrega,
                  quedan vacíos).
  --no-abstracts  usa la vista básica (más rápida, sin resumen ni palabras
                  clave). Por defecto se pide la vista completa.
"""
import argparse
import os
import sys
import time

import httpx
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from scopus_common import (
    TEMPLATE_COLUMNS,
    hoy_lima,
    parse_abstract_response,
    to_record,
)

BASE = "https://api.elsevier.com/content"
EXCEL_CELL_LIMIT = 32000


def build_headers() -> dict:
    key = os.environ.get("SCOPUS_API_KEY", "")
    if not key:
        raise SystemExit("Falta la variable de entorno SCOPUS_API_KEY")
    h = {"X-ELS-APIKey": key, "Accept": "application/json"}
    token = os.environ.get("SCOPUS_INSTTOKEN", "")
    if token:
        h["X-ELS-Insttoken"] = token
    return h


def get_with_retries(client, url, headers, params, tries=5):
    for attempt in range(tries):
        r = client.get(url, headers=headers, params=params)
        if r.status_code == 200:
            return r
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 2 * (attempt + 1)))
            print(f"  Scopus respondió {r.status_code}; reintento en {wait}s…")
            time.sleep(wait)
            continue
        return r
    return r


def fetch_all(client, query, max_results, view, headers, pause=0.3):
    # Páginas de 25 y paginación por «start»: funciona con cualquier key
    # (el parámetro «cursor» exige permisos que las keys básicas no tienen).
    page_size = 25
    records, total, start = [], None, 0
    quota = None
    while len(records) < max_results:
        params = {
            "query": query,
            "start": start,
            "count": min(page_size, max_results - len(records)),
            "view": view,
            "sort": "-coverDate",
        }
        r = get_with_retries(client, f"{BASE}/search/scopus", headers, params)
        if r.status_code == 401 and view == "COMPLETE" and not records:
            print(
                "AVISO: tu key no puede pedir la vista completa (resumen y palabras "
                "clave). Continúo con la vista básica: esas dos columnas quedarán "
                "vacías. Para llenarlas hace falta el institutional token "
                "(SCOPUS_INSTTOKEN) o exportar el CSV desde la web de Scopus."
            )
            view = "STANDARD"
            continue
        if r.status_code != 200:
            hint = ""
            if r.status_code in (401, 403):
                hint = (
                    "\nProbable causa: tu key no tiene acceso a la vista completa "
                    "(resumen y palabras clave). Pide a la biblioteca el "
                    "institutional token (SCOPUS_INSTTOKEN) o usa --no-abstracts."
                )
            raise SystemExit(f"Scopus respondió {r.status_code}: {r.text[:300]}{hint}")
        quota = r.headers.get("X-RateLimit-Remaining", quota)
        data = r.json()["search-results"]
        if total is None:
            total = int(data.get("opensearch:totalResults", 0))
            print(f"Scopus reporta {total} resultados para la ecuación.")
        entries = data.get("entry", [])
        if not entries or (len(entries) == 1 and "error" in entries[0]):
            break
        records.extend(to_record(e) for e in entries)
        print(f"  descargados {len(records)} / {min(total, max_results)}")
        start += len(entries)
        if start >= total:
            break
        if start >= 5000:
            print("AVISO: Scopus limita a 5000 resultados por búsqueda; divide la "
                  "ecuación por años (PUBYEAR) para descargar el resto.")
            break
        time.sleep(pause)
    return records, total or 0, quota


def enrich(client, records, headers, pause=0.2):
    """Intenta completar Editorial e Idioma consultando cada registro."""
    done = 0
    for i, rec in enumerate(records, start=1):
        if rec.get("DOI"):
            url = f"{BASE}/abstract/doi/{rec['DOI']}"
        elif rec.get("scopus_id"):
            url = f"{BASE}/abstract/scopus_id/{rec['scopus_id']}"
        else:
            continue
        r = get_with_retries(client, url, headers, {"view": "META"})
        if r.status_code == 200:
            extra = parse_abstract_response(r.json())
            if extra["publisher"]:
                rec["Editorial"] = extra["publisher"]
            if extra["language"]:
                rec["Idioma"] = extra["language"]
            done += 1
        if i % 50 == 0:
            print(f"  enriquecidos {i} / {len(records)}")
        time.sleep(pause)
    return done


def _cell(value):
    if isinstance(value, str) and len(value) > EXCEL_CELL_LIMIT:
        return value[:EXCEL_CELL_LIMIT] + "…"
    return value


def write_xlsx(path, records, query, filters, total):
    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F4E78")
    head_font = Font(bold=True, color="FFFFFF")

    # --- Hoja para pegar en «1.3 Identificación» (mismas posiciones) ---
    ws = wb.active
    ws.title = "Pegar en 1.3"
    last_row = 3 + len(records)
    ws["A1"] = (
        f"Copia A4:M{last_row} y pégalo en la hoja «1.3 Identificación» de tu "
        "plantilla, desde la celda A4. Los encabezados ya coinciden."
    )
    ws["A1"].font = Font(italic=True)
    for col, name in enumerate(TEMPLATE_COLUMNS, start=1):
        c = ws.cell(row=3, column=col, value=name)
        c.fill, c.font = head_fill, head_font
    for n, rec in enumerate(records, start=1):
        row = [n] + [_cell(rec.get(name)) for name in TEMPLATE_COLUMNS[1:]]
        for col, value in enumerate(row, start=1):
            ws.cell(row=3 + n, column=col, value=value)
    for col, width in zip("ABCDEFGHIJKLM", [6, 40, 60, 7, 35, 60, 40, 22, 12, 18, 28, 16, 14]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"

    # --- Datos para la hoja «1.2 Búsqueda» ---
    ws2 = wb.create_sheet("Datos para 1.2")
    headers2 = [
        "Base de datos",
        "Cadena de búsqueda utilizada",
        "Fecha de búsqueda",
        "Filtros aplicados",
        "N° de resultados",
    ]
    for col, name in enumerate(headers2, start=1):
        c = ws2.cell(row=1, column=col, value=name)
        c.fill, c.font = head_fill, head_font
    ws2.append(["Scopus", query, hoy_lima(), filters, total])
    ws2["B2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws2.column_dimensions["B"].width = 90
    ws2.column_dimensions["D"].width = 50
    ws2.column_dimensions["A"].width = 16
    ws2.column_dimensions["C"].width = 18
    ws2.column_dimensions["E"].width = 16
    ws2["A4"] = (
        f"Registros descargados: {len(records)} de {total} reportados por Scopus."
    )

    # --- Datos extra (opcionales, no van en la plantilla) ---
    ws3 = wb.create_sheet("Extras")
    for col, name in enumerate(
        ["N°", "ID Scopus", "Acceso abierto (Scopus)", "Lectura libre (tipo)", "Citas", "Enlace"],
        start=1,
    ):
        c = ws3.cell(row=1, column=col, value=name)
        c.fill, c.font = head_fill, head_font
    for n, rec in enumerate(records, start=1):
        ao = rec.get("acceso_abierto")
        ws3.append(
            [
                n,
                rec.get("scopus_id"),
                None if ao is None else ("Sí" if ao else "No"),
                rec.get("lectura_libre"),
                rec.get("citas"),
                rec.get("enlace"),
            ]
        )
    wb.save(path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--query", help="ecuación de búsqueda (sintaxis Scopus)")
    g.add_argument("--query-file", help="archivo de texto con la ecuación")
    p.add_argument("--filters", default="", help="texto para la columna «Filtros aplicados»")
    p.add_argument("--out", default="scopus_para_plantilla.xlsx")
    p.add_argument("--max", type=int, default=2000, help="máximo de registros a descargar (tope de Scopus: 5000)")
    p.add_argument("--no-abstracts", action="store_true")
    p.add_argument("--enrich", action="store_true")
    args = p.parse_args()

    query = args.query
    if args.query_file:
        with open(args.query_file, encoding="utf-8") as f:
            query = " ".join(f.read().split())
    headers = build_headers()
    view = "STANDARD" if args.no_abstracts else "COMPLETE"

    with httpx.Client(timeout=60) as client:
        records, total, quota = fetch_all(client, query, args.max, view, headers)
        if args.enrich and records:
            print("Consultando editorial e idioma de cada registro…")
            ok = enrich(client, records, headers)
            print(f"  {ok} registros enriquecidos")

    write_xlsx(args.out, records, query, args.filters, total)

    n = len(records) or 1
    pct = lambda k: f"{100 * sum(1 for r in records if r.get(k)) // n}%"
    print(f"\nListo: {len(records)} registros guardados en {args.out}")
    print(
        f"  con resumen: {pct('Resumen (Abstract)')} · con palabras clave: {pct('Palabras clave')} · "
        f"con editorial: {pct('Editorial')} · con idioma: {pct('Idioma')}"
    )
    if quota is not None:
        print(f"  cuota de la API restante (esta semana): {quota}")
    if len(records) < total:
        print(f"  Atención: Scopus reporta {total}; solo se descargaron {len(records)} (límite --max).")
    sys.exit(0)


if __name__ == "__main__":
    main()
