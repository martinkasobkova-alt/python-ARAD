from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import anthropic
import os

app = FastAPI(title="ARAD Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

ARAD_BASE = "https://www.cnb.cz/aradb/api/v1"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


@app.get("/")
def root():
    return {"status": "ok", "service": "ARAD Dashboard API"}


@app.get("/api/arad/{endpoint}")
async def arad_proxy(endpoint: str, request_params: str = Query(default="")):
    """Proxy pro ČNB ARAD API — přeposílá požadavek na cnb.cz"""
    from starlette.requests import Request
    return {"error": "use /api/data or /api/indicators"}


@app.get("/api/data")
async def get_data(
    indicator_id_list: str = Query(...),
    period_from: str = Query(default="20200101"),
    period_to: str = Query(default=""),
    api_key: str = Query(...),
    delimiter: str = Query(default="comma"),
):
    params = {
        "indicator_id_list": indicator_id_list,
        "period_from": period_from,
        "api_key": api_key,
        "delimiter": delimiter,
        "lang": "cs",
    }
    if period_to:
        params["period_to"] = period_to

    url = f"{ARAD_BASE}/data"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, params=params)
            # ARAD vrací CP1250
            text = resp.content.decode("cp1250", errors="replace")

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=text)

            # Parsuj CSV → JSON
            rows = parse_csv(text)
            return JSONResponse(content={"ok": True, "data": rows, "raw": text[:500]})

        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Nelze se připojit k ČNB: {str(e)}")


@app.get("/api/indicators")
async def get_indicators(
    set_id: str = Query(default=""),
    api_key: str = Query(...),
    lang: str = Query(default="cs"),
):
    params = {"api_key": api_key, "lang": lang, "delimiter": "comma"}
    if set_id:
        params["set_id"] = set_id

    url = f"{ARAD_BASE}/indicators"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, params=params)
            text = resp.content.decode("cp1250", errors="replace")
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=text)
            rows = parse_csv(text)
            return JSONResponse(content={"ok": True, "indicators": rows})
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/comment")
async def generate_comment(body: dict):
    """Vygeneruje AI komentář k datům přes Anthropic API"""
    claude_key = body.get("claude_key") or ANTHROPIC_API_KEY
    if not claude_key:
        raise HTTPException(status_code=400, detail="Chybí Anthropic API klíč")

    indicator_name = body.get("name", "neznámý ukazatel")
    unit = body.get("unit", "")
    real_id = body.get("real_id", "")
    period_from = body.get("period_from", "")
    period_to = body.get("period_to", "")
    last_val = body.get("last_value")
    first_val = body.get("first_value")
    min_val = body.get("min_value")
    max_val = body.get("max_value")
    avg_val = body.get("avg_value")
    sample = body.get("sample", "")
    obs_count = body.get("obs_count", 0)

    prompt = f"""Analyzuj tento ukazatel z dat ČNB ARAD:

Ukazatel: {indicator_name} (ID: {real_id})
Jednotka: {unit}
Období: {period_from} až {period_to}
Počet pozorování: {obs_count}
První hodnota: {first_val}
Poslední hodnota: {last_val}
Minimum: {min_val}, Maximum: {max_val}, Průměr: {avg_val}
Posledních 12 hodnot: {sample}

Napiš 2-3 odstavce: (1) co ukazatel říká o aktuálním stavu, (2) trendy a zajímavé body v datech, (3) kontext a možné příčiny. Buď konkrétní, vyhýbej se obecnostem."""

    try:
        client = anthropic.Anthropic(api_key=claude_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system="Jsi analytik české centrální banky. Piš stručně, věcně, v češtině. Odpovídej pouze odstavci textu bez nadpisů nebo odrážek.",
            messages=[{"role": "user", "content": prompt}]
        )
        text = message.content[0].text
        return {"ok": True, "comment": text}

    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="Neplatný Anthropic API klíč")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def parse_csv(text: str) -> list[dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    headers = [h.strip('"') for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        cols = [c.strip('"') for c in line.split(",")]
        if len(cols) >= 2:
            rows.append(dict(zip(headers, cols)))
    return rows
