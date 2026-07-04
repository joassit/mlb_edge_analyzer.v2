
"""weather_manager.py
Optimized Open-Meteo manager for MLB.
One fetch per execution (batch API), SQLite cache, stadium reuse.
"""
from __future__ import annotations
import json, sqlite3, requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

FORECAST="https://api.open-meteo.com/v1/forecast"
sess=requests.Session()
sess.mount("https://",HTTPAdapter(max_retries=Retry(total=3,backoff_factor=1,status_forcelist=[429,500,502,503,504])))

db=sqlite3.connect("weather_cache.sqlite")
db.execute("""CREATE TABLE IF NOT EXISTS weather_cache(
k TEXT PRIMARY KEY,v TEXT,expires TEXT)""")

def _get(k):
    r=db.execute("SELECT v,expires FROM weather_cache WHERE k=?",(k,)).fetchone()
    if not r:return None
    if datetime.utcnow()>datetime.fromisoformat(r[1]): return None
    return json.loads(r[0])

def _put(k,v,hours=1):
    exp=(datetime.utcnow()+timedelta(hours=hours)).isoformat()
    db.execute("INSERT OR REPLACE INTO weather_cache VALUES(?,?,?)",(k,json.dumps(v),exp));db.commit()

class WeatherManager:
    def __init__(self,stadiums):
        self.stadiums=stadiums
    def _fetch(self,item):
        sid,lat,lon,date=item
        key=f"{sid}_{date}"
        c=_get(key)
        if c:return sid,c
        p={"latitude":lat,"longitude":lon,"hourly":"temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m,surface_pressure,dew_point_2m","timezone":"UTC","start_date":date,"end_date":date}
        d=sess.get(FORECAST,params=p,timeout=(5,30)).json()["hourly"]
        _put(key,d,1)
        return sid,d
    def preload(self,games):
        uniq={}
        for g in games:
            s=self.stadiums[g["stadium_id"]]
            uniq[g["stadium_id"]]=(g["stadium_id"],s["lat"],s["lon"],g["game_time"][:10])
        with ThreadPoolExecutor(max_workers=5) as ex:
            self.data=dict(ex.map(self._fetch,uniq.values()))
        return self.data
