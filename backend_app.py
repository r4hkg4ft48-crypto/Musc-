import os, re, time, json, hashlib, asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = FastAPI(title='Volna Live Intelligence', version='0.7.0')
origins=[x.strip() for x in os.getenv('CORS_ORIGINS','*').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins or ['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])

DEFAULT_LAT=float(os.getenv('DEFAULT_LAT','55.7558'))
DEFAULT_LON=float(os.getenv('DEFAULT_LON','37.6173'))
CACHE_SECONDS=int(os.getenv('LIVE_CACHE_SECONDS','90'))
IMPORTANT_THRESHOLD=float(os.getenv('IMPORTANT_THRESHOLD','0.78'))

TELEGRAM_SOURCES=[
 {'username':'@news_18age','kind':'general','trust':float(os.getenv('TG_TRUST_NEWS18','0.62'))},
 {'username':'@crypto_news_and','kind':'crypto','trust':float(os.getenv('TG_TRUST_CRYPTO','0.60'))},
 {'username':'@ALL_NEWS_MOEX','kind':'moex','trust':float(os.getenv('TG_TRUST_MOEX','0.64'))},
]
_cache={'ts':0.0,'key':None,'value':None}
_profiles={}

URGENT=re.compile(r'(?i)срочно|молни[яи]|экстренн|чрезвычайн|атак|взрыв|землетряс|цунами|эвакуац|обстрел|авари|катастроф|закрыт[ьи]|запрет|ставк[аи]|санкци|банкрот|дефолт')
MARKET=re.compile(r'(?i)bitcoin|btc|ethereum|eth|крипт|биткоин|эфир|ставк[аи]|фрс|цб|рынок|бирж')

def now_iso(): return datetime.now(timezone.utc).isoformat()
def clamp(x,a=0.0,b=1.0): return max(a,min(b,x))
def eid(source,text): return hashlib.sha256((source+'|'+(text or '').strip()).encode()).hexdigest()[:18]
def excerpt(text,n=900): return re.sub(r'\s+',' ',text or '').strip()[:n]

def score_news(text,age,trust,kind):
    s=.20+trust*.38
    if URGENT.search(text): s+=.27
    if MARKET.search(text): s+=.10
    if kind=='crypto' and MARKET.search(text): s+=.08
    if kind=='moex' and re.search(r'(?i)мосбирж|moex|акци|облигац|дивиденд|эмитент|рубл|цб|индекс',text): s+=.09
    if age<15:s+=.08
    elif age<60:s+=.04
    elif age>360:s-=.12
    return clamp(s)

async def get_weather(lat,lon):
    ykey=os.getenv('YANDEX_WEATHER_KEY','').strip()
    async with httpx.AsyncClient(timeout=12) as c:
        if ykey:
            q='''query Weather($lat: Float!, $lon: Float!) { weatherByPoint(request: {lat: $lat, lon: $lon}) { now { temperature feelsLike humidity windSpeed windGust condition } } }'''
            try:
                r=await c.post('https://api.weather.yandex.ru/graphql/query',headers={'X-Yandex-Weather-Key':ykey,'Content-Type':'application/json'},json={'query':q,'variables':{'lat':lat,'lon':lon}})
                r.raise_for_status(); n=r.json()['data']['weatherByPoint']['now']
                return {'source':'yandex_weather','trust':.96,'temperature':n.get('temperature'),'feels_like':n.get('feelsLike'),'humidity':n.get('humidity'),'wind_speed':n.get('windSpeed'),'wind_gust':n.get('windGust'),'condition':n.get('condition'),'observed_at':now_iso()}
            except Exception:
                pass
        p={'latitude':lat,'longitude':lon,'current':'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,wind_gusts_10m,precipitation','forecast_days':1,'timezone':'auto'}
        r=await c.get('https://api.open-meteo.com/v1/forecast',params=p); r.raise_for_status(); n=r.json().get('current',{})
        return {'source':'open_meteo_ecmwf','trust':.91,'temperature':n.get('temperature_2m'),'feels_like':n.get('apparent_temperature'),'humidity':n.get('relative_humidity_2m'),'wind_speed':n.get('wind_speed_10m'),'wind_gust':n.get('wind_gusts_10m'),'condition':f"wmo:{n.get('weather_code')}",'precipitation':n.get('precipitation'),'observed_at':n.get('time') or now_iso()}

async def get_crypto():
    h={'accept':'application/json'}
    k=os.getenv('COINGECKO_API_KEY','').strip()
    if k:h['x-cg-demo-api-key']=k
    p={'ids':'bitcoin,ethereum,solana,the-open-network','vs_currencies':'usd','include_24hr_change':'true','include_last_updated_at':'true'}
    async with httpx.AsyncClient(timeout=12) as c:
        r=await c.get('https://api.coingecko.com/api/v3/simple/price',params=p,headers=h); r.raise_for_status(); data=r.json()
    labels={'bitcoin':'BTC','ethereum':'ETH','solana':'SOL','the-open-network':'TON'}
    return {'source':'coingecko','trust':.97,'assets':[{'id':cid,'symbol':labels.get(cid,cid.upper()),'usd':v.get('usd'),'change_24h':float(v.get('usd_24h_change') or 0),'updated_at':v.get('last_updated_at')} for cid,v in data.items()],'observed_at':now_iso()}

async def one_tg_public(cfg,limit=12):
    username=cfg['username'].lstrip('@')
    url=f'https://t.me/s/{username}'
    headers={'User-Agent':'Mozilla/5.0 (compatible; VolnaLive/1.0; +https://volna-live-intelligence.onrender.com)'}
    try:
        async with httpx.AsyncClient(timeout=15,follow_redirects=True,headers=headers) as client:
            r=await client.get(url); r.raise_for_status()
    except Exception:
        return []
    soup=BeautifulSoup(r.text,'html.parser')
    wraps=soup.select('.tgme_widget_message_wrap')
    now=datetime.now(timezone.utc); out=[]; source='telegram_'+username.lower()
    for wrap in wraps[-limit:]:
        text_el=wrap.select_one('.tgme_widget_message_text')
        date_el=wrap.select_one('a.tgme_widget_message_date')
        time_el=wrap.select_one('time')
        text=excerpt(text_el.get_text(' ',strip=True) if text_el else '')
        if not text: continue
        href=date_el.get('href') if date_el else None
        dt_raw=time_el.get('datetime') if time_el else None
        try:
            dt=datetime.fromisoformat(dt_raw.replace('Z','+00:00')) if dt_raw else now
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        except Exception: dt=now
        age=max(0,(now-dt).total_seconds()/60)
        out.append({'id':eid(source,text),'source':source,'source_name':cfg['username'],'source_kind':cfg['kind'],'trust':cfg['trust'],'confidence':cfg['trust'],'importance':round(score_news(text,age,cfg['trust'],cfg['kind']),3),'published_at':dt.isoformat(),'text':text,'url':href or f'https://t.me/{username}','access_mode':'public_web'})
    return out

def tokens(text):
    stop={'который','которые','этого','после','будет','также','сегодня','заявил','сообщил'}
    return {w for w in re.findall(r'[a-zа-яё0-9]{4,}',(text or '').lower()) if w not in stop}

def confirm(items):
    for i,a in enumerate(items):
        ta=tokens(a['text']); by=[]
        if len(ta)<3: continue
        for j,b in enumerate(items):
            if i==j or a['source']==b['source']: continue
            tb=tokens(b['text'])
            if len(tb)>=3 and len(ta&tb)/max(1,min(len(ta),len(tb)))>=.42: by.append(b['source_name'])
        if by:
            a['confirmed_by']=sorted(set(by))[:3]
            a['confidence']=round(clamp(a['trust']+min(.12,.05*len(a['confirmed_by']))),3)
            a['importance']=round(clamp(a['importance']+min(.10,.04*len(a['confirmed_by']))),3)
    return items

async def get_tg():
    chunks=await asyncio.gather(*[one_tg_public(x) for x in TELEGRAM_SOURCES])
    items=confirm([x for ch in chunks for x in ch]); items.sort(key=lambda x:(x['published_at'],x['importance']),reverse=True)
    return items

def crypto_events(c):
    out=[]
    for a in c.get('assets',[]):
        ch=a.get('change_24h',0); mag=abs(ch)
        if mag<4: continue
        text=f"{a['symbol']} {'вырос' if ch>0 else 'снизился'} на {mag:.1f}% за 24 часа. Сейчас около ${a['usd']}."
        out.append({'id':eid('coingecko',text),'source':'coingecko','source_name':'CoinGecko','trust':.97,'confidence':.97,'importance':round(clamp(.45+min(mag,20)/40+.97*.15),3),'published_at':now_iso(),'text':text,'url':'https://www.coingecko.com/'})
    return out

def fallback_brief(e):
    return f"Короткое обновление. {e['text'][:260]} Источник: {e.get('source_name','источник')}."

def ai_brief(e):
    k=os.getenv('OPENAI_API_KEY','').strip()
    if not(k and OpenAI): return fallback_brief(e)
    try:
        c=OpenAI(api_key=k)
        prompt='Ты редактор музыкального радио. Используй только данный факт. Напиши одну спокойную русскую эфирную вставку на 12–28 секунд, без домыслов и сенсационности. В конце кратко назови источник.\nИсточник: '+str(e.get('source_name'))+'\nФакт: '+e['text']
        r=c.responses.create(model=os.getenv('OPENAI_MODEL','gpt-5.6-luna'),input=prompt)
        return (r.output_text or fallback_brief(e))[:600]
    except Exception: return fallback_brief(e)

async def build(lat,lon):
    weather,crypto,tg=await asyncio.gather(get_weather(lat,lon),get_crypto(),get_tg())
    events=tg+crypto_events(crypto); events.sort(key=lambda x:x['importance'],reverse=True)
    important=[x.copy() for x in events if x['importance']>=IMPORTANT_THRESHOLD][:3]
    for e in important:
        e['brief']=await asyncio.to_thread(ai_brief,e); e['insert_between_tracks']=True
    return {'generated_at':now_iso(),'weather':weather,'crypto':crypto,'news':tg[:8],'events':events[:12],'important':important,'telegram_sources':TELEGRAM_SOURCES}

class AgentSignal(BaseModel):
    user_id: str='default'
    topic: str
    weight: float=1.0
class VoiceRequest(BaseModel):
    text: str

def profile_for(user_id): return _profiles.setdefault(user_id,{'topics':{},'updated_at':now_iso()})

def topic_hits(text):
    rules={'crypto':r'(?i)bitcoin|btc|ethereum|eth|solana|ton|крипт|биткоин|эфир','markets':r'(?i)мосбирж|moex|рынок|акци|облигац|дивиденд|индекс|цб|ставк','world':r'(?i)сша|европ|китай|нато|оон|президент|правительств|международ','tech':r'(?i)ии|искусственн.*интеллект|openai|apple|google|microsoft|технолог','weather':r'(?i)погод|температур|дожд|снег|ветер|шторм|жар|мороз'}
    return [k for k,p in rules.items() if re.search(p,text or '')]

def rank_for_profile(events,user_id):
    prefs=profile_for(user_id)['topics']; out=[]
    for e in events:
        x=e.copy(); bonus=sum(max(0,float(prefs.get(t,0))) for t in topic_hits(x.get('text','')))*.035
        x['personal_score']=round(clamp(float(x.get('importance',0))+min(.18,bonus)),3); x['matched_topics']=topic_hits(x.get('text','')); out.append(x)
    out.sort(key=lambda x:(x['personal_score'],x.get('confidence',0)),reverse=True); return out

def fallback_digest(snapshot,user_id):
    ranked=rank_for_profile(snapshot.get('events',[]),user_id)[:5]; headlines=[e['text'][:180] for e in ranked[:3]]
    return {'title':'Короткая сводка','summary':' '.join(headlines)[:700],'bullets':[{'text':e['text'][:220],'source':e.get('source_name'),'url':e.get('url'),'confidence':e.get('confidence'),'importance':e.get('importance')} for e in ranked[:5]],'emerging_topics':[{'topic':t,'score':round(sum(1 for e in ranked if t in e.get('matched_topics',[]))/max(1,len(ranked)),2)} for t in ['crypto','markets','world','tech','weather'] if any(t in e.get('matched_topics',[]) for e in ranked)],'speech_text':('Главное к этому моменту. '+' '.join(headlines))[:1100]}

def ai_digest(snapshot,user_id):
    key=os.getenv('OPENAI_API_KEY','').strip()
    if not(key and OpenAI): return fallback_digest(snapshot,user_id)
    ranked=rank_for_profile(snapshot.get('events',[]),user_id)[:12]
    payload={'events':[{'text':e.get('text'),'source':e.get('source_name'),'confidence':e.get('confidence'),'importance':e.get('importance'),'matched_topics':e.get('matched_topics')} for e in ranked],'weather':snapshot.get('weather'),'crypto':snapshot.get('crypto'),'profile':profile_for(user_id)}
    prompt='''Ты редактор персонального музыкального эфира Volna. Используй ТОЛЬКО факты из JSON. Ничего не додумывай. Сделай JSON с полями: title, summary, bullets, emerging_topics, speech_text. bullets: максимум 5 объектов {text,source,confidence,importance}. emerging_topics: максимум 5 объектов {topic,score,why}. speech_text: естественная русская сводка примерно 25-45 секунд, спокойная, без кликбейта. Учитывай profile, но важные события не скрывай. Не повторяй дубли. JSON:\n'''+json.dumps(payload,ensure_ascii=False)
    try:
        client=OpenAI(api_key=key); r=client.responses.create(model=os.getenv('OPENAI_MODEL','gpt-5.6-luna'),input=prompt); txt=(r.output_text or '').strip(); m=re.search(r'\{.*\}',txt,re.S); return json.loads(m.group(0) if m else txt)
    except Exception: return fallback_digest(snapshot,user_id)

def infographic_bundle(snapshot,digest):
    crypto=[{'label':a['symbol'],'value':round(float(a.get('change_24h') or 0),2)} for a in snapshot.get('crypto',{}).get('assets',[])]
    trusts=[]; seen=set()
    for e in snapshot.get('events',[]):
        n=e.get('source_name')
        if n and n not in seen: seen.add(n); trusts.append({'label':n,'value':round(float(e.get('trust',0))*100,1)})
    topics=[{'label':x.get('topic','тема'),'value':round(float(x.get('score',0))*100,1)} for x in digest.get('emerging_topics',[]) if isinstance(x,dict)]
    return [{'id':'crypto_24h','type':'bar','title':'Крипта · 24 часа','unit':'%','data':crypto},{'id':'source_trust','type':'bar','title':'Доверие к источникам','unit':'%','data':trusts[:8]},{'id':'topics','type':'bar','title':'Темы сводки','unit':'%','data':topics[:6]}]

@app.post('/api/agent/signal')
async def agent_signal(sig:AgentSignal):
    p=profile_for(sig.user_id); cur=float(p['topics'].get(sig.topic,0)); p['topics'][sig.topic]=round(clamp(cur+sig.weight,-5,10),3); p['updated_at']=now_iso(); return {'ok':True,'profile':p}

@app.get('/api/agent/profile')
async def agent_profile(user_id:str='default'): return profile_for(user_id)

@app.get('/api/agent/briefing')
async def agent_briefing(user_id:str='default',lat:Optional[float]=Query(None),lon:Optional[float]=Query(None)):
    snap=await snapshot(lat,lon); digest=await asyncio.to_thread(ai_digest,snap,user_id)
    return {'generated_at':now_iso(),'agent':{'name':'Volna AI','role':'персональный редактор эфира','voice':'marin','voice_is_ai_generated':True},'profile':profile_for(user_id),'digest':digest,'infographics':infographic_bundle(snap,digest),'sources':[{'name':e.get('source_name'),'url':e.get('url'),'confidence':e.get('confidence')} for e in rank_for_profile(snap.get('events',[]),user_id)[:6]]}

@app.post('/api/agent/voice')
async def agent_voice(req:VoiceRequest):
    key=os.getenv('OPENAI_API_KEY','').strip()
    if not key: raise HTTPException(status_code=503,detail='OPENAI_API_KEY is not configured')
    text=(req.text or '').strip()
    if not text: raise HTTPException(status_code=400,detail='text is required')
    text=text[:1800]
    instructions=os.getenv('OPENAI_TTS_INSTRUCTIONS','Говори по-русски женским голосом в спокойной, собранной манере современного новостного и музыкального эфира. Высота голоса низко-средняя для женской речи, без писклявости и без излишней воздушности. Темп умеренный, примерно 0.92–0.96 от обычного разговорного темпа. Интонация ровная, уверенная и очень чистая; предложения заканчивай мягко, без рекламной улыбки и театральности. Делай осмысленные паузы примерно 0.5–0.9 секунды между смысловыми блоками и чуть более длинную паузу перед ключевой цифрой или выводом. Дикция четкая, согласные аккуратные, цифры, проценты, названия компаний и тикеры произноси особенно разборчиво. Эмоциональность сдержанная: тепло и живо, но без драматизации. Не ускоряйся на новостях. Погоду рассказывай чуть мягче, рынки и крипту — чуть строже. Сохраняй одинаковый характер голоса во всех выпусках.')
    try:
        client=OpenAI(api_key=key)
        speech=client.audio.speech.create(model=os.getenv('OPENAI_TTS_MODEL','gpt-4o-mini-tts'),voice=os.getenv('OPENAI_TTS_VOICE','marin'),input=text,instructions=instructions)
        return Response(content=speech.read(),media_type='audio/mpeg',headers={'Cache-Control':'private, max-age=3600','X-AI-Generated-Voice':'true'})
    except Exception:
        raise HTTPException(status_code=502,detail='TTS generation failed')

@app.get('/')
async def root(): return {'service':'Volna Live Intelligence','health':'/api/live/health','snapshot':'/api/live/snapshot','briefing':'/api/agent/briefing'}

@app.get('/api/live/health')
async def health():
    return {'ok':True,'time':now_iso(),'service':'volna-live-intelligence','telegram_ready':True,'telegram_access_mode':'public_web_no_login','telegram_sources':[x['username'] for x in TELEGRAM_SOURCES],'weather_primary_ready':bool(os.getenv('YANDEX_WEATHER_KEY','').strip()),'weather_fallback':'open-meteo','coingecko_ready':True,'ai_editor_ready':bool(os.getenv('OPENAI_API_KEY','').strip()),'agent_ready':bool(os.getenv('OPENAI_API_KEY','').strip()),'tts_model':os.getenv('OPENAI_TTS_MODEL','gpt-4o-mini-tts'),'tts_voice':os.getenv('OPENAI_TTS_VOICE','marin')}

@app.get('/api/live/sources')
async def sources():
    return {'telegram_access_mode':'public_web_no_login','telegram':TELEGRAM_SOURCES,'weather':[{'id':'yandex_weather','role':'primary','configured':bool(os.getenv('YANDEX_WEATHER_KEY','').strip()),'trust':.96},{'id':'open_meteo_ecmwf','role':'fallback','configured':True,'trust':.91}],'crypto':[{'id':'coingecko','role':'primary','configured':True,'trust':.97}],'important_threshold':IMPORTANT_THRESHOLD}

@app.get('/api/live/snapshot')
async def snapshot(lat:Optional[float]=Query(None),lon:Optional[float]=Query(None)):
    global _cache
    lat=DEFAULT_LAT if lat is None else lat; lon=DEFAULT_LON if lon is None else lon; key=(round(lat,3),round(lon,3)); now=time.time()
    if _cache['value'] is not None and _cache['key']==key and now-_cache['ts']<CACHE_SECONDS: return _cache['value']
    value=await build(lat,lon); _cache={'ts':now,'key':key,'value':value}; return value
