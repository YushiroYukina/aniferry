import base64
from contextlib import contextmanager,closing
from email.parser import BytesParser
from email.policy import default
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit

from core import Store
from downloaders import QBittorrent,Aria2,Thunder,encode_profile,decode_profile
from test_core import release

INFO=b'd6:lengthi1e4:name4:test12:piece lengthi16384e6:pieces20:abcdefghijklmnopqrste'
TORRENT=b'd4:info'+INFO+b'e'
HASH=hashlib.sha1(INFO).hexdigest()


@contextmanager
def api_server():
    state={'visible':False,'complete':False,'partial':False,'directory':'','added':0,'wronghash':False,'auth':True,'failadd':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,value,code=200,cookie=False):
            self.send_response(code)
            if cookie: self.send_header('Set-Cookie','SID=tracker-test; Path=/')
            self.end_headers(); self.wfile.write(value if isinstance(value,bytes) else json.dumps(value).encode())
        def do_GET(self):
            if self.headers.get('Cookie')!='SID=tracker-test': return self.reply(b'Forbidden',403)
            path=urlsplit(self.path).path
            if path.endswith('/version'): return self.reply(b'v5.0.0')
            if path.endswith('/info'):
                return self.reply([{'hash':'b'*40 if state['wronghash'] else HASH,'save_path':state['directory'],'progress':int(state['complete']),'amount_left':0 if state['complete'] else 1}] if state['visible'] else [])
            if path.endswith('/files'):
                return self.reply([{'priority':1,'progress':int(state['complete'])},{'priority':0 if state['partial'] else 1,'progress':1}])
            self.reply(b'Not found',404)
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if self.path.endswith('/auth/login'):
                fields=parse_qs(body.decode())
                good=fields=={'username':['user'],'password':['test-secret']} and state['auth']
                return self.reply(b'Ok.' if good else b'Fails.',cookie=good)
            if self.path.endswith('/torrents/add'):
                if self.headers.get('Cookie')!='SID=tracker-test': return self.reply(b'Forbidden',403)
                if state['failadd']: return self.reply(b'Error',503)
                parts=BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+body)
                fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in parts.iter_parts()}
                if fields.get('torrents')!=TORRENT: return self.reply(b'Invalid torrent',400)
                state['directory']=fields['savepath'].decode(); state['added']+=1
                return self.reply(b'Ok.')
            payload=json.loads(body); params=payload['params']
            if not params or params.pop(0)!='token:test-secret':
                return self.reply({'jsonrpc':'2.0','id':'anime-tracker','error':{'code':1,'message':'Unauthorized'}})
            method=payload['method'].split('.')[-1]
            def reply(result): self.reply({'jsonrpc':'2.0','id':'anime-tracker','result':result})
            if method=='getVersion': return reply({'version':'1.37.0','enabledFeatures':['BitTorrent']})
            if method=='addTorrent':
                if state['failadd']: return self.reply(b'Error',503)
                if base64.b64decode(params[0])!=TORRENT or params[1]!=[]: return self.reply(b'Bad torrent',400)
                state['directory']=params[2]['dir']; state['added']+=1; return reply('1234567890abcdef')
            task={'gid':'1234567890abcdef','infoHash':'b'*40 if state['wronghash'] else HASH,
                  'dir':state['directory'],'totalLength':'2','completedLength':'2' if state['complete'] else '0',
                  'status':'active','files':[{'selected':'true','length':'1','completedLength':'1' if state['complete'] else '0'},
                          {'selected':'false' if state['partial'] else 'true','length':'1','completedLength':'1'}]}
            if method=='tellStatus':
                if state['visible']: return reply(task)
                return self.reply({'jsonrpc':'2.0','id':'anime-tracker','error':{'code':1}})
            return reply([task] if method=='tellActive' and state['visible'] else [])
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port),state
    finally: server.shutdown(); server.server_close(); thread.join()


class ProtocolTests(unittest.TestCase):
    def test_end_to_end_http_receipt_completion_and_backend_switch(self):
        for backend in ('qbittorrent','aria2','motrix'):
            with self.subTest(backend=backend),api_server() as (url,state),tempfile.TemporaryDirectory() as tmp:
                store=Store(Path(tmp)/'data')
                try:
                    output=Path(tmp)/'中文动画'; store.set_setting('output',str(output)); store.set_setting('downloader',backend)
                    profile={'url':url if backend=='qbittorrent' else url+'/jsonrpc','username':'user','password':'test-secret','secret':'test-secret'}
                    store.set_setting('downloader_profile_'+backend,encode_profile(profile))
                    sid=store.save_show('Test',{'英文名':'Test'}); item=dict(release(5,'5'),hash=HASH)
                    store.ingest(sid,[release(i,str(i)) for i in range(1,5)]+[item])
                    with patch('core.fetch',return_value=TORRENT): result=store.transfer(sid,item)
                    self.assertEqual(result['state'],'pending'); self.assertEqual(state['added'],1)
                    self.assertTrue((output/'_种子'/(HASH+'.torrent')).is_file())
                    self.assertEqual(store.poll_downloaders()[:2],(0,0))
                    # Selecting a different default cannot change which application owns this handoff.
                    store.set_setting('downloader','bitcomet')
                    store.set_setting('bitcomet',str(Path(tmp)/'not-installed.exe'))
                    state['visible']=True
                    state['directory']=str(Path(tmp)/'wrong'); self.assertEqual(store.poll_downloaders()[:2],(0,0))
                    state['directory']=str(output); state['wronghash']=True
                    self.assertEqual(store.poll_downloaders()[:2],(0,0)); state['wronghash']=False
                    self.assertEqual(store.poll_downloaders()[:2],(1,0))
                    state['complete']=True; state['partial']=True
                    self.assertEqual(store.poll_downloaders()[:2],(0,0)); self.assertEqual(store.episode_rule(sid)[2],0)
                    state['partial']=False; self.assertEqual(store.poll_downloaders()[:2],(0,1))
                    self.assertEqual(store.episode_rule(sid)[0],4)
                    self.assertEqual(store.db.execute("SELECT count(*) FROM releases WHERE status='弃置'").fetchone()[0],3)
                    with self.assertRaisesRegex(ValueError,'已有转交'): store.transfer(sid,item)
                    self.assertEqual(state['added'],1)
                    self.assertNotIn('test-secret',store.db.execute('SELECT profile FROM handoffs').fetchone()[0])
                finally: store.db.close()

    def test_existing_task_not_submitted_twice(self):
        for backend in ('qbittorrent','aria2','motrix'):
            with self.subTest(backend=backend),api_server() as (url,state),tempfile.TemporaryDirectory() as tmp:
                s=Store(tmp)
                try:
                    s.set_setting('downloader',backend); s.set_setting('output',tmp)
                    s.set_setting('downloader_profile_'+backend,encode_profile({'url':url,'username':'user','password':'test-secret','secret':'test-secret'}))
                    sid=s.save_show('Test',{'英文名':'Test'}); item=dict(release(5,'5'),hash=HASH); s.ingest(sid,[item])
                    state.update(visible=True,complete=True,directory=tmp)
                    with patch('core.fetch',return_value=TORRENT): result=s.transfer(sid,item)
                    self.assertEqual(result['state'],'already_completed'); self.assertEqual(state['added'],0)
                finally: s.db.close()

    def test_authentication_failures_are_clear_and_hide_secrets(self):
        with api_server() as (url,state):
            state['auth']=False
            with self.assertRaisesRegex(ValueError,'登录失败'): QBittorrent({'url':url,'username':'user','password':'test-secret'}).test()
            with self.assertRaisesRegex(ValueError,'RPC') as error: Aria2({'url':url,'secret':'wrong-secret'}).test()
            self.assertNotIn('wrong-secret',str(error.exception))

    def test_ambiguous_add_failure_retains_pending_and_blocks_duplicate(self):
        with api_server() as (url,state),tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            try:
                s.set_setting('downloader','aria2'); s.set_setting('output',tmp)
                s.set_setting('downloader_profile_aria2',encode_profile({'url':url,'secret':'test-secret'}))
                sid=s.save_show('Test',{'英文名':'Test'}); item=dict(release(5,'5'),hash=HASH); s.ingest(sid,[item]); state['failadd']=True
                with patch('core.fetch',return_value=TORRENT),self.assertRaisesRegex(ValueError,'保留待接收'): s.transfer(sid,item)
                self.assertEqual(s.db.execute('SELECT status FROM releases').fetchone()[0],'待接收')
                with self.assertRaisesRegex(ValueError,'已有转交'): s.transfer(sid,item)
            finally: s.db.close()

    def test_encrypted_profile_roundtrip(self):
        p={'url':'http://127.0.0.1:6800/jsonrpc','password':'密钥-long-secret','secret':'RPC-private'}
        encoded=encode_profile(p); self.assertNotIn('RPC-private',encoded); self.assertNotIn('long-secret',encoded)
        self.assertEqual(decode_profile(encoded),p)

    def test_thunder_launch_and_database_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'Thunder.exe'; exe.touch(); torrent=Path(tmp)/'test.torrent'; torrent.write_bytes(TORRENT)
            path=Path(tmp)/'TaskDb.dat'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE TaskBase(TaskId INTEGER, SavePath TEXT,ResourceSize INTEGER,TotalReceiveValidSize INTEGER,CompletionTime INTEGER,RemovingToRecycleTime INTEGER); CREATE TABLE BtTask(TaskId INTEGER,InfoId BLOB); CREATE TABLE BtFile(BtTaskId INTEGER,FileSize INTEGER,ReceivedSize INTEGER,Download INTEGER);')
                db.execute('INSERT INTO TaskBase VALUES(1,?,2,2,123,0)',(tmp,)); db.execute('INSERT INTO BtTask VALUES(1,?)',(bytes.fromhex(HASH),)); db.execute('INSERT INTO BtFile VALUES(1,2,2,1)')
            adapter=Thunder({'exe':str(exe),'task_db':str(path)})
            with patch('downloaders.subprocess.Popen') as launch:
                adapter.add(TORRENT,torrent,tmp)
                self.assertEqual(launch.call_args.args[0],[str(exe),str(torrent.resolve()),'-StartType:torrent'])
            self.assertTrue(adapter.snapshot({HASH})[HASH]['complete']); self.assertEqual(adapter.snapshot({'b'*40}),{})
            with closing(sqlite3.connect(path)) as db, db: db.execute('UPDATE BtFile SET Download=0')
            self.assertFalse(adapter.snapshot({HASH})[HASH]['complete'])
            with closing(sqlite3.connect(path)) as db, db: db.execute('UPDATE TaskBase SET RemovingToRecycleTime=1')
            self.assertEqual(adapter.snapshot({HASH}),{})

    def test_bitcomet_observation_cannot_acknowledge_other_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            try:
                sid=s.save_show('Test',{'英文名':'Test'}); item=release(1); s.ingest(sid,[item])
                s.db.execute("UPDATE releases SET status='待接收',output=?",(tmp,))
                s.db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)',(sid,item['hash'],'aria2','{}','')); s.db.commit()
                self.assertEqual(s.sync_received({item['hash']:{'SaveDirectory':tmp}}),0)
                s.db.execute("UPDATE releases SET status='已转交'"); s.db.commit()
                self.assertEqual(s.sync_completed({item['hash']}),0)
            finally: s.db.close()

if __name__=='__main__': unittest.main()
