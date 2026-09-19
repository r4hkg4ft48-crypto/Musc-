import os, re, time, hashlib, asyncio, json
from datetime import datetime, timezone
from typing import Optional, List, Dict

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware\nfrom fastapi.responses import Response
from telethon import TelegramClient
from telethon.sessions import StringSession

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = FastAPI(title="Volna Live Intelligence", version="0.6.0")
origins=[x.strip() for x in os.getenv("CORS_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=["GET"],allow_headers=["*"])

DEFAULT_LAT=float(os.getenv("DEFAULT_LAT","55.7558"))
DEFAULT_LON=float(os.getenv("DEFAULT_LON","37.6173"))
CACHE_SECONDS=int(os.getenv("LIVE_CACHE_SECONDS","90"))
IMPORTANT_THRESHOLD=float(os.getenv("IMPORTANT_THRESHOLD","0.78"))

TELEGRAM_SOURCES=[
 {"username":"@news_18age","kind":"general","trust":float(os.getenv("TG_TRUST_NEWS18","0.62"))},
 {"username":"@crypto_news_and","kind":"crypto","trust":float(os.getenv("TG_TRUST_CRYPTO","0.60"))},
 {"username":"@ALL_NEWS_MOEX","kind":"moex","trust":float(os.getenv("TG_TRUST_MOEX","0.64"))},
]
for src in [x.strip() for x in os.getenv("TELEGRAM_EXTRA_SOURCES","").split(",") if x.strip()]:
    TELEGRAM_SOURCES.append({"username":src if src.startswith("@") else "@"+src,"kind":"general","trust":float(os.getenv("TG_TRUST_EXTRA","0.55"))})

SOURCE_TRUST={"yandex_weather":0.96,"open_meteo_ecmwf":0.91,"coingecko":0.97}
_cache={"ts":0.0,"key":None,"value":None}\n_profiles={}
_tg=None

URGENT=re.compile(r"(?i)срочно|молни[яи]|экстренн|чрезвычайн|атак|взрыв|землетряс|цунами|эвакуац|обстрел|авари|катастроф|закрыт[ьи]|запрет|ставк[аи]|санкци|банкрот|дефолт")
MARKET=re.compile(r"(?i)bitcoin|btc|ethereum|eth|крипт|биткоин|эфир|ставк[аи]|фрс|цб|рынок|бирж")

def now_iso(): return datetime.now(timezone.utc).isoformat()
def clamp(x,a=0.0,b=1.0): return max(a,min(b,x))
def eid(source,text): return hashlib.sha256((source+"|"+text.strip()).encode()).hexdigest()[:18]
def excerpt(text,n=900): return re.sub(r"\s+"," ",text or "").strip()[:n]

def score_news(text,age,trust,kind):
    s=.20+trust*.38
    if URGENT.search(text): s+=.27
    if MARKET.search(text): s+=.10
    if kind=="crypto" and MARKET.search(text): s+=.08
    if kind=="moex" and re.search(r"(?i)мосбирж|moex|акци|облигац|дивиденд|эмитент|рубл|цб|индекс",text): s+=.09
    if age<15:s+=.08
    elif age<60:s+=.04
    elif age>360:s-=.12
    return clamp(s)

async def get_weather(lat,lon):
    ykey=os.getenv("YANDEX_WEATHER_KEY","").strip()
    async with httpx.AsyncClient(timeout=12) as c:
        if ykey:
            q="""query Weather($lat: Float!, $lon: Float!) { weatherByPoint(request: {lat: $lat, lon: $lon}) { now { temperature feelsLike humidity windSpeed windGust condition } } }"""
            try:
                r=await c.post("https://api.weather.yandex.ru/graphql/query",headers={"X-Yandex-Weather-Key":ykey,"Content-Type":"application/json"},json={"query":q,"variables":{"lat":lat,"lon":lon}})
                r.raise_for_status(); n=r.json()["data"]["weatherByPoint"]["now"]
                return {"source":"yandex_weather","trust":.96,"temperature":n.get("temperature"),"feels_like":n.get("feelsLike"),"humidity":n.get("humidity"),"wind_speed":n.get("windSpeed"),"wind_gust":n.get("windGust"),"condition":n.get("condition"),"observed_at":now_iso()}
            except Exception: pass
        p={"latitude":lat,"longitude":lon,"current":"temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,wind_gusts_10m,precipitation","forecast_days":1,"timezone":"auto"}
        r=await c.get("https://api.open-meteo.com/v1/forecast",params=p); r.raise_for_status(); n=r.json().get("current",{})
        return {"source":"open_meteo_ecmwf","trust":.91,"temperature":n.get("temperature_2m"),"feels_like":n.get("apparent_temperature"),"humidity":n.get("relative_humidity_2m"),"wind_speed":n.get("wind_speed_10m"),"wind_gust":n.get("wind_gusts_10m"),"condition":f"wmo:{n.get('weather_code')}","precipitation":n.get("precipitation"),"observed_at":n.get("time") or now_iso()}

async def get_crypto():
    h={"accept":"application/json"}
    k=os.getenv("COINGECKO_API_KEY","").strip()
    if k:h["x-cg-demo-api-key"]=k
    p={"ids":"bitcoin,ethereum,solana,the-open-network","vs_currencies":"usd","include_24hr_change":"true","include_last_updated_at":"true"}
    async with httpx.AsyncClient(timeout=12) as c:
        r=await c.get("https://api.coingecko.com/api/v3/simple/price",params=p,headers=h); r.raise_for_status(); data=r.json()
    labels={"bitcoin":"BTC","ethereum":"ETH","solana":"SOL","the-open-network":"TON"}
    return {"source":"coingecko","trust":.97,"assets":[{"id":cid,"symbol":labels[cid],"usd":v.get("usd"),"change_24h":float(v.get("usd_24h_change") or 0),"updated_at":v.get("last_updated_at")} for cid,v in data.items()],"observed_at":now_iso()}

async def one_tg_public(cfg,limit=12):
    username=cfg["username"].lstrip("@")
    url=f"https://t.me/s/{username}"
    headers={"User-Agent":"Mozilla/5.0 (compatible; VolnaLive/1.0; +https://volna-live-intelligence.onrender.com)"}
    try:
        async with httpx.AsyncClient(timeout=15,follow_redirects=True,headers=headers) as client:
            r=await client.get(url)
            r.raise_for_status()
    except Exception:
        return []
    soup=BeautifulSoup(r.text,"html.parser")
    wraps=soup.select(".tgme_widget_message_wrap")
    now=datetime.now(timezone.utc); out=[]; source="telegram_"+username.lower()
    for wrap in wraps[-limit:]:
        text_el=wrap.select_one(".tgme_widget_message_text")
        date_el=wrap.select_one("a.tgme_widget_message_date")
        time_el=wrap.select_one("time")
        text=excerpt(text_el.get_text(" ",strip=True) if text_el else "")
        if not text: continue
        href=date_el.get("href") if date_el else None
        dt_raw=time_el.get("datetime") if time_el else None
        try:
            dt=datetime.fromisoformat(dt_raw.replace("Z","+00:00")) if dt_raw else now
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt=now
        age=max(0,(now-dt).total_seconds()/60)
        out.append({"id":eid(source,text),"source":source,"source_name":cfg["username"],"source_kind":cfg["kind"],"trust":cfg["trust"],"confidence":cfg["trust"],"importance":round(score_news(text,age,cfg["trust"],cfg["kind"]),3),"published_at":dt.isoformat(),"text":text,"url":href or f"https://t.me/{username}","access_mode":"public_web"})
    return out

async def get_tg():
    chunks=await asyncio.gather(*[one_tg_public(x) for x in TELEGRAM_SOURCES])
    items=confirm([x for ch in chunks for x in ch])
    items.sort(key=lambda x:(x["published_at"],x["importance"]),reverse=True)
    return items

