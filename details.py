"""Bangumi cache with startup/daily refresh and five-hour detail refresh."""
import io
import json
import os
import time
import threading
from datetime import datetime,timezone,timedelta
import urllib.parse
from pathlib import Path
from PIL import Image, ImageOps
from core import fetch

class DetailCache:
    TTL=5*60*60
    POLICY='1.7'

    def __init__(self,root,fetcher=None):
        self.root=Path(root)/'bangumi-cache'
        self.fetcher=fetcher or fetch
        self._locks={}
        self._guard=threading.Lock()

    @staticmethod
    def day(timestamp=None):
        return datetime.fromtimestamp(time.time() if timestamp is None else timestamp,timezone(timedelta(hours=8))).date().isoformat()

    def needs_refresh(self,subject,reason='detail'):
        previous=self.peek(subject)
        if not previous or previous.get('cache_policy')!=self.POLICY: return True
        if self.day(previous['cached_at'])!=self.day(): return True
        return reason!='daily' and time.time()-previous['cached_at']>=self.TTL

    def paths(self,subject):
        subject=str(subject)
        if not subject.isdigit() or not 0<int(subject)<10**10: raise ValueError('无效的Bangumi条目ID')
        return self.root/(subject+'.json'),self.root/(subject+'.png')

    def peek(self,subject):
        path,cover=self.paths(subject)
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if str(data['id'])!=str(subject) or not isinstance(data.get('cached_at'),(float,int)): return None
            data['cover_path']=str(cover) if cover.is_file() else ''
            return data
        except (OSError,ValueError,KeyError,TypeError): return None

    def get(self,subject,force=False,reason='detail'):
        self.paths(subject)
        with self._guard:
            lock=self._locks.setdefault(str(subject),threading.Lock())
        with lock: return self._get(subject,force,reason)

    def _get(self,subject,force,reason):
        path,cover=self.paths(subject)
        previous=self.peek(subject)
        if previous and not force and not self.needs_refresh(subject,reason):
            return dict(previous,cache_hit=True)
        raw=json.loads(self.fetcher('https://api.bgm.tv/v0/subjects/'+str(subject),timeout=15))
        if str(raw.get('id'))!=str(subject) or raw.get('type')!=2: raise ValueError('返回的条目不是所选动画')
        rating=raw.get('rating') or {}
        data=dict(id=raw['id'],name=raw.get('name',''),name_cn=raw.get('name_cn',''),
                  summary=raw.get('summary') or '该条目暂无简介。',date=raw.get('date') or '',
                  eps=raw.get('eps') or raw.get('total_episodes') or 0,platform=raw.get('platform') or '',
                  score=rating.get('score',0),rank=rating.get('rank',0),votes=rating.get('total',0),
                  distribution=rating.get('count') or {},collection=raw.get('collection') or {},
                  tags=[x['name'] for x in raw.get('tags',[])[:6] if x.get('name')],cached_at=time.time(),cache_policy=self.POLICY,
                  cover_url=previous.get('cover_url','') if previous else '')
        self.root.mkdir(parents=True,exist_ok=True)
        url=(raw.get('images') or {}).get('medium') or (raw.get('images') or {}).get('common') or ''
        if url:
            parsed=urllib.parse.urlparse(url)
            if parsed.scheme=='https' and parsed.hostname in ('lain.bgm.tv','lain.bgm.tv.cn') and (force or not cover.is_file() or url!=data['cover_url']):
                try:
                    image_data=self.fetcher(url,timeout=15)
                    if len(image_data)>4*1024*1024: raise ValueError('封面过大')
                    with Image.open(io.BytesIO(image_data)) as original:
                        if original.width*original.height>16_000_000: raise ValueError('封面尺寸过大')
                        resized=ImageOps.contain(ImageOps.exif_transpose(original).convert('RGB'),(280,400))
                        temp=cover.with_suffix('.png.tmp'); resized.save(temp,format='PNG'); os.replace(temp,cover)
                        data['cover_url']=url
                except Exception as e: data['cover_error']='封面暂不可用：'+str(e)
        tmp=path.with_suffix('.json.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); os.replace(tmp,path)
        data['cover_path']=str(cover) if cover.is_file() else ''
        return dict(data,cache_hit=False)
