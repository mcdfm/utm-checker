import os
from pathlib import Path
import re
import uvicorn
import requests
from urllib.parse import urlparse, unquote  # Korrekter Import für die benötigten Funktionen
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from pydantic import BaseModel, field_validator
from fastapi.responses import JSONResponse

# Sicherere JSON-Datei-Ladung
try:
    sources_path = Path('sources.json')
    if not sources_path.exists():
        raise FileNotFoundError("sources.json nicht gefunden")
        
    with open(sources_path, 'r', encoding='utf-8') as file:
        source_categories = json.load(file)
except Exception as e:
    print(f"Fehler beim Laden von sources.json: {e}")
    source_categories = {}  # Fallback leeres Dict

app = FastAPI() 

# CORS Middleware hinzufügen
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://frankmickeler.com",
        "http://localhost:3000",  # Für lokale Entwicklung
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Nur benötigte Methoden erlauben
    allow_headers=["Content-Type"],  # Nur benötigte Header erlauben
)

paid_medium_regex = re.compile(r'^(.*cp.*|ppc|retargeting|paid.*)$', re.IGNORECASE)

# Channel Rules (Fallback):
channel_rules = [
    # Direct: source = direct oder none, medium = none
    (r'.*', r'^(direct|none)$', r'^none$', r'.*', 'Direct'),
    # Cross-network
    (r'.*', r'.*', r'.*', r'.*cross-network.*', 'Cross-network'),
    # Paid Shopping (Fallback über Kampagnennamen + Medium)
    (r'.*', r'.*', r'^(.*cp.*|ppc|retargeting|paid.*)$', r'^(.*(([^a-df-z]|^)shop|shopping).*)$', 'Paid Shopping')
]

def get_channel(
    utm_source: str | None, 
    utm_medium: str | None, 
    utm_campaign: str | None = None, 
    source_platform: str | None = None
) -> str:
    sp = source_platform.strip() if source_platform else ""
    us = utm_source.strip() if utm_source else "none"
    um = utm_medium.strip() if utm_medium else "none"
    uc = utm_campaign.strip() if utm_campaign else "none"

    category = source_categories.get(us.lower())

    # 1. Spezifische Paid-Kategorien prüfen
    if category == "SOURCE_CATEGORY_SEARCH" and paid_medium_regex.match(um):
        return "Paid Search"
    if category == "SOURCE_CATEGORY_SOCIAL" and paid_medium_regex.match(um):
        return "Paid Social"
    if category == "SOURCE_CATEGORY_VIDEO" and paid_medium_regex.match(um):
        return "Paid Video"
    if category == "SOURCE_CATEGORY_SHOPPING" and paid_medium_regex.match(um):
        return "Paid Shopping"

    # 2. Paid Other als Fallback prüfen
    # Greift nur, wenn Medium paid ist und keine spezifische Paid-Kategorie zutraf
    if paid_medium_regex.match(um):
        return "Paid Other"

    # 3. Weitere Regeln (Organic, Referral, etc.)
    if (category == "SOURCE_CATEGORY_SHOPPING" and not paid_medium_regex.match(um)) or re.match(r'^(.*(([^a-df-z]|^)shop|shopping).*)$', uc, re.IGNORECASE):
        return "Organic Shopping"
    if (category == "SOURCE_CATEGORY_SOCIAL" and not paid_medium_regex.match(um)) or um in ["social", "social-network", "social-media", "sm", "social network", "social media"]:
        return "Organic Social"
    if (category == "SOURCE_CATEGORY_VIDEO" and not paid_medium_regex.match(um)) or re.match(r'.*video.*', um, re.IGNORECASE):
        return "Organic Video"
    # Display vor Organic Search
    if um in ["display", "banner", "expandable", "interstitial", "cpm"]:
        return "Display"
    if category == "SOURCE_CATEGORY_SEARCH" or um == "organic":
        return "Organic Search"
    if um in ["referral", "app", "link"]:
        return "Referral"
    if re.match(r'^(email|e-mail|e_mail|e mail)$', us, re.IGNORECASE) or re.match(r'^(email|e-mail|e_mail|e mail)$', um, re.IGNORECASE):
        return "Email"
    if um == "affiliate":
        return "Affiliates"
    if um == "audio":
        return "Audio"
    if us == "sms" or um == "sms":
        return "SMS"
    if um.endswith("push") or "mobile" in um or "notification" in um or us == "firebase":
        return "Mobile Push Notifications"

    # 4. Fallback-Regeln prüfen
    for sp_pattern, source_pattern, medium_pattern, campaign_pattern, channel in channel_rules:
        if (re.match(sp_pattern, sp, re.IGNORECASE) and
            re.match(source_pattern, us, re.IGNORECASE) and
            re.match(medium_pattern, um, re.IGNORECASE) and
            re.match(campaign_pattern, uc, re.IGNORECASE)):
            return channel

    # 5. Wenn keine Regel zutrifft: Unassigned
    return "Unassigned"

class UTMParams(BaseModel):
    url: str

    @field_validator("url")
    def validate_url(cls, value):
        if len(value) > 2000:
            raise ValueError("URL ist zu lang (max. 2000 Zeichen)")
        
        # Protokoll hinzufügen wenn nötig
        if not value.startswith(('http://', 'https://')):
            value = "https://" + value

        # Basis-URL-Validierung
        try:
            parsed = urlparse(value)
            if not all([parsed.scheme, parsed.netloc]):
                raise ValueError
        except Exception:
            raise ValueError("Ungültige URL-Format")
            
        return value


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail)},
    )

@app.get("/")
def read_root():
    return {"message": "UTM Checker is running!"}

@app.post("/check_utm")
async def check_utm(params: UTMParams):
    try:
        print("Received URL:", params.url)
        url_string = str(params.url)

        # URL-Validierung
        parsed_url = urlparse(url_string)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise HTTPException(status_code=400, detail="Ungültige URL. Bitte geben Sie eine vollständige URL mit http/https an.")
        
        # Verbesserte Behandlung leerer Query-Parameter
        url_query = parsed_url.query
        if not url_query:
            return JSONResponse(content={
                "utm_source": "none",
                "utm_medium": "none",
                "utm_campaign": "none",
                "channel": get_channel(None, None, None),
                "warning": "Keine UTM-Parameter gefunden"
            })

        # Sicherere Parameter-Extraktion
        try:
            utm_params = dict(param.split('=', 1) for param in url_query.split('&') if '=' in param)
        except Exception:
            raise HTTPException(status_code=400, detail="Fehler beim Parsen der URL-Parameter")

        # URL-Parameter dekodieren
        raw_utm_source = utm_params.get('utm_source', None)
        utm_source = unquote(raw_utm_source) if raw_utm_source else None
        print("Decoded utm_source:", utm_source)  # Debugging message
        raw_utm_medium = utm_params.get('utm_medium', None)
        utm_medium = unquote(raw_utm_medium) if raw_utm_medium else None
        print("Decoded utm_medium:", utm_medium)  # Debugging message
        raw_utm_campaign = utm_params.get('utm_campaign', None)
        utm_campaign = unquote(raw_utm_campaign) if raw_utm_campaign else None
        print("Decoded utm_campaign:", utm_campaign)  # Debugging message

        # Prüfen auf Anführungszeichen in den Parametern
        for val in [utm_source, utm_medium, utm_campaign]:
            if val and ('"' in val or "'" in val):
                raise HTTPException(status_code=400, detail="Anführungszeichen in UTM Parametern können zu Problemen führen und sollten vermieden werden")

        # Prüfen auf Großbuchstaben in den Parametern
        uppercase_warnings = []
        for key, val in {k: v for k, v in utm_params.items() if k in ['utm_source', 'utm_medium', 'utm_campaign']}.items():
            if any(c.isupper() for c in val):
                print(f"Uppercase detected in {key}: {val}")  # Debugging message
                uppercase_warnings.append(key)

        channel = get_channel(utm_source, utm_medium, utm_campaign)

        response = {
            "utm_source": utm_source if utm_source else "none",
            "utm_medium": utm_medium if utm_medium else "none",
            "utm_campaign": utm_campaign if utm_campaign else "none",
            "channel": channel
        }

        if uppercase_warnings:
            response["warning"] = f"Die folgenden UTM-Parameter enthalten Großbuchstaben; das sollte vermieden werden: {', '.join(uppercase_warnings)}"

        print("Final Response:", response)  # Debugging message
        return JSONResponse(content=response)

    except HTTPException as e:
        raise e  # Bereits gefangene Ausnahmen erneut werfen
    except Exception as e:
        print("Error details:", str(e))  # Debugging message
        raise HTTPException(status_code=422, detail=f"Error parsing URL or parameters: {str(e)}")


if __name__ == "__main__":
    uvicorn.run("Default_channel_group:app", host="0.0.0.0", port=8800)

