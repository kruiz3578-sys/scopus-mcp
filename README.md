# Conector MCP de Scopus

Servidor MCP que permite a Claude buscar en Scopus usando tu API key.

## Herramientas
- `scopus_count`: cuenta resultados de una ecuación (para afinarla).
- `scopus_search`: descarga documentos; devuelve ecuación, fecha y total (datos para PRISMA).
- `scopus_get_abstract`: abstract y metadatos por DOI.
- `open_access_fulltext`: dado un DOI, busca una versión legal en acceso abierto (Unpaywall) y devuelve el texto del PDF por tramos. Si no hay versión abierta, lo indica y hay que subir el PDF a mano.

## Variables de entorno
| Variable | Obligatoria | Descripción |
|---|---|---|
| `SCOPUS_API_KEY` | Sí | Tu key de https://dev.elsevier.com |
| `MCP_SECRET` | Sí | Texto aleatorio de 16+ caracteres. Va en la URL, así solo tú puedes usar el conector |
| `SCOPUS_INSTTOKEN` | No | Institutional token, si la biblioteca de la USIL te lo da |
| `UNPAYWALL_EMAIL` | Para texto completo | Tu correo real; Unpaywall lo pide para identificar las consultas (gratis, hasta 100 000 consultas/día) |

Genera el secreto con:
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Exportar la búsqueda a tu plantilla PRISMA (sin modificarla)
`export_to_template.py` descarga la búsqueda completa y genera un Excel aparte
con las columnas y el orden exactos de la hoja «1.3 Identificación»:

```
pip install -r requirements.txt
export SCOPUS_API_KEY="tu_key"
python export_to_template.py --query-file ecuacion.txt \
  --filters "2015–2026; Article o Conference paper; inglés o español" \
  --out scopus_para_plantilla.xlsx --enrich
```
El Excel trae tres hojas:
- **Pegar en 1.3**: copia A4:M(última fila) y pégalo en tu plantilla desde A4.
- **Datos para 1.2**: ecuación, fecha (dd/mm/aaaa, hora de Lima), filtros y N° de resultados.
- **Extras**: ID Scopus, acceso abierto, citas y enlace (no van en la plantilla).

Notas:
- «País del estudio» queda vacío: se completa al leer cada estudio.
- Editorial e Idioma solo se llenan con `--enrich` y si tu acceso los entrega.
- Resumen y palabras clave requieren la vista completa; si Scopus responde 401/403
  hace falta el institutional token de la biblioteca (`SCOPUS_INSTTOKEN`) o usar
  `--no-abstracts`.
- Pon los filtros (años, idioma, tipo de documento) dentro de la ecuación, con
  `PUBYEAR`, `LANGUAGE` y `DOCTYPE`, para que el N° de resultados sea «tras filtros».

## Probar en tu computadora
```
pip install -r requirements.txt
export SCOPUS_API_KEY="tu_key"
export MCP_SECRET="tu_secreto"
python server.py
```
(En Windows PowerShell: `$env:SCOPUS_API_KEY="tu_key"`)

## Desplegar (ejemplo con Render)
1. Sube esta carpeta a un repositorio de GitHub (privado).
2. En Render: New → Web Service → elige el repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python server.py`
5. En Environment agrega `SCOPUS_API_KEY` y `MCP_SECRET` (aquí pegas tu key, no en el chat).
6. Cuando termine, tu URL del conector será:
   `https://TU-SERVICIO.onrender.com/TU_SECRETO/mcp`

## Conectar en Claude
Configuración → Conectores → Agregar conector personalizado → pega esa URL.

## Seguridad
- La URL con el secreto equivale a una contraseña: no la compartas.
- Si se filtra, cambia `MCP_SECRET` en Render y actualiza el conector.
- Respeta los términos de uso de la API de Elsevier (uso académico, límites de cuota).
