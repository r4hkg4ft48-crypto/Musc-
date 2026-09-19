import os, re, time, hashlib, asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from telethon import TelegramClient
from telethon.sessions import StringSession

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = FastAPI(title="Volna Live Intelligence", version="0.5.0")
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
_cache={"ts":0.0,"key":None,"value":None}
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

async def telegram_client():
    global _tg
    if _tg:return _tg
    aid=os.getenv("TELEGRAM_API_ID","").strip(); ah=os.getenv("TELEGRAM_API_HASH","").strip(); sess=os.getenv("TELEGRAM_SESSION","").strip()
    if not(aid and ah and sess): return None
    _tg=TelegramClient(StringSession(sess),int(aid),ah)
    await _tg.connect()
    if not await _tg.is_user_authorized():
        await _tg.disconnect(); _tg=None; return None
    return _tg

async def one_tg(client,cfg,limit=10):
    try:
        ent=await client.get_entity(cfg["username"]); msgs=await client.get_messages(ent,limit=limit)
    except Exception:return []
    now=datetime.now(timezone.utc); out=[]; source="telegram_"+cfg["username"].lstrip("@").lower()
    for m in msgs:
        text=excerpt(getattr(m,"message","") or "")
        if not text:continue
        dt=m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
        age=max(0,(now-dt).total_seconds()/60)
        out.append({"id":eid(source,text),"source":source,"source_name":cfg["username"],"source_kind":cfg["kind"],"trust":cfg["trust"],"confidence":cfg["trust"],"importance":round(score_news(text,age,cfg["trust"],cfg["kind"]),3),"published_at":dt.isoformat(),"text":text,"url":f"https://t.me/{cfg['username'].lstrip('@')}/{m.id}"})
    return out

def tokens(text):
    stop={"который","которые","этого","после","будет","также","сегодня","заявил","сообщил"}
    return {w for w in re.findall(r"[a-zа-яё0-9]{4,}",(text or "").lower()) if w not in stop}

def confirm(items):
    for i,a in enumerate(items):
        ta=tokens(a["text"]); by=[]
        if len(ta)<3:continue
        for j,b in enumerate(items):
            if i==j or a["source"]==b["source"]:continue
            tb=tokens(b["text"])
            if len(tb)>=3 and len(ta&tb)/max(1,min(len(ta),len(tb)))>=.42:by.append(b["source_name"])
        if by:
            a["confirmed_by"]=sorted(set(by))[:3]
            a["confidence"]=round(clamp(a["trust"]+min(.12,.05*len(a["confirmed_by"]))),3)
            a["importance"]=round(clamp(a["importance"]+min(.10,.04*len(a["confirmed_by"]))),3)
    return items

async def get_tg():
    c=await telegram_client()
    if not c:return []
    chunks=await asyncio.gather(*[one_tg(c,x) for x in TELEGRAM_SOURCES])
    items=confirm([x for ch in chunks for x in ch]); items.sort(key=lambda x:(x["published_at"],x["importance"]),reverse=True); return items

def crypto_events(c):
    out=[]
    for a in c["assets"]:
        ch=a["change_24h"]; mag=abs(ch)
        if mag<4:continue
        text=f"{a['symbol']} {'вырос' if ch>0 else 'снизился'} на {mag:.1f}% за 24 часа. Сейчас около ${a['usd']}."
        out.append({"id":eid("coingecko",text),"source":"coingecko","source_name":"CoinGecko","trust":.97,"confidence":.97,"importance":round(clamp(.45+min(mag,20)/40+.97*.15),3),"published_at":now_iso(),"text":text,"url":"https://www.coingecko.com/"})
    return out

def fallback_brief(e):
    t=e["text"][:260]
    return f"Короткое обновление. {t} Источник: {e.get('source_name','источник')}."

def ai_brief(e):
    k=os.getenv("OPENAI_API_KEY","").strip()
    if not(k and OpenAI):return fallback_brief(e)
    try:
        c=OpenAI(api_key=k)
        prompt="Ты редактор музыкального радио. Используй только данный факт. Напиши одну спокойную русскую эфирную вставку на 12–28 секунд, без домыслов и сенсационности. В конце кратко назови источник.\nИсточник: "+str(e.get("source_name"))+"\nФакт: "+e["text"]
        r=c.responses.create(model=os.getenv("OPENAI_MODEL","gpt-5.6-luna"),input=prompt)
        return (r.output_text or fallback_brief(e))[:600]
    except Exception:return fallback_brief(e)

async def build(lat,lon):
    weather,crypto,tg=await asyncio.gather(get_weather(lat,lon),get_crypto(),get_tg())
    events=tg+crypto_events(crypto); events.sort(key=lambda x:x["importance"],reverse=True)
    important=[x.copy() for x in events if x["importance"]>=IMPORTANT_THRESHOLD][:3]
    for e in important:
        e["brief"]=await asyncio.to_thread(ai_brief,e); e["insert_between_tracks"]=True
    return {"generated_at":now_iso(),"weather":weather,"crypto":crypto,"news":tg[:8],"events":events[:12],"important":important,"telegram_sources":TELEGRAM_SOURCES}

@app.get("/")
async def root():return {"service":"Volna Live Intelligence","health":"/api/live/health","snapshot":"/api/live/snapshot"}

@app.get("/api/live/health")
async def health():
    tg_ready=all(os.getenv(k,"").strip() for k in ("TELEGRAM_API_ID","TELEGRAM_API_HASH","TELEGRAM_SESSION"))
    return {"ok":True,"time":now_iso(),"service":"volna-live-intelligence","telegram_ready":tg_ready,"telegram_sources":[x["username"] for x in TELEGRAM_SOURCES],"weather_primary_ready":bool(os.getenv("YANDEX_WEATHER_KEY","").strip()),"weather_fallback":"open-meteo","coingecko_ready":True,"ai_editor_ready":bool(os.getenv("OPENAI_API_KEY","").strip())}

@app.get("/api/live/sources")
async def sources():
    return {"telegram":TELEGRAM_SOURCES,"weather":[{"id":"yandex_weather","role":"primary","configured":bool(os.getenv("YANDEX_WEATHER_KEY","").strip()),"trust":.96},{"id":"open_meteo_ecmwf","role":"fallback","configured":True,"trust":.91}],"crypto":[{"id":"coingecko","role":"primary","configured":True,"trust":.97}],"important_threshold":IMPORTANT_THRESHOLD}

@app.get("/api/live/snapshot")
async def snapshot(lat:Optional[float]=Query(None),lon:Optional[float]=Query(None)):
    global _cache
    lat=DEFAULT_LAT if lat is None else lat; lon=DEFAULT_LON if lon is None else lon; key=(round(lat,3),round(lon,3)); now=time.time()
    if _cache["value"] is not None and _cache["key"]==key and now-_cache["ts"]<CACHE_SECONDS:return _cache["value"]
    value=await build(lat,lon); _cache={"ts":now,"key":key,"value":value}; return value
