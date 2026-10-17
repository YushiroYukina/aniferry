"""Downloader protocols. A launch or an add response alone never proves receipt."""
import base64
import ctypes
import http.cookiejar
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing

LABELS = {'bitcomet':'BitComet', 'qbittorrent':'qBittorrent', 'aria2':'aria2',
          'motrix':'Motrix', 'thunder':'迅雷'}
DEFAULTS = {'qbittorrent':'http://127.0.0.1:8080', 'aria2':'http://127.0.0.1:6800/jsonrpc',
            'motrix':'http://127.0.0.1:16800/jsonrpc'}


def protect(value, decrypt=False):
    """Windows account-bound encryption for saved passwords and RPC tokens."""
    if not value: return ''
    if decrypt and not value.startswith('dpapi:'): return value
    if os.name != 'nt': raise ValueError('保存连接密码需要 Windows 用户加密功能')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value[6:]) if decrypt else value.encode('utf-8')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte))); target=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ValueError('无法读取此 Windows 用户保存的连接密码，请重新填写')
    try: result=ctypes.string_at(target.data,target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)
    return result.decode('utf-8') if decrypt else 'dpapi:'+base64.b64encode(result).decode('ascii')


def encode_profile(profile):
    result=dict(profile)
    for key in ('password','secret'):
        if result.get(key): result[key]=protect(result[key])
    return json.dumps(result,ensure_ascii=False)


def decode_profile(raw):
    result=json.loads(raw)
    for key in ('password','secret'):
        if result.get(key): result[key]=protect(result[key],True)
    return result


def discover_thunder():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,r'Xunlei.Bittorrent.6\shell\open\command') as key:
            command=winreg.QueryValue(key,None)
        import re
        match=re.match(r'"([^"]+)"|([^ ]+\.exe)',command,re.I)
        exe=(match.group(1) or match.group(2)) if match else ''
        if Path(exe).is_file(): return exe
    except (ImportError,OSError): pass
    return ''


def thunder_db(exe):
    path=Path(exe).parent.parent/'Profiles'/'TaskDb.dat'
    return str(path) if path.is_file() else ''


def endpoint(value):
    value=value.strip().rstrip('/')
    parsed=urllib.parse.urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('连接地址须为 http/https 地址；用户名和密码请在各自栏位填写')
    return value


class API:
    def __init__(self,profile):
        self.profile=profile
        self.url=endpoint(profile.get('url',''))
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self,path='',data=None,content_type=None):
        url=self.url+path
        headers={'Referer':self.url+'/', 'Origin':urllib.parse.urlunsplit((*urllib.parse.urlsplit(self.url)[:2],'','',''))}
        if content_type: headers['Content-Type']=content_type
        try:
            with self.opener.open(urllib.request.Request(url,data=data,headers=headers),timeout=10) as response:
                raw=response.read(16*1024*1024+1)
            if len(raw)>16*1024*1024: raise ValueError('下载器响应过大')
            return raw
        except urllib.error.HTTPError as error:
            raise ValueError(f'下载器接口返回 HTTP {error.code}，请检查地址、权限及密码') from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise ValueError('无法连接下载器，请确认程序正在运行并已启用控制接口') from None


class QBittorrent(API):
    def __init__(self,profile):
        super().__init__(profile)
        self.logged_in=False

    def login(self):
        if self.logged_in: return
        data=urllib.parse.urlencode({'username':self.profile.get('username',''),
                                     'password':self.profile.get('password','')}).encode()
        if self.request('/api/v2/auth/login',data,'application/x-www-form-urlencoded').strip()!=b'Ok.':
            raise ValueError('qBittorrent 登录失败，请检查 Web UI 用户名和密码')
        self.logged_in=True

    def test(self):
        self.login()
        return 'qBittorrent '+self.request('/api/v2/app/version').decode()

    def snapshot(self,hashes,ids=None):
        self.login()
        query=urllib.parse.urlencode({'hashes':'|'.join(hashes)})
        tasks=json.loads(self.request('/api/v2/torrents/info?'+query))
        result={}
        for task in tasks:
            hash_=task.get('hash','').lower()
            if hash_ not in hashes: continue
            candidate=float(task.get('progress',0))>=1 and int(task.get('amount_left',-1))==0
            files=json.loads(self.request('/api/v2/torrents/files?'+urllib.parse.urlencode({'hash':hash_}))) if candidate else []
            full=bool(files) and all(int(f.get('priority',0))>0 and float(f.get('progress',0))>=1 for f in files)
            complete=full and candidate
            result[hash_]={'directory':task.get('save_path',''),'complete':complete,'id':hash_}
        return result

    def add(self,data,torrent,output):
        self.login()
        boundary='AnimeTracker'+uuid.uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="savepath"\r\n\r\n{output}\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="{Path(torrent).name}"\r\n'
              'Content-Type: application/x-bittorrent\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
        response=self.request('/api/v2/torrents/add',body,'multipart/form-data; boundary='+boundary)
        if response.strip()!=b'Ok.': raise ValueError('qBittorrent 未确认提交成功，请查看转交历史后再重试')
        return ''


class Aria2(API):
    def rpc(self,method,params=None):
        args=list(params or [])
        if self.profile.get('secret'): args.insert(0,'token:'+self.profile['secret'])
        payload={'jsonrpc':'2.0','id':'anime-tracker','method':'aria2.'+method,'params':args}
        result=json.loads(self.request(data=json.dumps(payload).encode(),content_type='application/json'))
        if 'error' in result:
            raise ValueError(f'下载器 RPC 调用 {method} 失败（代码 {result["error"].get("code","未知")}），请核对密钥和配置')
        if result.get('id')!='anime-tracker' or 'result' not in result: raise ValueError('下载器返回了无法识别的 RPC 响应')
        return result['result']

    def test(self):
        result=self.rpc('getVersion')
        if 'BitTorrent' not in result.get('enabledFeatures',[]): raise ValueError('此下载器核心未启用 BitTorrent 功能')
        return 'aria2 '+result['version']

    def add(self,data,torrent,output):
        return self.rpc('addTorrent',[base64.b64encode(data).decode(),[],{'dir':str(output)}])

    def snapshot(self,hashes,ids=None):
        tasks=[]; missing=set(hashes)
        for hash_,gid in (ids or {}).items():
            if not gid: continue
            try: task=self.rpc('tellStatus',[gid])
            except ValueError: continue
            tasks.append(task)
            if task.get('infoHash','').lower()==hash_: missing.discard(hash_)
        if missing:
            tasks.extend(self.rpc('tellActive'))
            for method in ('tellWaiting','tellStopped'):
                for offset in range(0,10000,1000):
                    page=self.rpc(method,[offset,1000]); tasks.extend(page)
                    if len(page)<1000: break
        result={}
        for task in tasks:
            hash_=task.get('infoHash','').lower()
            if hash_ not in hashes or task.get('status')=='removed': continue
            files=task.get('files',[])
            total=int(task.get('totalLength',0)); received=int(task.get('completedLength',0))
            full=bool(files) and all(f.get('selected')=='true' and int(f.get('completedLength',0))>=int(f.get('length',0)) for f in files)
            complete=full and total>0 and received>=total and task.get('status') in ('complete','active','paused')
            result[hash_]={'directory':task.get('dir',''),'complete':complete,'id':task['gid']}
        return result


class Thunder:
    def __init__(self,profile): self.profile=profile

    def test(self):
        if not Path(self.profile.get('exe','')).is_file(): raise ValueError('请选择 Thunder.exe')
        path=self.profile.get('task_db','')
        if not path or not Path(path).is_file(): raise ValueError('请选择迅雷 Profiles 目录中的 TaskDb.dat，才能核对任务接收和完成')
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.execute('SELECT b.InfoId,t.SavePath,t.CompletionTime,t.RemovingToRecycleTime FROM BtTask b JOIN TaskBase t ON t.TaskId=b.TaskId LIMIT 0')
            db.execute('SELECT FileSize,ReceivedSize,Download FROM BtFile LIMIT 0')
        return '迅雷程序与任务记录可读取；新任务仍需在迅雷窗口确认目录'

    def add(self,data,torrent,output):
        subprocess.Popen([self.profile['exe'],str(Path(torrent).resolve()),'-StartType:torrent'],cwd=str(Path(self.profile['exe']).parent))
        return ''

    def snapshot(self,hashes,ids=None):
        self.test(); result={}
        with closing(sqlite3.connect(Path(self.profile['task_db']).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            for hash_ in hashes:
                rows=db.execute('SELECT t.TaskId,t.SavePath,t.ResourceSize,t.TotalReceiveValidSize,t.CompletionTime,t.RemovingToRecycleTime FROM BtTask b JOIN TaskBase t ON t.TaskId=b.TaskId WHERE lower(hex(b.InfoId))=?',(hash_,)).fetchall()
                for tid,directory,total,received,finished,recycled in rows:
                    if recycled: continue
                    files=db.execute('SELECT FileSize,ReceivedSize,Download FROM BtFile WHERE BtTaskId=?',(tid,)).fetchall()
                    full=bool(files) and all(selected and int(got or 0)>=int(size or 0) for size,got,selected in files)
                    complete=full and int(total or 0)>0 and int(received or 0)>=int(total) and int(finished or 0)>0
                    result[hash_]={'directory':directory,'complete':complete,'id':str(tid)}
        return result


def client(backend,profile):
    if backend=='qbittorrent': return QBittorrent(profile)
    if backend in ('aria2','motrix'): return Aria2(profile)
    if backend=='thunder': return Thunder(profile)
    raise ValueError('未知下载器')
