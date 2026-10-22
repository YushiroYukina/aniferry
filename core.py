import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import unicodedata
import base64
import ssl
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from functools import lru_cache
from html.parser import HTMLParser
from contextlib import closing
from pathlib import Path
from branding import USER_AGENT

DEFAULT_OUTPUT = r'E:\动画\202610\追番'
UA = USER_AGENT

@lru_cache(maxsize=2048)
def title_key(text):
    text=unicodedata.normalize('NFKC',text or '').casefold()
    if os.name=='nt':
        import ctypes
        convert=ctypes.windll.kernel32.LCMapStringEx
        convert.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_longlong]
        convert.restype=ctypes.c_int
        size=convert('zh-CN',0x02000000,text,len(text),None,0,None,None,0)
        if size:
            result=ctypes.create_unicode_buffer(size+1)
            if convert('zh-CN',0x02000000,text,len(text),result,size,None,None,0): text=result.value
    return ''.join(c for c in text if c.isalnum()).replace('姊妹','姐妹')


def roman_tokens(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('ō','ou').replace('ū','uu')
    text=''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))
    return re.findall(r'[a-z0-9]+',text.replace("'",'').replace('’',''))


def cour_base(text):
    # Only split-cour/part labels: never strip Season 2 / 第2季 or arbitrary sequel numbers.
    return re.sub(r'\s*(?:第\s*[0-9一二三四五六七八九十]+\s*(?:部分|クール)|(?:part|cour)\s*\d+|\d+(?:st|nd|rd|th)\s+cour)\s*$', '',
                  unicodedata.normalize('NFKC',text),flags=re.I).strip()


def cour_index(text):
    match=re.search(r'第\s*([0-9一二三四五六七八九十]+)\s*(?:部分|クール)|\b(?:part|cour)\s*(\d+)|\b(\d+)(?:st|nd|rd|th)\s+cour',
                    unicodedata.normalize('NFKC',text),re.I)
    if not match: return None
    number=next(g for g in match.groups() if g)
    return int(number) if number.isdigit() else dict(zip('一二三四五六七八九十',range(1,11))).get(number)

def title_season(text):
    text=unicodedata.normalize('NFKC',text).casefold()
    compact=re.search(r'\bs(\d{1,2})e\d',text)
    if compact: return int(compact.group(1))
    m=re.search(r'(?:season\s*|\bs\s*|第)(\d{1,2})(?:\b|季|期)|\b(\d{1,2})(?:st|nd|rd|th)\s+season',text)
    if m: return int(m.group(1) or m.group(2))
    for number,ch in enumerate('一二三四五六七八九十',1):
        if '第'+ch+'季' in text or '第'+ch+'期' in text: return number
    return None


def continuation_eligible(name, aliases, options=None):
    options=options or {}
    if 'continuation' in options: return bool(options['continuation'])
    names=[name]+[v for k,v in aliases.items() if not k.startswith('_') and isinstance(v,str)]
    return any(cour_index(v) is not None or (title_season(v) or 1)>1 for v in names)

def matches_show(item,aliases):
    if re.search(r'OPテーマ|EDテーマ|\bOST\b|soundtrack|主題歌|主题曲|原声集|原聲集',item.get('title',''),re.I): return False
    special=r'特別篇|特别篇|特別編|特别编|総集編|总集篇|\b(?:SP|OVA|OAD|NCOP|NCED)\b'
    if re.search(special,item.get('title',''),re.I) and not any(
            re.search(special,v,re.I) for k,v in aliases.items() if not k.startswith('_') and isinstance(v,str)):
        return False
    expected=str(aliases.get('_bgm_id',''))
    found=str(item.get('subject_id',''))
    if found:
        if expected: return expected==found
    title=item.get('title','')
    names=[v for k,v in aliases.items() if not k.startswith('_') and isinstance(v,str) and v.strip()]
    parts={cour_index(n) for n in names}-{None}
    actual_part=cour_index(title)
    if actual_part and (parts and actual_part not in parts or not parts and actual_part>1): return False
    seasons={title_season(n) for n in names}-{None}
    actual=title_season(title)
    if actual and (seasons and actual not in seasons or not seasons and actual>1): return False
    key=title_key(title)
    for name in names:
        if len(title_key(name))<2: continue
        if re.search(r'[^\x00-\x7f]',name):
            if title_key(name) in key: return True
        else:
            needle=''.join(roman_tokens(name)); hay=roman_tokens(title)
            # Permit LuluttoLilly / Lulutto Lilly but retain whole-token boundaries.
            for start in range(len(hay)):
                candidate=''
                for token in hay[start:]:
                    candidate+=token
                    if candidate==needle: return True
                    if len(candidate)>=len(needle): break
    return False

def parse_external_rss(raw,source):
    items=[]
    for node in ET.fromstring(raw).findall('./channel/item'):
        title=node.findtext('title',''); page=node.findtext('link','')
        enclosure=node.find('enclosure')
        if enclosure is None: continue
        url=enclosure.get('url','')
        try:
            if url.startswith('magnet:'): hash_=magnet_hash(url)
            else:
                match=re.search(r'/([a-fA-F0-9]{40})\.torrent(?:\?|$)',url)
                if not match: continue
                hash_=match.group(1).lower()
        except ValueError: continue
        if page.startswith('http://share.dmhy.org/'): page=page.replace('http://','https://',1)
        download=url if allowed_torrent_url(url,hash_) else 'https://mikanani.me/Home/Episode/'+hash_
        try: size=f"{int(enclosure.get('length','0'))/1024/1024:.1f} MiB"
        except ValueError: size='未知'
        if size=='0.0 MiB': size='未知'
        items.append(dict(hash=hash_,title=title,page=page,url=download,source=source,seeds=0,seed_known=False,
                          size=size,date=node.findtext('pubDate',''),episode=episode(title),ani=is_ani(title),subtitle=subtitle(title)))
    return items

def fetch(url, payload=None, timeout=25):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = response.read(8 * 1024 * 1024 + 1)
    if len(result) > 8 * 1024 * 1024:
        raise ValueError('响应过大')
    return result

def subtitle(title):
    if re.search(r'无字幕|無字幕|无中文|無中文|\braw\b|English[ -]?only', title, re.I):
        return '无中文'
    if re.search(r'简体|繁体|簡體|繁體|简繁|簡繁|简日|繁日|簡日|简中|繁中|中字|中文字幕|CHS|CHT|Chinese|\b(?:SC|TC)\b', title, re.I):
        return '标题声明中文'
    return '未知'

def episode(title):
    # Strip resolution/CRC and group tags before identifying episode numbers.
    if re.search(r'(?:\[|\s-\s)(\d{1,3})\s*[-~～]\s*\d{1,3}',title):
        return '待核对:' + title
    season_ep=re.search(r'\bS\d{1,2}E(\d{1,3})(?:v\d+)?\b',title,re.I)
    if season_ep: return str(int(season_ep.group(1)))
    s = re.sub(r'\[[^\]]*\]', ' ', title)
    m = re.search(r'(?:\s-\s|\bEP?\s*|第)(\d{1,3})(?:v\d+)?(?:\b|话|話|集)', s, re.I)
    if not m:
        m = re.search(r'\[(\d{1,3})(?:v\d+)?\]', title)
    return str(int(m.group(1))) if m else '待核对:' + title

def parse_rss(raw):
    result = []
    for item in ET.fromstring(raw).findall('./channel/item'):
        fields = {child.tag.split('}')[-1]: child.text or '' for child in item}
        title = fields.get('title', '')
        hash_ = fields.get('infoHash', '').lower()
        if not re.fullmatch(r'[a-f0-9]{40}', hash_):
            continue
        result.append(dict(hash=hash_, title=title, url=fields.get('link', ''),
                           page=fields.get('guid', ''), seeds=int(fields.get('seeders', '0')),
                           size=fields.get('size', ''), date=fields.get('pubDate', ''),
                           episode=episode(title), subtitle=subtitle(title),
                           ani=is_ani(title),source='Nyaa',seed_known=True))
    return result

def is_ani(title, fansub=''):
    return fansub.strip().casefold()=='ani' or bool(re.search(r'[\[【]\s*ANI\s*[\]】]',title,re.I))

def magnet_hash(magnet):
    for xt in urllib.parse.parse_qs(urllib.parse.urlparse(magnet).query).get('xt',[]):
        if not xt.lower().startswith('urn:btih:'): continue
        value=xt[9:]
        if re.fullmatch('[a-fA-F0-9]{40}',value): return value.lower()
        if re.fullmatch('[A-Za-z2-7]{32}',value): return base64.b32decode(value.upper()).hex()
    raise ValueError('资源没有合法的 BT v1 infohash')

def parse_garden(raw, subject_id):
    response=json.loads(raw)
    if response.get('status')!='OK': raise ValueError('聚合源返回非成功状态')
    result=[]
    for entry in response.get('resources',[]):
        if str(entry.get('subjectId'))!=str(subject_id) or entry.get('type')!='动画': continue
        try: hash_=magnet_hash(entry.get('magnet',''))
        except ValueError: continue
        title=entry['title']
        fansub=(entry.get('fansub') or {}).get('name','')
        result.append(dict(hash=hash_,title=title,episode=episode(title),ani=is_ani(title,fansub),
                           subtitle=subtitle(title),seeds=0,seed_known=False,source='AnimeGarden',
                           url='https://mikanani.me/Home/Episode/'+hash_,
                           page=f"https://animes.garden/detail/{entry['provider']}/{entry['providerId']}",
                           size=f"{int(entry.get('size',0))/1024/1024:.1f} MiB",date=entry.get('createdAt',''),
                           subject_id=str(subject_id),fansub=fansub))
    return result

def merge_release(merged,item):
    previous=merged.get(item['hash'])
    if not previous: merged[item['hash']]=item; return
    priority={'Nyaa':0,'Mikan':1,'DMHY':2,'AnimeGarden':3}
    chosen=dict(item if priority.get(item.get('source'),9)<priority.get(previous.get('source'),9) else previous)
    identity=previous.get('subject_id') or item.get('subject_id')
    if identity: chosen['subject_id']=identity
    chosen['ani']=previous['ani'] or item['ani']
    chosen['sources']=sorted(set(previous.get('sources',[previous.get('source','Nyaa')])+[item.get('source','Nyaa')]))
    merged[item['hash']]=chosen

@lru_cache(maxsize=128)
def resolve_subject(chinese,japanese):
    entries=lookup(chinese or japanese)
    native=[e for e in entries if japanese and normalized_title(e['日文名'])==normalized_title(japanese)]
    matches=native or [e for e in entries if chinese and normalized_title(e['中文名'])==normalized_title(chinese)]
    return str(matches[0]['id']) if len(matches)==1 else ''

def recommend(items, handled):
    groups = {}
    for item in items:
        if item['episode'] in handled or item['hash'] in handled:
            continue
        groups.setdefault(item['episode'], []).append(item)
    selected = []
    for group in groups.values():
        ranked = sorted(group, key=lambda x: (-x['ani'], -x['seeds'], x['hash']))
        selected.extend(ranked[:1] if ranked[0]['ani'] else ranked[:3])
    return selected

def discover_bitcomet():
    candidates = []
    try:
        import winreg
        for key in [r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\BitComet.exe',
                    r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\BitComet_x64.exe']:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    with winreg.OpenKey(hive, key) as k:
                        candidates.append(winreg.QueryValue(k, None))
                except OSError:
                    pass
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'.torrent') as k:
                progid = winreg.QueryValue(k, None)
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\shell\open\command') as k:
                command = winreg.QueryValue(k, None)
            m = re.match(r'"([^"]+)"|([^ ]+\.exe)', command, re.I)
            if m and 'bitcomet' in (m.group(1) or m.group(2)).lower():
                candidates.append(m.group(1) or m.group(2))
        except OSError:
            pass
    except ImportError:
        pass
    for base in [os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', ''), r'D:\BitComet']:
        for name in ['BitComet.exe', 'BitComet_x64.exe']:
            candidates.extend([str(Path(base) / 'BitComet' / name), str(Path(base) / name)])
    return next((p for p in candidates if Path(p).is_file()), '')

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'tracker.sqlite3')
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='episode_rules'").fetchone() and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='shows'").fetchone():
            from datetime import datetime
            backup=self.root/('tracker-before-v12-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
            with closing(sqlite3.connect(backup)) as destination: self.db.backup(destination)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS shows(id INTEGER PRIMARY KEY,name TEXT,aliases TEXT);
          CREATE TABLE IF NOT EXISTS releases(show_id INTEGER,hash TEXT,title TEXT,episode TEXT,payload TEXT,
            status TEXT DEFAULT '待确认',output TEXT DEFAULT '', PRIMARY KEY(show_id,hash));
          CREATE TABLE IF NOT EXISTS episode_rules(show_id INTEGER PRIMARY KEY,
            first INTEGER DEFAULT 1,last INTEGER DEFAULT 0,completed INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS deleted_shows(id INTEGER PRIMARY KEY,name TEXT,deleted_at REAL);
          CREATE TABLE IF NOT EXISTS show_options(show_id INTEGER PRIMARY KEY,options TEXT NOT NULL);
        ''')
        self.db.commit()

        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='handoffs'").fetchone():
            if self.db.execute('SELECT 1 FROM releases LIMIT 1').fetchone(): self.backup('before-v18-downloaders')
            self.db.execute('CREATE TABLE handoffs(show_id INTEGER,hash TEXT,backend TEXT,profile TEXT,remote_id TEXT DEFAULT \'\', PRIMARY KEY(show_id,hash))')
            self.db.commit()

        if self.setting('release_parser')!='1.3':
            rows=self.db.execute('SELECT show_id,hash,title,payload FROM releases').fetchall()
            if rows:
                from datetime import datetime
                backup=self.root/('tracker-before-v13-metadata-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
                with closing(sqlite3.connect(backup)) as destination: self.db.backup(destination)
            for sid,hash_,title,raw in rows:
                try: item=json.loads(raw)
                except (ValueError,TypeError): continue
                item.update(episode=episode(title),subtitle=subtitle(title),ani=bool(item.get('ani')) or is_ani(title,item.get('fansub','')))
                item.setdefault('source','Nyaa'); item.setdefault('seed_known',item['source']=='Nyaa')
                self.db.execute('UPDATE releases SET episode=?,payload=? WHERE show_id=? AND hash=?',(item['episode'],json.dumps(item,ensure_ascii=False),sid,hash_))
            self.set_setting('release_parser','1.3')
        if self.setting('identity_filter')!='1.5':
            if self.db.execute('SELECT 1 FROM releases LIMIT 1').fetchone(): self.backup('before-v15-identity')
            self.clean_mismatches()
            self.set_setting('identity_filter','1.5')

    def backup(self,label):
        from datetime import datetime
        target=self.root/('tracker-'+label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
        with closing(sqlite3.connect(target)) as destination: self.db.backup(destination)
        return target

    def clean_mismatches(self):
        count=0
        aliases={sid:self.search_aliases(sid) for sid,_,raw in self.shows()}
        for sid,hash_,raw in self.db.execute("SELECT show_id,hash,payload FROM releases WHERE status IN ('待确认','忽略')").fetchall():
            if sid in aliases and not matches_show(json.loads(raw),aliases[sid]):
                self.db.execute("UPDATE releases SET status='误匹配' WHERE show_id=? AND hash=?",(sid,hash_)); count+=1
        self.db.commit()
        return count

    def delete_show(self,sid):
        return self.delete_shows([sid])

    def delete_shows(self,ids):
        wanted=set(ids)
        rows=[(sid,name) for sid,name,_ in self.shows() if sid in wanted]
        if not rows: return 0
        self.backup('before-delete')
        with self.db:
            for sid,name in rows:
                self.db.execute('INSERT OR IGNORE INTO deleted_shows VALUES(?,?,?)',(sid,name,time.time()))
                self.db.execute('DELETE FROM releases WHERE show_id=?',(sid,))
                self.db.execute('DELETE FROM handoffs WHERE show_id=?',(sid,))
                self.db.execute('DELETE FROM episode_rules WHERE show_id=?',(sid,))
                self.db.execute('DELETE FROM show_options WHERE show_id=?',(sid,))
                self.db.execute('DELETE FROM shows WHERE id=?',(sid,))
        self.export_queue()
        report_path=self.root/'last-search.json'
        if report_path.exists():
            try:
                reports=json.loads(report_path.read_text(encoding='utf-8'))
                report_path.write_text(json.dumps([r for r in reports if r.get('show_id') not in wanted],ensure_ascii=False,indent=2),encoding='utf-8')
            except (OSError,ValueError,AttributeError): pass
        return len(rows)

    def ignore_releases(self,sid,hashes):
        count=0
        with self.db:
            for hash_ in set(hashes):
                count+=self.db.execute("UPDATE releases SET status='忽略' WHERE show_id=? AND hash=? AND status='待确认'",(sid,hash_)).rowcount
        return count

    def clear_unknown(self,sid):
        # Keep a hash tombstone so a future scan cannot re-add the same uncertain resource.
        with self.db:
            count=self.db.execute("UPDATE releases SET status='已清理' WHERE show_id=? AND episode LIKE '待核对%' AND status IN ('待确认','忽略')",(sid,)).rowcount
        return count

    def setting(self, key, default=''):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, value))
        self.db.commit()

    def shows(self):
        return self.db.execute('SELECT id,name,aliases FROM shows ORDER BY id').fetchall()

    def save_show(self, name, aliases, sid=None):
        if not name.strip():
            raise ValueError('请输入动画名称')
        if sid:
            self.db.execute('UPDATE shows SET name=?,aliases=? WHERE id=?', (name, json.dumps(aliases, ensure_ascii=False), sid))
        else:
            sid=self.db.execute('SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM shows UNION ALL SELECT id FROM deleted_shows)').fetchone()[0]
            self.db.execute('INSERT INTO shows(id,name,aliases) VALUES(?,?,?)', (sid,name, json.dumps(aliases, ensure_ascii=False)))
        self.db.commit()
        return sid

    def ingest(self, sid, items):
        row=self.db.execute('SELECT aliases FROM shows WHERE id=?',(sid,)).fetchone()
        if not row: return
        aliases=self.search_aliases(sid)
        first,last,_ = self.episode_rule(sid)
        for item in items:
            if not matches_show(item,aliases): continue
            ep = episode_number(item['episode'])
            if ep is not None and (ep < first or (last and ep > last)):
                continue
            self.db.execute("INSERT INTO releases(show_id,hash,title,episode,payload) VALUES(?,?,?,?,?) ON CONFLICT(show_id,hash) DO UPDATE SET payload=excluded.payload,title=excluded.title,episode=excluded.episode,status=CASE WHEN releases.status='误匹配' THEN '待确认' ELSE releases.status END",
                            (sid, item['hash'], item['title'], item['episode'], json.dumps(item, ensure_ascii=False)))
        self.db.commit()

    def show_options(self,sid):
        row=self.db.execute('SELECT options FROM show_options WHERE show_id=?',(sid,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_show_options(self,sid,options):
        row=self.db.execute('SELECT name,aliases FROM shows WHERE id=?',(sid,)).fetchone()
        if not row: raise ValueError('动画已删除')
        offset=options.get('offset',0)
        if type(offset) is not int or not 0<=offset<=998: raise ValueError('前序集数须为0～998')
        if options.get('numbering','absolute') not in ('local','absolute') or options.get('range_basis','local') not in ('local','absolute'):
            raise ValueError('编号方式不正确')
        if options.get('dual') and not continuation_eligible(row[0],json.loads(row[1]),options):
            raise ValueError('双集数仅用于续作或分割放送')
        if not isinstance(options.get('extra_aliases',[]),list) or len(options.get('extra_aliases',[]))>8:
            raise ValueError('补充名称最多8个')
        for key in ('extra_aliases','exclude_words'):
            values=options.get(key,[])
            if not isinstance(values,list) or len(values)>20 or any(not isinstance(v,str) or not 1<=len(v)<=160 for v in values):
                raise ValueError('补充名称或排除词格式不正确')
        self.db.execute('INSERT OR REPLACE INTO show_options VALUES(?,?)',(sid,json.dumps(options,ensure_ascii=False)))
        self.db.commit()

    def search_aliases(self,sid):
        row=self.db.execute('SELECT aliases FROM shows WHERE id=?',(sid,)).fetchone()
        aliases=json.loads(row[0]) if row else {}
        for i,value in enumerate(self.show_options(sid).get('extra_aliases',[])):
            aliases[f'补充名称{i}']=value
        return aliases

    def candidates(self, sid, expanded=False):
        rows = self.db.execute('SELECT payload,status,episode,hash FROM releases WHERE show_id=?', (sid,)).fetchall()
        handled = set()
        for _, status, ep, hash_ in rows:
            if status in ('待接收','已转交','已完成'):
                handled.update([ep, hash_])
        first,last,_ = self.episode_rule(sid)
        items = [json.loads(r[0]) for r in rows if r[1] == '待确认']
        row=self.db.execute('SELECT aliases FROM shows WHERE id=?',(sid,)).fetchone()
        if not row: return []
        aliases=self.search_aliases(sid)
        excluded=self.show_options(sid).get('exclude_words',[])
        items=[x for x in items if matches_show(x,aliases) and not any(w.casefold() in x['title'].casefold() for w in excluded)]
        items = [x for x in items if episode_number(x['episode']) is None or
                 first <= int(x['episode']) and (not last or int(x['episode']) <= last)]
        eligible=[x for x in items if x['episode'] not in handled and x['hash'] not in handled]
        return sorted(eligible if expanded else recommend(items, handled),key=lambda x:(episode_number(x['episode']) is None,episode_number(x['episode']) or 0,-x['ani'],-x['seeds']))

    def episode_rule(self, sid):
        row=self.db.execute('SELECT first,last,completed FROM episode_rules WHERE show_id=?',(sid,)).fetchone()
        first,last,completed=row or (1,0,0)
        return max(first,completed-1,1),last,completed

    def set_episode_range(self, sid, first, last=0):
        if not 1 <= first <= 999 or not 0 <= last <= 999 or last and last < first:
            raise ValueError('起始集数须为1～999；结束集数留空或不小于起始集数')
        self.db.execute('INSERT INTO episode_rules(show_id,first,last) VALUES(?,?,?) ON CONFLICT(show_id) DO UPDATE SET first=excluded.first,last=excluded.last',(sid,first,last))
        self.db.commit()

    def mark_completed(self, sid, hash_):
        row=self.db.execute('SELECT episode,status FROM releases WHERE show_id=? AND hash=?',(sid,hash_)).fetchone()
        if not row or row[1] not in ('已转交','已完成'):
            return False
        self.db.execute("UPDATE releases SET status='已完成' WHERE show_id=? AND hash=?",(sid,hash_))
        ep=episode_number(row[0])
        if ep is not None:
            self.db.execute('INSERT INTO episode_rules(show_id,completed) VALUES(?,?) ON CONFLICT(show_id) DO UPDATE SET completed=MAX(completed,excluded.completed)',(sid,ep))
            # Only pending/ignored sniffed candidates are discarded; keep download history.
            self.db.execute("UPDATE releases SET status='弃置' WHERE show_id=? AND status IN ('待确认','忽略') AND episode NOT GLOB '*[^0-9]*' AND CAST(episode AS INTEGER) < ?",(sid,max(1,ep-1)))
        self.db.commit()
        return True

    def sync_completed(self, hashes):
        changed=0
        for sid,hash_ in self.db.execute("SELECT show_id,hash FROM releases WHERE status='已转交'").fetchall():
            if self.handoff_backend(sid,hash_)!='bitcomet': continue
            if hash_ in hashes:
                changed += self.mark_completed(sid,hash_)
        if changed: self.export_queue()
        return changed

    def sync_received(self, records):
        count=0
        for sid,hash_,output in self.db.execute("SELECT show_id,hash,output FROM releases WHERE status='待接收'").fetchall():
            if self.handoff_backend(sid,hash_)!='bitcomet': continue
            task=records.get(hash_)
            if task and same_directory(task.get('SaveDirectory',''),output):
                self.db.execute("UPDATE releases SET status='已转交' WHERE show_id=? AND hash=?",(sid,hash_)); count+=1
        self.db.commit()
        if count: self.export_queue()
        return count

    def handoff_backend(self,sid,hash_):
        row=self.db.execute('SELECT backend FROM handoffs WHERE show_id=? AND hash=?',(sid,hash_)).fetchone()
        return row[0] if row else 'bitcomet'

    def downloader_profile(self,backend=None):
        from downloaders import decode_profile,DEFAULTS,discover_thunder,thunder_db
        backend=backend or self.setting('downloader','bitcomet')
        raw=self.setting('downloader_profile_'+backend)
        if raw: return decode_profile(raw)
        if backend=='bitcomet':
            exe=self.setting('bitcomet',discover_bitcomet())
            return {'exe':exe,'task_xml':self.setting('task_xml') or discover_task_file(exe)}
        if backend=='thunder':
            exe=discover_thunder()
            return {'exe':exe,'task_db':thunder_db(exe)}
        return {'url':DEFAULTS.get(backend,''),'username':'','password':'','secret':''}

    def poll_downloaders(self):
        """Group by saved connection so changing the default cannot reassign old tasks."""
        from downloaders import client,decode_profile,LABELS
        groups={}; errors=[]; accepted=completed=0
        rows=self.db.execute("SELECT r.show_id,r.hash,r.output,h.backend,h.profile,h.remote_id FROM releases r LEFT JOIN handoffs h ON h.show_id=r.show_id AND h.hash=r.hash WHERE r.status IN ('待接收','已转交')").fetchall()
        for sid,hash_,output,backend,raw,remote_id in rows:
            backend=backend or 'bitcomet'
            try:
                profile=decode_profile(raw) if raw else self.downloader_profile(backend)
                key=(backend,json.dumps(profile,sort_keys=True))
                groups.setdefault(key,[]).append((sid,hash_,output,remote_id))
            except ValueError as error: errors.append(LABELS.get(backend,backend)+'：'+str(error))
        for (backend,raw),tasks in groups.items():
            try:
                profile=json.loads(raw)
                if backend=='bitcomet':
                    path=profile.get('task_xml') or discover_task_file(profile.get('exe',''))
                    if not path: raise ValueError('请在设置中指定任务文件')
                    records=task_records(path); finished=completed_hashes(path)
                    observations={h:{'directory':r.get('SaveDirectory',''),'complete':h in finished} for h,r in records.items()}
                else:
                    observations=client(backend,profile).snapshot({t[1] for t in tasks},{t[1]:t[3] for t in tasks})
                for sid,hash_,output,_ in tasks:
                    task=observations.get(hash_)
                    if not task: continue
                    if not same_directory(task.get('directory',''),output):
                        errors.append(LABELS[backend]+'：任务目录与设置不符，请在下载器核对保存位置'); continue
                    changed=self.db.execute("UPDATE releases SET status='已转交' WHERE show_id=? AND hash=? AND status='待接收'",(sid,hash_)).rowcount
                    accepted+=changed
                    if task.get('id'):
                        self.db.execute('UPDATE handoffs SET remote_id=? WHERE show_id=? AND hash=?',(task['id'],sid,hash_))
                    self.db.commit()
                    if task.get('complete'): completed+=self.mark_completed(sid,hash_)
            except Exception as error:
                errors.append(LABELS.get(backend,backend)+'：'+str(error))
        if accepted or completed: self.export_queue()
        return accepted,completed,sorted(set(errors))

    def transfer(self, sid, item, backend=None):
        row=self.db.execute('SELECT aliases FROM shows WHERE id=?',(sid,)).fetchone()
        if not row: raise ValueError('该动画已删除，停止转交')
        if not matches_show(item,self.search_aliases(sid)): raise ValueError('资源片名与当前动画不符，停止转交')
        if any(w.casefold() in item['title'].casefold() for w in self.show_options(sid).get('exclude_words',[])):
            raise ValueError('资源已被本番排除词过滤')
        # An existing handoff belongs to its original downloader, regardless of the current default.
        if self.db.execute("SELECT 1 FROM releases WHERE show_id=? AND episode=? AND status IN ('待接收','已转交','已完成')", (sid, item['episode'])).fetchone():
            raise ValueError('本集已有转交记录')
        from downloaders import client,encode_profile
        backend=backend or self.setting('downloader','bitcomet')
        profile=self.downloader_profile(backend)
        exe = profile.get('exe','')
        if backend=='bitcomet' and (not exe or not Path(exe).is_file()):
            raise ValueError('请在设置中选择 BitComet.exe')
        adapter=None if backend=='bitcomet' else client(backend,profile)
        output = Path(self.setting('output', DEFAULT_OUTPUT))
        if not output.is_absolute():
            raise ValueError('下载目录必须为绝对路径')
        first,last,_=self.episode_rule(sid)
        ep=episode_number(item['episode'])
        if ep is not None and (ep < first or last and ep > last):
            raise ValueError('本集已超出追踪范围或被完成进度淘汰')
        if adapter: adapter.test()
        cached=self.root/'torrents'/(item['hash']+'.torrent')
        data = cached.read_bytes() if cached.is_file() else fetch(resolve_torrent_url(item))
        validate_torrent(data, item['hash'])
        if not self.db.execute('SELECT 1 FROM shows WHERE id=?',(sid,)).fetchone(): raise ValueError('该动画已删除，停止转交')
        output.mkdir(parents=True, exist_ok=True)
        folder = self.root / 'torrents'
        folder.mkdir(exist_ok=True)
        torrent = folder / (item['hash'] + '.torrent')
        torrent.write_bytes(data)
        copies=output/'_种子'; copies.mkdir(exist_ok=True)
        (copies/torrent.name).write_bytes(data)
        if adapter:
            existing=adapter.snapshot({item['hash']}).get(item['hash'])
            if existing and not same_directory(existing.get('directory',''),str(output)):
                raise ValueError('此下载器已有同一资源，但保存目录不同，请先在下载器中核对目录')
            self.db.execute('INSERT OR REPLACE INTO handoffs VALUES(?,?,?,?,?)',(sid,item['hash'],backend,encode_profile(profile),(existing or {}).get('id','')))
            # Retain pending on an ambiguous transport failure; retry requires an explicit history reset.
            self.db.execute("UPDATE releases SET status=?,output=? WHERE show_id=? AND hash=?",('已转交' if existing else '待接收',str(output),sid,item['hash']))
            self.db.commit()
            if existing:
                state='already_received'
                if existing.get('complete'): self.mark_completed(sid,item['hash']); state='already_completed'
                self.export_queue()
            else:
                try: remote_id=adapter.add(data,torrent,output)
                except Exception as error:
                    raise ValueError(str(error)+'；已保留待接收记录，请在转交历史核对后重试') from None
                self.db.execute('UPDATE handoffs SET remote_id=? WHERE show_id=? AND hash=?',(remote_id,sid,item['hash'])); self.db.commit()
                state='pending'
            return {'state':state,'torrent':str(copies/torrent.name),'location':str(output),'backend':backend}
        task_path=profile.get('task_xml') or discover_task_file(exe)
        self.db.execute('INSERT OR REPLACE INTO handoffs VALUES(?,?,?,?,?)',(sid,item['hash'],backend,encode_profile(profile),'')); self.db.commit()
        try: existing=task_records(task_path).get(item['hash']) if task_path else None
        except (OSError,ET.ParseError,ValueError): existing=None
        if existing and same_directory(existing.get('SaveDirectory',''),str(output)):
            self.db.execute("UPDATE releases SET status='已转交',output=? WHERE show_id=? AND hash=?",(str(output),sid,item['hash'])); self.db.commit()
            if item['hash'] in completed_hashes(task_path):
                self.mark_completed(sid,item['hash']); state='already_completed'
            else: state='already_received'
            self.export_queue()
            return {'state':state,'torrent':str(copies/torrent.name),'location':existing.get('SaveLocation',str(output))}
        self.db.execute("UPDATE releases SET status='待接收',output=? WHERE show_id=? AND hash=?", (str(output), sid, item['hash']))
        self.db.commit()
        subprocess.Popen([exe, str(torrent.resolve()), '--output=' + str(output), '--silent'],cwd=str(Path(exe).parent))
        return {'state':'pending','torrent':str(copies/torrent.name),'location':str(output)}

    def export_queue(self):
        result = []
        for name, payload, output, status in self.db.execute("SELECT shows.name,releases.payload,releases.output,releases.status FROM releases JOIN shows ON shows.id=show_id WHERE status IN ('已转交','已完成')"):
            item = json.loads(payload)
            if item['subtitle'] != '标题声明中文':
                result.append(dict(show=name, title=item['title'], infohash=item['hash'], episode=item['episode'],
                                   subtitle=item['subtitle'], download_directory=output,
                                   state='下载完成，待检查字幕轨道' if status=='已完成' else '待确认下载完成及字幕轨道', torrent_path=str(self.root / 'torrents' / (item['hash']+'.torrent'))))
        dest = self.root / 'ai-subtitle-queue.json'
        dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return dest

def validate_torrent(data, expected):
    import hashlib
    pos = 0
    info = None
    def parse(depth=0):
        nonlocal pos, info
        if depth > 60 or pos >= len(data):
            raise ValueError('无效种子结构')
        start = pos
        c = data[pos:pos+1]
        if c == b'i':
            pos += 1
            end = data.index(b'e', pos)
            val = int(data[pos:end]); pos = end+1
            return val
        if c in (b'd', b'l'):
            pos += 1
            val = {} if c == b'd' else []
            while data[pos:pos+1] != b'e':
                key = parse(depth+1)
                if c == b'd':
                    a = pos
                    value = parse(depth+1)
                    if depth == 0 and key == b'info':
                        info = data[a:pos]
                    val[key] = value
                else:
                    val.append(key)
            pos += 1
            return val
        if c.isdigit():
            end = data.index(b':', pos)
            n = int(data[pos:end]); pos = end+1
            if n < 0 or pos+n > len(data):
                raise ValueError('种子字符串越界')
            val = data[pos:pos+n]; pos += n
            return val
        raise ValueError('下载内容不是 BT 种子')
    try:
        obj = parse()
        if pos != len(data) or not isinstance(obj, dict) or not isinstance(obj.get(b'info'), dict) or not info:
            raise ValueError('下载内容不是 BT 种子')
        if hashlib.sha1(info).hexdigest() != expected:
            raise ValueError('种子 infohash 与候选不符')
    except (IndexError, KeyError) as e:
        raise ValueError('损坏的种子') from e

def episode_number(value):
    return int(value) if str(value).isdigit() else None

def same_directory(a,b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a))==os.path.normcase(os.path.abspath(b))

def task_records(path):
    raw=Path(path).read_bytes()
    if len(raw)>32*1024*1024: raise ValueError('BitComet 任务文件过大')
    result={}
    for task in ET.fromstring(raw).iter('Torrent'):
        hash_=task.get('InfoHashHex','').lower()
        if re.fullmatch('[a-f0-9]{40}',hash_): result[hash_]=dict(task.attrib)
    return result

def episode_query(word, first=1):
    # Nyaa supports negative quoted literals. Exclude standard episode labels on the server.
    # The local parser remains authoritative for unusual release naming.
    exclusions=[]
    for ep in reversed(range(1,first)):
        group=[f'-" - {ep:02d}"',f'-"[{ep:02d}]"',f'-"EP{ep:02d}"']
        if len(urllib.parse.quote(word+' '+' '.join(exclusions+group)))>6000:
            break
        exclusions.extend(group)
    return word + (' ' + ' '.join(exclusions) if exclusions else '')

def completed_hashes(path):
    raw=Path(path).read_bytes()
    if len(raw)>32*1024*1024: raise ValueError('BitComet 任务文件过大')
    root=ET.fromstring(raw)
    hashes=set()
    for task in root.iter('Torrent'):
        a=task.attrib
        hash_=a.get('InfoHashHex','').lower()
        try:
            size=int(a.get('SelectedSize','0'))
            left=int(a.get('Left','-1'))
            # Completed selected files must constitute the whole torrent; partial selection
            # could otherwise retire earlier episodes after downloading only a subtitle.
            full=int(a.get('Size','0'))
            finished=bool(a.get('FinishDate','').strip()) and not a.get('FinishDate','').startswith('0000')
            deleted=any(a.get(k,'').lower() in ('true','1') for k in ('Deleted','Removed','Recycled'))
            if re.fullmatch('[a-f0-9]{40}',hash_) and full>0 and size==full and left==0 and finished and not deleted:
                hashes.add(hash_)
        except ValueError: continue
    return hashes

def discover_task_file(exe=''):
    exe=exe or discover_bitcomet()
    candidates=[Path(exe).parent/'Downloads.xml'] if exe else []
    for base in [os.environ.get('APPDATA',''),os.environ.get('LOCALAPPDATA','')]:
        if base: candidates.append(Path(base)/'BitComet'/'Downloads.xml')
    return next((str(p) for p in candidates if p.is_file()),'')

def allowed_torrent_url(url,hash_):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443): return False
    return bool(parsed.hostname=='nyaa.si' and re.fullmatch(r'/download/\d+\.torrent',parsed.path) or
                parsed.hostname=='mikanani.me' and re.fullmatch('/Download/[0-9]{8}/'+hash_+r'\.torrent',parsed.path))

def resolve_torrent_url(item):
    if allowed_torrent_url(item['url'],item['hash']): return item['url']
    page='https://mikanani.me/Home/Episode/'+item['hash']
    if item['url']!=page: raise ValueError('不是支持的来源种子下载链接')
    class Links(HTMLParser):
        def __init__(self): super().__init__(); self.links=[]
        def handle_starttag(self,tag,attrs):
            if tag=='a':
                href=dict(attrs).get('href')
                if href: self.links.append(urllib.parse.urljoin(page,href))
    parser=Links()
    try: parser.feed(fetch(page).decode('utf-8'))
    except Exception as e: raise ValueError('补充源种子页暂不可用，请打开资源页面核对：'+str(e)) from e
    for url in parser.links:
        if allowed_torrent_url(url,item['hash']): return url
    raise ValueError('补充源没有可下载的对应种子，请打开资源页面核对')

def search_fetch(url, cancelled=None, report=None, timeout=10):
    """Retry transient search failures once; never weaken TLS or retry bad input."""
    for attempt in range(2):
        if cancelled and cancelled.is_set(): raise InterruptedError('搜索已取消')
        try:
            return fetch(url,timeout=timeout)
        except (urllib.error.URLError, OSError) as error:
            retryable=not isinstance(error,urllib.error.HTTPError) or error.code in (408,429,500,502,503,504)
            certificate=isinstance(error,ssl.SSLCertVerificationError) or isinstance(getattr(error,'reason',None),ssl.SSLCertVerificationError)
            if attempt or not retryable or certificate: raise
            if report is not None: report['retries']=report.get('retries',0)+1
            if cancelled:
                if cancelled.wait(.25): raise InterruptedError('搜索已取消')
            else: time.sleep(.25)


@lru_cache(maxsize=128)
def cour_episode_span(subject, day):
    # Use the subject's actual episode table, not an assumed 12/13-episode offset.
    raw=json.loads(search_fetch('https://api.bgm.tv/v0/episodes?'+urllib.parse.urlencode(
        {'subject_id':subject,'type':0,'limit':100})))
    rows=[r for r in raw.get('data',[]) if r.get('type')==0 and str(r.get('subject_id'))==str(subject)]
    values=[r.get('sort') for r in rows]
    if not rows or raw.get('total',len(rows))>len(rows) or not all(
            isinstance(n,(int,float)) and n>0 and int(n)==n for n in values):
        raise ValueError('分集资料不完整，保留完整标题检索')
    numbers=sorted(set(int(n) for n in values))
    if numbers[0]<=1 or numbers!=list(range(numbers[0],numbers[-1]+1)):
        raise ValueError('未确认连续编号的后半部范围，保留完整标题检索')
    return numbers[0],numbers[-1]


def cached_cour_span(subject, cache_dir=None, report=None):
    """Persist only validated spans. Reuse for a day, retain a 30-day offline fallback."""
    saved=None; path=Path(cache_dir)/f'cour-{subject}.json' if cache_dir else None
    if path:
        try:
            obj=json.loads(path.read_text(encoding='utf-8'))
            span=obj['span']; age=time.time()-obj['checked_at']
            if (str(obj['subject'])==str(subject) and len(span)==2 and
                    all(type(n) is int for n in span) and 1<span[0]<=span[1]<=10000 and
                    span[1]-span[0]<100 and 0<=age<=30*86400):
                saved=tuple(span)
                if age<86400:
                    if report is not None: report['cache_state']='本地已核对分集范围'
                    return saved
        except (OSError,ValueError,TypeError,KeyError): pass
    try:
        span=cour_episode_span(str(subject),time.strftime('%Y-%m-%d'))
    except Exception as e:
        if saved:
            if report is not None: report['warning']='分集刷新失败，复用30天内已核对范围：'+str(e)
            return saved
        raise
    if path:
        temporary=path.with_suffix(f'.{time.time_ns()}.tmp')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps({'subject':str(subject),'span':span,'checked_at':time.time()}),encoding='utf-8')
            temporary.replace(path)
        except OSError as e:
            if report is not None: report['warning']='分集范围已核对，但缓存保存失败：'+str(e)
        finally: temporary.unlink(missing_ok=True)
    return span


def search_words(aliases):
    words=list(dict.fromkeys(v.strip() for k,v in aliases.items()
        if not k.startswith('_') and isinstance(v,str) and v.strip()))
    # Publishers use both 姐妹 and 姊妹; this is a spelling variant, not fuzzy matching.
    for word in list(words):
        if '姐妹' in word: words.append(word.replace('姐妹','姊妹'))
        elif '姊妹' in word: words.append(word.replace('姊妹','姐妹'))
    return list(dict.fromkeys(words))


def search_show(aliases, cancelled=None, first=1, last=0, diagnostics=None, use_garden=True, use_external=None, on_progress=None, cache_dir=None, request_control=None, exclude_words=()):
    merged, errors = {}, []
    reports=diagnostics if diagnostics is not None else []
    effective=dict(aliases); span=None
    if use_external is None: use_external=use_garden
    originals=search_words(aliases)
    bases=[cour_base(v) for v in originals]
    if any(v!=b for v,b in zip(originals,bases)) and str(aliases.get('_bgm_id','')).isdigit():
        report={'source':'Bangumi','query':'核对后半部分集编号','returned':0,'kept':0,'ani':0,'error':'','checked_at':time.time()}
        try:
            span=cached_cour_span(str(aliases['_bgm_id']),cache_dir,report)
            report.update(returned=span[1]-span[0]+1,episode_span=list(span),query=f'后半部对应全篇第 {span[0]}～{span[1]} 集')
            for i,(name,base) in enumerate(zip(originals,bases)):
                if base!=name and len(title_key(base))>=4: effective[f'总标题{i}']=base
        except Exception as e: report['error']=str(e); errors.append('Bangumi: '+str(e))
        reports.append(report)
    words=search_words(effective)
    if span:
        # A total-title query also matches a release carrying the longer Part 2 title.
        # Avoid spending another round of requests on the known over-specific labels.
        words=list(dict.fromkeys(cour_base(word) for word in words))

    def stopped(): return bool(cancelled and cancelled.is_set())
    batches=Queue()

    def source_queries(source):
        local=[]
        def query(word,url,parser):
            report={'source':source,'query':word,'returned':0,'kept':0,'ani':0,'error':''}
            started=time.monotonic()
            try:
                operation=lambda:search_fetch(url,cancelled,report)
                raw=request_control.request(url,operation,cancelled,report) if request_control else operation()
                items=parser(raw)
                local.append((items,report)); batches.put((items,report)); return raw,items
            except Exception as e:
                report['error']=str(e) or type(e).__name__; local.append(([],report)); batches.put(([],report)); return None,[]
            finally:
                report.update(checked_at=time.time(),elapsed=round(time.monotonic()-started,2))

        if source=='AnimeGarden':
            try:
                subject=aliases.get('_bgm_id') or resolve_subject(aliases.get('中文名',''),aliases.get('日文名',''))
                if not str(subject).isdigit(): raise ValueError('作品ID未匹配；请编辑名称并选择正确作品后保存')
                for fansub in ['ANi','']:
                    seen=set()
                    for page in range(1,4):
                        if stopped(): break
                        params={'subject':subject,'page':page,'pageSize':100,'type':'动画'}
                        if fansub: params['fansub']=fansub
                        label=f'作品ID {subject}'+(' / ANi' if fansub else '')+f' / 第{page}页'
                        raw,items=query(label,'https://api.animes.garden/resources?'+urllib.parse.urlencode(params),lambda r:parse_garden(r,subject))
                        if raw is None: break
                        obj=json.loads(raw); hashes={x['hash'] for x in items}
                        pagination=obj.get('pagination',{})
                        more=pagination.get('complete') is False or (not pagination and len(obj.get('resources',[]))>=100)
                        if not more or not hashes-seen: break
                        seen.update(hashes)
                        if page==3: local[-1][1]['warning']='仍有更多历史资源，本轮已达3页上限'
            except Exception as e:
                pair=([],{'source':source,'query':'匹配作品ID','returned':0,'kept':0,'ani':0,'error':str(e),'checked_at':time.time()})
                local.append(pair);batches.put(pair)
        else:
            for word in words:
                if stopped(): break
                if source=='Nyaa':
                    q=episode_query('"'+word.replace('"','')+'"',max(first,span[0]) if span else first)
                    url='https://nyaa.si/?'+urllib.parse.urlencode(dict(page='rss',q=q,c='1_0',f='0'))
                    _,items=query(word,url,parse_rss)
                    if len(items)>=75 and not any(x['ani'] for x in items) and not stopped():
                        ani_url='https://nyaa.si/?'+urllib.parse.urlencode(dict(page='rss',q=q+' ANI',c='1_0',f='0'))
                        query(word+' / ANI补搜',ani_url,parse_rss)
                else:
                    base,param={'Mikan':('https://mikanani.me/RSS/Search','searchstr'),
                                'DMHY':('https://share.dmhy.org/topics/rss/rss.xml','keyword')}[source]
                    query(word,base+'?'+urllib.parse.urlencode({param:word}),lambda r:parse_external_rss(r,source))
        return local

    sources=['Nyaa']+(['AnimeGarden'] if use_garden else [])+(['Mikan','DMHY'] if use_external else [])
    # Different sources run concurrently; a timeout on one cannot hold up the others.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(source_queries,source) for source in sources if not stopped()]
        while any(not future.done() for future in futures) or not batches.empty():
            try: items,report=batches.get(timeout=.1)
            except Empty: continue
            kept=[]; rejected=0; ranged=0; excluded=0;changed=False
            for original in items:
                if not matches_show(original,effective): rejected+=1; continue
                if any(w.casefold() in original['title'].casefold() for w in exclude_words): excluded+=1; continue
                item=dict(original)
                ep=episode_number(item['episode'])
                if span and ep is not None and ep<span[0] and ep<=span[1]-span[0]+1 and matches_show(original,aliases):
                    # An explicitly identified cour may restart numbering from 01.
                    item['source_episode']=str(ep); ep+=span[0]-1; item['episode']=str(ep)
                if ep is not None and (ep<first or last and ep>last or span and not span[0]<=ep<=span[1]):
                    ranged+=1; continue
                # Unnumbered packs cannot prove that they belong to this cour.
                if span and ep is None: ranged+=1; continue
                if span:
                    item['subject_id']=str(aliases['_bgm_id'])
                    item['identity_basis']='Bangumi分集范围及总标题匹配'
                kept.append(item);previous=merged.get(item['hash']);merge_release(merged,item)
                changed=changed or previous!=merged.get(item['hash'])
            report.update(returned=len(items),kept=len(kept),ani=sum(x['ani'] for x in kept),
                          ani_returned=sum(x['ani'] for x in items),identity_rejected=rejected,range_rejected=ranged,preference_rejected=excluded)
            reports.append(report)
            if report['error']: errors.append(report['source']+' / '+report['query']+': '+report['error'])
            if changed and on_progress and not stopped(): on_progress(list(merged.values()))
        for future in futures: future.result()
    return list(merged.values()), errors

def lookup(name):
    # Bangumi supplies Chinese/Japanese titles; Jikan supplies romanized/English titles.
    raw = json.loads(fetch('https://api.bgm.tv/v0/search/subjects?limit=8', {'keyword': name, 'filter': {'type': [2]}}))
    result = []
    for entry in raw.get('data', []):
        result.append({'中文名': entry.get('name_cn', ''), '日文名': entry.get('name', ''),
                       '罗马音': '', '英文名': '', 'id': entry['id']})
    return result

def normalized_title(title):
    # Tolerate width/spacing differences, but retain punctuation and season numbers.
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', title or '')).casefold()

def complete_aliases(japanese):
    if not japanese.strip():
        return '', ''
    errors = []
    roman, english = '', ''
    query = '''query ($search: String) { Page(perPage: 20) {
      media(search: $search, type: ANIME) { id title { romaji english native } }
    } }'''
    try:
        raw = json.loads(fetch('https://graphql.anilist.co',
                         {'query': query, 'variables': {'search': japanese}}, timeout=10))
        if raw.get('errors'):
            raise ValueError(str(raw['errors']))
        matches = [entry['title'] for entry in raw['data']['Page']['media']
                   if normalized_title(entry['title'].get('native')) == normalized_title(japanese)]
        # Several editions may share a title. Only accept unambiguous names.
        romaji = {x.get('romaji') for x in matches if x.get('romaji')}
        eng = {x.get('english') for x in matches if x.get('english')}
        roman = next(iter(romaji)) if len(romaji) == 1 else ''
        english = next(iter(eng)) if len(eng) == 1 else ''
        if roman and english:
            return roman, english
    except Exception as e:
        errors.append('AniList: ' + str(e))
    try:
        raw = json.loads(fetch('https://api.jikan.moe/v4/anime?' + urllib.parse.urlencode({'q': japanese, 'limit': 20}), timeout=8))
        matches = [entry for entry in raw.get('data', [])
                   if normalized_title(entry.get('title_japanese')) == normalized_title(japanese)]
        romaji = {x.get('title') for x in matches if x.get('title')}
        eng = {x.get('title_english') for x in matches if x.get('title_english')}
        roman = roman or (next(iter(romaji)) if len(romaji) == 1 else '')
        english = english or (next(iter(eng)) if len(eng) == 1 else '')
    except Exception as e:
        errors.append('Jikan: ' + str(e))
    if not roman and not english and len(errors) == 2:
        raise ValueError('名称补全服务暂不可用：' + '；'.join(errors))
    return roman, english

def fill_missing_aliases(name, aliases):
    result = dict(aliases)
    if not result.get('日文名'):
        entries = lookup(result.get('中文名') or name)
        target = normalized_title(result.get('中文名') or name)
        matches = [e for e in entries if target in
                   {normalized_title(e['中文名']), normalized_title(e['日文名'])}]
        if len(matches) != 1:
            raise ValueError('未找到唯一匹配的作品，请编辑名称并选择作品')
        for key in ['中文名', '日文名']:
            result[key] = result.get(key) or matches[0][key]
    if not result.get('罗马音') or not result.get('英文名'):
        roman, english = complete_aliases(result['日文名'])
        result['罗马音'] = result.get('罗马音') or roman
        result['英文名'] = result.get('英文名') or english
    return result
