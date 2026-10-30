"""Local recovery, corrections and task observations. No downloader side effects."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import time
import urllib.parse


FEEDBACK_REASONS = ('暂不需要', '不是本作', '季度不符', '集数识别错误')


def subscription_url(value):
    """Only public RSS endpoints of supported providers; never arbitrary URLs."""
    value=value.strip()
    parsed=urllib.parse.urlsplit(value)
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None,443) or len(value)>2048):
        raise ValueError('请填写支持来源的公开 HTTPS RSS 地址')
    host=parsed.hostname
    query=urllib.parse.parse_qs(parsed.query)
    source=None
    if host=='nyaa.si' and parsed.path=='/' and query.get('page')==['rss']:
        source='Nyaa'
    elif host=='mikanani.me' and parsed.path in ('/RSS/Bangumi','/RSS/Search'):
        source='Mikan'
    elif host=='share.dmhy.org' and parsed.path=='/topics/rss/rss.xml':
        source='DMHY'
    if not source or any(k.casefold() in ('token','passkey','password','auth','apikey') for k in query):
        raise ValueError('支持 Nyaa RSS、蜜柑作品/搜索 RSS、動漫花園 RSS；请勿使用私人订阅地址')
    return value,source


class Maintenance:
    def init_maintenance(self):
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS repair_targets(show_id INTEGER,episode INTEGER,PRIMARY KEY(show_id,episode));
          CREATE TABLE IF NOT EXISTS corrections(show_id INTEGER,hash TEXT,reason TEXT,previous_status TEXT,created REAL,PRIMARY KEY(show_id,hash));
          CREATE TABLE IF NOT EXISTS task_observations(show_id INTEGER,hash TEXT,submitted REAL,checked REAL DEFAULT 0,state TEXT DEFAULT '',detail TEXT DEFAULT '',PRIMARY KEY(show_id,hash));
        ''')

    def repair_episodes(self,sid):
        return {r[0] for r in self.db.execute('SELECT episode FROM repair_targets WHERE show_id=?',(sid,))}

    def request_repair(self,sid,episodes):
        episodes=set(episodes)
        if not episodes or any(type(n) is not int or not 1<=n<=999 for n in episodes):
            raise ValueError('请选择1～999之间的集数')
        if not self.db.execute('SELECT 1 FROM shows WHERE id=?',(sid,)).fetchone():
            raise ValueError('动画已删除')
        with self.db:
            for ep in episodes:
                self.db.execute('INSERT OR IGNORE INTO repair_targets VALUES(?,?)',(sid,ep))
                # Only revive automatically discarded candidates, never user corrections.
                self.db.execute("UPDATE releases SET status='待确认' WHERE show_id=? AND episode=? AND status='弃置'",(sid,str(ep)))

    def end_repair(self,sid,episodes):
        with self.db:
            for ep in episodes:
                self.db.execute('DELETE FROM repair_targets WHERE show_id=? AND episode=?',(sid,ep))

    def episode_inventory(self,sid,end=None):
        """Known history is evidence; configured future episodes are not called missing."""
        rule=self.db.execute('SELECT first,last FROM episode_rules WHERE show_id=?',(sid,)).fetchone() or (1,0)
        rows=self.db.execute('SELECT episode,status FROM releases WHERE show_id=?',(sid,)).fetchall()
        states={}
        for ep,status in rows:
            if str(ep).isdigit(): states.setdefault(int(ep),set()).add(status)
        known=max(states,default=0)
        limit=end if end is not None else known
        if type(limit) is not int or not 0<=limit<=999: raise ValueError('检查截止集须为1～999')
        if rule[1]:limit=min(limit,rule[1])
        result=[]
        for ep in range(rule[0],limit+1):
            values=states.get(ep,set())
            state=next((v for v in ('已完成','已转交','待接收','待确认','已纠正','忽略','已清理','弃置','误匹配') if v in values),None)
            state={'弃置':'旧候选已隐藏','忽略':'已忽略'}.get(state,state)
            if state is None:state='未找到' if ep<=known else '尚无记录'
            result.append((ep,state))
        return result

    def correct_releases(self,sid,hashes,reason):
        if reason not in FEEDBACK_REASONS:raise ValueError('请选择纠正原因')
        count=0
        with self.db:
            for h in set(hashes):
                row=self.db.execute('SELECT status FROM releases WHERE show_id=? AND hash=?',(sid,h)).fetchone()
                if not row or row[0]!='待确认':continue
                self.db.execute('INSERT OR REPLACE INTO corrections VALUES(?,?,?,?,?)',(sid,h,reason,row[0],time.time()))
                self.db.execute("UPDATE releases SET status='已纠正' WHERE show_id=? AND hash=?",(sid,h));count+=1
        return count

    def restore_candidates(self,sid,hashes):
        count=0
        with self.db:
            for h in set(hashes):
                count+=self.db.execute("UPDATE releases SET status='待确认' WHERE show_id=? AND hash=? AND status IN ('已纠正','忽略','已清理','误匹配')",(sid,h)).rowcount
                self.db.execute('DELETE FROM corrections WHERE show_id=? AND hash=?',(sid,h))
        return count

    def observe_task(self,sid,h,state,detail=''):
        self.db.execute("INSERT INTO task_observations(show_id,hash,submitted,checked,state,detail) VALUES(?,?,0,?,?,?) ON CONFLICT(show_id,hash) DO UPDATE SET checked=excluded.checked,state=excluded.state,detail=excluded.detail",(sid,h,time.time(),state,detail))
        self.db.commit()

    def note_submission(self,sid,h):
        self.db.execute("INSERT OR REPLACE INTO task_observations(show_id,hash,submitted) VALUES(?,?,?)",(sid,h,time.time()))
        self.db.commit()

    def task_history(self):
        rows=self.db.execute("SELECT r.show_id,r.hash,s.name,r.title,r.output,r.status,o.submitted,o.checked,o.state,o.detail FROM releases r JOIN shows s ON s.id=r.show_id LEFT JOIN task_observations o ON o.show_id=r.show_id AND o.hash=r.hash WHERE r.status IN ('待接收','已转交','已完成') ORDER BY COALESCE(o.submitted,0) DESC,s.id").fetchall()
        result=[]
        for sid,h,name,title,output,status,submitted,checked,state,detail in rows:
            if status=='已完成':state='已完成'
            elif not state:state='待核对'
            if state=='未发现任务' and status=='待接收':
                state='长时间未接收' if submitted and time.time()-submitted>=120 else '等待接收'
            result.append(dict(sid=sid,hash=h,name=name,title=title,output=output,status=status,
                               submitted=submitted or 0,checked=checked or 0,state=state,detail=detail or ''))
        return result

    def reset_handoff_checked(self,sid,h):
        """Reset only after a fresh successful absence observation; cannot retry offline."""
        row=self.db.execute('SELECT status FROM releases WHERE show_id=? AND hash=?',(sid,h)).fetchone()
        obs=self.db.execute('SELECT checked,state FROM task_observations WHERE show_id=? AND hash=?',(sid,h)).fetchone()
        if not row or row[0]=='已完成':raise ValueError('完成记录不可重置')
        if not obs or obs[1]!='未发现任务' or not 0<=time.time()-obs[0]<60:
            raise ValueError('请先检查状态；只有下载器可连接且确认没有此任务时，才能恢复待选')
        self.backup('before-reset')
        with self.db:
            self.db.execute("UPDATE releases SET status='待确认',output='' WHERE show_id=? AND hash=?",(sid,h))
            self.db.execute('DELETE FROM handoffs WHERE show_id=? AND hash=?',(sid,h))
            self.db.execute('DELETE FROM task_observations WHERE show_id=? AND hash=?',(sid,h))
        self.export_queue()

    def backup_files(self):
        return sorted(self.root.glob('tracker-*.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True)

    @staticmethod
    def validate_backup(path):
        path=Path(path)
        if not path.is_file() or path.stat().st_size>256*1024*1024:raise ValueError('备份不存在或文件过大')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('备份完整性检查失败')
            for table,columns in {'shows':{'id','name','aliases'},'releases':{'show_id','hash','payload','episode','status','output'},'settings':{'key','value'}}.items():
                found={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
                if not columns<=found:raise ValueError('这不是可识别的番舟备份')
            return db.execute('SELECT COUNT(*) FROM shows').fetchone()[0]

    def restore_backup(self,path):
        path=Path(path).resolve()
        if path== (self.root/'tracker.sqlite3').resolve():raise ValueError('请选择备份文件')
        count=self.validate_backup(path)
        before=self.backup('before-restore')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
            source.backup(self.db)
        # Re-run additive migrations, including backups produced by old versions.
        self.db.close();self.__init__(self.root)
        self.export_queue()
        return count,before

    def export_library(self,path):
        # Deliberately omit connection settings, secrets, history and subscriptions.
        shows=[]
        for sid,name,raw in self.shows():
            options=self.show_options(sid)
            allowed={k:v for k,v in options.items() if k in ('continuation','dual','offset','numbering','range_basis','extra_aliases','exclude_words')}
            aliases={k:v for k,v in json.loads(raw).items() if k in ('中文名','日文名','罗马音','英文名','_bgm_id')}
            shows.append(dict(name=name,aliases=aliases,options=allowed))
        Path(path).write_text(json.dumps(dict(format='AniFerry-library',version=1,shows=shows),ensure_ascii=False,indent=2),encoding='utf-8')
        return len(shows)

    def import_library(self,path):
        path=Path(path)
        if path.stat().st_size>4*1024*1024:raise ValueError('列表文件过大')
        obj=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(obj,dict) or obj.get('format')!='AniFerry-library' or obj.get('version')!=1 or not isinstance(obj.get('shows'),list) or len(obj['shows'])>2000:
            raise ValueError('不是支持的追番列表')
        # Validate everything before changing the database; use a temporary store.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trial=type(self)(tmp)
            try:
                for show in obj['shows']:
                    if not isinstance(show,dict) or not isinstance(show.get('name'),str) or not 1<=len(show['name'].strip())<=300 or not isinstance(show.get('aliases'),dict):raise ValueError('列表条目格式不正确')
                    if any(not isinstance(k,str) or not isinstance(v,(str,int)) or len(str(v))>500 for k,v in show['aliases'].items()):raise ValueError('别名格式不正确')
                    if set(show['aliases'])-{'中文名','日文名','罗马音','英文名','_bgm_id'}:raise ValueError('列表包含不支持的别名字段')
                    if not isinstance(show.get('options',{}),dict):raise ValueError('追踪设置格式不正确')
                    if set(show.get('options',{}))-{'continuation','dual','offset','numbering','range_basis','extra_aliases','exclude_words'}:raise ValueError('列表包含不支持的设置')
                    sid=trial.save_show(show['name'],show['aliases']);trial.set_show_options(sid,show.get('options',{}))
            finally:trial.db.close()
        self.backup('before-import');added=0
        existing={str(json.loads(raw).get('_bgm_id') or name.casefold()) for _,name,raw in self.shows()}
        for show in obj['shows']:
            identity=str(show['aliases'].get('_bgm_id') or show['name'].casefold())
            if identity in existing:continue
            sid=self.save_show(show['name'],show['aliases']);self.set_show_options(sid,show.get('options',{}))
            existing.add(identity);added+=1
        return added
