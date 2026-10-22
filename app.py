import json
import os
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from core import Store, DEFAULT_OUTPUT, discover_bitcomet, search_show, lookup, complete_aliases, fill_missing_aliases, completed_hashes, discover_task_file, normalized_title, task_records, same_directory
from core import resolve_subject, continuation_eligible
from tracking import EpisodeView, SearchLimiter, save_source_health
from library_ui import LibraryTools
from details import DetailCache
from appearance import apply_style, THEMES, SoftCard, SoftButton, recolor_native, mix, release_images
from downloaders import LABELS,DEFAULTS,encode_profile,client,discover_thunder,thunder_db
from branding import APP_NAME, APP_NAME_EN, VERSION, DATA_DIRECTORY

class App:
    def __init__(self, root, data_dir=None, prefetch=True):
        self.root = root
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.store = Store(data_dir or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / DATA_DIRECTORY)
        self.messages = queue.Queue()
        self.jobs = set()
        self.cancelled = threading.Event()
        self.workers = {key: ThreadPoolExecutor(max_workers=count) for key,count in [('scan',1),('transfer',1),('monitor',1),('metadata',3),('details',2)]}
        self.next_progress=time.monotonic()+2
        self.progress_error=''
        self.editors = set()
        self.next_scan = time.monotonic() + 5
        self.items = {}
        self.removed_shows=set()
        self.detail_cache=DetailCache(self.store.root)
        self.detail_sid=None
        self.detail_data=None
        self.cover_photo=None
        self.prefetch_details=prefetch
        self.next_detail_batch=time.monotonic()+1
        self.detail_failures={}
        self.detail_checked={}
        self.library_tools=LibraryTools(self)
        self.search_limiter=SearchLimiter()
        self.compact=False
        self.build_ui(root)
        self.refresh_shows()
        root.after(200, self.poll)

    def build_ui(self,root):
        self.palette=apply_style(root,self.store.setting('theme','sage'))
        root.title(f'{APP_NAME} {APP_NAME_EN} {VERSION} · 把喜欢的故事收进日常')
        icon=Path(__file__).resolve().parent/'assets'/'aniferry.ico'
        if os.name=='nt' and icon.is_file(): root.iconbitmap(str(icon))
        root.geometry('1380x900'); root.minsize(1120,740)
        header=ttk.Frame(root,padding=(24,18)); header.pack(fill='x'); self.header=header
        identity=ttk.Frame(header); identity.pack(side='left')
        ttk.Label(identity,text=APP_NAME,style='Title.TLabel').pack(anchor='w')
        ttk.Label(identity,text='ANIFERRY  /  追番 · 下载 · 整理',style='Muted.TLabel').pack(anchor='w',pady=(3,0))
        tools=ttk.Frame(header); tools.pack(side='right')
        self.theme_choice=tk.StringVar(value=self.palette['name'])
        theme_picker=ttk.Combobox(tools,textvariable=self.theme_choice,values=[p['name'] for p in THEMES.values()],state='readonly',width=5)
        theme_picker.pack(side='left',padx=(0,8)); theme_picker.bind('<<ComboboxSelected>>',lambda _:self.change_theme())
        SoftButton(tools,text='工具',command=lambda:self.show_tools_menu()).pack(side='left',padx=4)
        SoftButton(tools,text='设置',command=self.settings).pack(side='left',padx=4)
        SoftButton(tools,text='检查所选',command=self.scan_selected,style='Accent.TButton').pack(side='left',padx=(10,0))
        pane=ttk.Panedwindow(root,orient='horizontal'); pane.pack(fill='both',expand=True,padx=24)
        left=SoftCard(pane,self.palette,padding=22); right=ttk.Frame(pane)
        pane.add(left,weight=0); pane.add(right,weight=1)
        ttk.Label(left,text='我的追番',style='Section.TLabel').pack(anchor='w')
        self.library_count=tk.StringVar()
        ttk.Label(left,textvariable=self.library_count,style='CardMuted.TLabel',wraplength=220).pack(anchor='w',pady=(5,14))
        self.shows=tk.Listbox(left,width=24,selectmode='extended',exportselection=False,borderwidth=0,highlightthickness=0,
                              bg=self.palette['card'],fg=self.palette['ink'],selectbackground=self.palette['soft'],selectforeground=self.palette['ink'],
                              activestyle='none',font=('Microsoft YaHei UI',11),relief='flat')
        self.shows.pack(fill='both',expand=True)
        self.shows.bind('<<ListboxSelect>>',lambda _:self.root.after_idle(self.open_detail))
        self.shows.bind('<Return>',lambda _:self.open_detail())
        self.shows.bind('<Control-a>',lambda _:self.select_all_shows())
        actions=ttk.Frame(left,style='Card.TFrame'); actions.pack(fill='x',pady=(14,0))
        for i,(label,command) in enumerate([('添加追番',self.add),('编辑名称',self.edit),('补全名称',self.complete_existing),('删除动画',self.delete_selected)]):
            SoftButton(actions,text=label,command=command).grid(row=i//2,column=i%2,padx=2,pady=3,sticky='ew')
        actions.columnconfigure((0,1),weight=1)
        self.welcome=SoftCard(right,self.palette,padding=24); self.welcome.pack(fill='x',pady=(0,14))
        ttk.Label(self.welcome,text='今天，想追哪一部？',style='Section.TLabel').pack(anchor='w')
        ttk.Label(self.welcome,text='选择动画，查看介绍与更新。',style='CardMuted.TLabel').pack(anchor='w',pady=(8,0))
        self.intro=SoftCard(right,self.palette,padding=22)
        self.cover_label=tk.Label(self.intro,text='ANIME\n\n封面加载中',bg=self.palette['soft'],fg=self.palette['muted'],width=17,height=12,font=('Microsoft YaHei UI',10))
        self.cover_label._cover=True
        self.cover_label.grid(row=0,column=0,rowspan=5,sticky='nw',padx=(0,18))
        self.detail_title=tk.StringVar(); self.detail_native=tk.StringVar(); self.detail_meta=tk.StringVar(); self.detail_score=tk.StringVar()
        self.detail_hint=tk.StringVar()
        self.intro_title_label=ttk.Label(self.intro,textvariable=self.detail_title,style='Section.TLabel',wraplength=560); self.intro_title_label.grid(row=0,column=1,sticky='w')
        self.intro_native_label=ttk.Label(self.intro,textvariable=self.detail_native,style='CardMuted.TLabel',wraplength=560); self.intro_native_label.grid(row=1,column=1,sticky='w',pady=(4,6))
        self.intro_meta_label=ttk.Label(self.intro,textvariable=self.detail_meta,style='CardMuted.TLabel',wraplength=560); self.intro_meta_label.grid(row=2,column=1,sticky='w')
        self.intro.bind('<Configure>',lambda event:[label.configure(wraplength=max(220,event.width-350)) for label in [self.intro_title_label,self.intro_native_label,self.intro_meta_label]],add='+')
        rating=ttk.Frame(self.intro,style='Card.TFrame'); rating.grid(row=0,column=2,rowspan=4,sticky='ne',padx=(12,0))
        score_label=tk.Label(rating,textvariable=self.detail_score,bg=self.palette['card'],fg=self.palette['accent'],font=('Segoe UI',24,'bold')); score_label._score=True; score_label.pack(anchor='e')
        self.rating_chart=tk.Canvas(rating,width=140,height=82,bg=self.palette['card'],highlightthickness=0); self.rating_chart.pack()
        ttk.Label(rating,text='评分分布 · 10 → 1',style='CardMuted.TLabel').pack(anchor='e',pady=(3,0))
        summary_frame=ttk.Frame(self.intro,style='Card.TFrame'); summary_frame.grid(row=3,column=1,sticky='nsew',pady=(10,7))
        self.detail_summary=tk.Text(summary_frame,height=4,width=50,wrap='word',bg=self.palette['card'],fg=self.palette['ink'],borderwidth=0,
                                    highlightthickness=0,font=('Microsoft YaHei UI',10),spacing3=4,state='disabled',cursor='arrow')
        self.detail_summary.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(summary_frame,command=self.detail_summary.yview); scroll.pack(side='right',fill='y'); self.detail_summary.configure(yscrollcommand=scroll.set)
        footer=ttk.Frame(self.intro,style='Card.TFrame'); footer.grid(row=4,column=1,columnspan=2,sticky='ew')
        ttk.Label(footer,textvariable=self.detail_hint,style='CardMuted.TLabel').pack(side='left')
        SoftButton(footer,text='Bangumi条目',command=self.open_bangumi).pack(side='right')
        SoftButton(footer,text='刷新介绍',command=lambda:self.open_detail(True)).pack(side='right',padx=4)
        self.intro.columnconfigure(1,weight=1)
        self.resource_section=SoftCard(right,self.palette,padding=22); self.resource_section.pack(fill='both',expand=True)
        resource_heading=ttk.Frame(self.resource_section,style='Card.TFrame'); resource_heading.pack(fill='x',pady=(0,10))
        self.resource_title=tk.StringVar(value='待下载资源')
        ttk.Label(resource_heading,textvariable=self.resource_title,style='Section.TLabel').pack(side='left')
        self.auto = tk.BooleanVar(value=self.store.setting('auto', '1') == '1')
        ttk.Checkbutton(resource_heading, text='定时检查', variable=self.auto, command=lambda: self.store.set_setting('auto', '1' if self.auto.get() else '0')).pack(side='right')
        self.downloader=tk.StringVar(value=LABELS.get(self.store.setting('downloader','bitcomet'),'BitComet'))
        selector=ttk.Combobox(resource_heading,textvariable=self.downloader,values=list(LABELS.values()),state='readonly',width=13)
        selector.pack(side='right',padx=12)
        selector.bind('<<ComboboxSelected>>',lambda _:self.choose_downloader())
        ttk.Label(resource_heading,text='下载器',style='CardMuted.TLabel').pack(side='right')
        self.first_ep=tk.StringVar(value='1'); self.last_ep=tk.StringVar()
        self.show_unknown=tk.BooleanVar(value=True)
        self.progress_label=tk.StringVar()
        table=ttk.Frame(self.resource_section,style='Card.TFrame'); table.pack(fill='both',expand=True)
        columns = ('ep', 'ani', 'sub', 'seeds', 'size', 'title')
        batch=ttk.Frame(self.resource_section,style='Card.TFrame'); batch.pack(fill='x',pady=(0,6))
        ttk.Label(batch,textvariable=self.progress_label,style='CardMuted.TLabel').pack(side='left')
        SoftButton(batch,text='追踪设置',command=self.library_tools.preferences).pack(side='right')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='extended')
        self.tree.bind('<Control-a>',lambda _:self.select_all_candidates())
        self.tree.bind('<Double-1>',lambda _:self.library_tools.candidates())
        for col, title, width in zip(columns, ['集数', '来源', '中文字幕', '做种', '大小', '资源标题'], [55,82,112,55,75,400]):
            anchor='w' if col=='title' else 'center'
            self.tree.heading(col, text=title,anchor=anchor); self.tree.column(col, width=width, stretch=col=='title',anchor=anchor)
        self.tree.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(table,command=self.tree.yview); scrollbar.grid(row=0,column=1,sticky='ns'); self.tree.configure(yscrollcommand=scrollbar.set)
        horizontal=ttk.Scrollbar(table,orient='horizontal',command=self.tree.xview); horizontal.grid(row=1,column=0,sticky='ew'); self.tree.configure(xscrollcommand=horizontal.set)
        table.rowconfigure(0,weight=1); table.columnconfigure(0,weight=1)
        self.tree.column('title',width=580,minwidth=520)
        self.tree.tag_configure('even',background=mix(self.palette['card'],self.palette['soft'],.35)); self.tree.tag_configure('odd',background=self.palette['card'])
        bar = ttk.Frame(self.resource_section,style='Card.TFrame'); bar.pack(side='bottom',fill='x', pady=(12,0))
        self.transfer_label=tk.StringVar(value='转交 '+self.downloader.get())
        SoftButton(bar, textvariable=self.transfer_label, command=self.transfer,style='Accent.TButton').pack(side='left')
        SoftButton(bar, text='忽略所选', command=self.ignore).pack(side='left', padx=8)
        SoftButton(bar, text='全部候选', command=self.library_tools.candidates).pack(side='left')
        table.pack_forget(); table.pack(fill='both',expand=True)
        self.status = tk.StringVar(value='准备就绪')
        status_area=ttk.Frame(root); status_area.pack(side='bottom',fill='x')
        status_label=ttk.Label(status_area, textvariable=self.status, padding=(24,8),style='Muted.TLabel',wraplength=1200); status_label.pack(fill='x')
        status_area.bind('<Configure>',lambda e:status_label.configure(wraplength=max(400,e.width-48)))
        self.log = tk.Text(status_area, height=1, state='disabled', wrap='word',bg=self.palette['bg'],fg=self.palette['muted'],highlightthickness=0,borderwidth=0,font=('Microsoft YaHei UI',9)); self.log._muted=True
        pane.pack_forget(); pane.pack(fill='both',expand=True,padx=24)
        root.bind('<Configure>',self.resize_layout,add='+')

    def selected_show(self):
        sel = self.shows.curselection()
        active=self.shows.index('active')
        return self.show_rows[active if active in sel else sel[0]] if sel else None

    def show_tools_menu(self):
        menu=getattr(self,'tools_menu',None)
        if menu is None:
            menu=tk.Menu(self.root,tearoff=False);self.tools_menu=menu
            for label,command in [('检查全部',self.scan),('来源状态',self.library_tools.health),('转交历史',self.history),
                                  ('清除当前待核对',self.clear_unknown),('待字幕清单',self.export),('下载目录',self.open_output),('使用说明',self.library_tools.help)]:
                menu.add_command(label=label,command=command)
        menu.configure(background=self.palette['card'],foreground=self.palette['ink'],activebackground=self.palette['soft'],activeforeground=self.palette['ink'])
        try:menu.tk_popup(self.root.winfo_pointerx(),self.root.winfo_pointery())
        finally:menu.grab_release()

    def episode_view(self,sid):
        row=self.store.db.execute('SELECT name,aliases FROM shows WHERE id=?',(sid,)).fetchone()
        if not row:return EpisodeView()
        options=self.store.show_options(sid)
        return EpisodeView.from_options(options,continuation_eligible(row[0],json.loads(row[1]),options))

    def select_all_shows(self):
        self.shows.selection_set(0,'end')
        return 'break'

    def select_all_candidates(self):
        self.tree.selection_set(self.tree.get_children())
        return 'break'

    def close(self):
        self.cancelled.set()
        self.cover_photo=None; release_images(self.root)
        self.root.destroy()

    def resize_layout(self,event):
        if event.widget!=self.root: return
        compact=event.width<1200 or event.height<820
        if compact==self.compact: return
        self.compact=compact; self.header.configure(padding=(24,10 if compact else 18))
        self.intro.configure(padding=16 if compact else 22); self.resource_section.configure(padding=16 if compact else 22)
        self.detail_summary.configure(height=1 if compact else 4)
        if self.detail_data: self.render_detail(self.detail_data)
        else: self.cover_label.configure(width=14 if compact else 17,height=8 if compact else 12)

    def change_theme(self):
        key=next((k for k,p in THEMES.items() if p['name']==self.theme_choice.get()),'sage')
        self.palette=apply_style(self.root,key); recolor_native(self.root,self.palette)
        self.tree.tag_configure('even',background=mix(self.palette['card'],self.palette['soft'],.35))
        self.tree.tag_configure('odd',background=self.palette['card'])
        if self.detail_data: self.render_detail(self.detail_data)
        self.store.set_setting('theme',key)
        self.note('已切换为“'+self.palette['name']+'”主题，下次启动自动沿用')

    def open_detail(self,force=False):
        row=self.selected_show()
        if not row: return
        sid,name,raw=row; aliases=json.loads(raw)
        self.detail_sid=sid
        self.welcome.pack_forget()
        self.intro.pack(fill='x',before=self.resource_section,pady=(0,14))
        self.refresh_candidates()
        self.detail_title.set(name); self.detail_native.set(aliases.get('日文名',''))
        self.detail_meta.set(''); self.detail_score.set('—'); self.detail_data=None
        self.cover_photo=None; self.cover_label.configure(image='',text='ANIME\n\n封面加载中',width=14 if self.compact else 17,height=8 if self.compact else 12,bg=self.palette['soft'])
        self.rating_chart.delete('all'); self.set_summary('正在读取动画介绍…')
        subject=aliases.get('_bgm_id','')
        cached=self.detail_cache.peek(subject) if str(subject).isdigit() else None
        if cached: self.render_detail(cached)
        self.detail_hint.set('正在读取资料…' if not cached else '已显示本地缓存')
        key=f'detail:{sid}'
        if key in self.jobs: return
        def work():
            bound=subject or resolve_subject(aliases.get('中文名',''),aliases.get('日文名',''))
            if not bound: raise ValueError('未匹配Bangumi条目，请先编辑名称并选择作品')
            if self.cancelled.is_set(): return None
            return self.detail_cache.get(bound,force=force)
        def done(data):
            self.accept_detail_data(sid,aliases,data)
        def failed(error):
            if self.detail_sid!=sid: return
            if not cached: self.set_summary('介绍暂未加载。可稍后刷新，或打开Bangumi条目查看。'); self.cover_label.configure(text='ANIME\n\n暂无封面')
            self.detail_hint.set(('保留本地缓存 · ' if cached else '')+'加载失败，可重试')
            self.note('动画介绍加载失败：'+error)
        self.background(work,done,key=key,on_error=failed)

    def accept_detail_data(self,sid,aliases,data):
        if not data: return
        stored=self.store.db.execute('SELECT name,aliases FROM shows WHERE id=?',(sid,)).fetchone()
        if not stored: return
        current=json.loads(stored[1])
        if current.get('_bgm_id') and str(current['_bgm_id'])!=str(data['id']): return
        if not current.get('_bgm_id'):
            if current!=aliases: return
            current['_bgm_id']=str(data['id']); self.store.save_show(stored[0],current,sid)
            self.show_rows=self.store.shows()
        selected=self.selected_show()
        if self.detail_sid==sid and selected and selected[0]==sid: self.render_detail(data)

    def refresh_library_details(self):
        if 'detail:batch' in self.jobs: return
        self.next_detail_batch=time.monotonic()+60
        pending=[]
        for sid,name,raw in self.store.shows():
            aliases=json.loads(raw); subject=aliases.get('_bgm_id')
            failure=self.detail_failures.get(sid)
            if self.detail_checked.get(sid)==raw and not failure: continue
            if failure and failure[0]==raw and time.monotonic()-failure[1]<15*60: continue
            if not str(subject).isdigit() or self.detail_cache.needs_refresh(subject,reason='daily'):
                pending.append((sid,name,raw,aliases,subject))
            else: self.detail_checked[sid]=raw
        if not pending: return
        self.note(f'正在后台补充动画资料：{len(pending)} 部；可继续浏览和下载')
        def progress(result):
            sid,name,raw,aliases,data,error,position=result
            if sid in self.removed_shows: return
            if error:
                self.detail_failures[sid]=(raw,time.monotonic())
                self.note(f'资料 {position}/{len(pending)}：{name} 更新失败，保留缓存')
            else:
                self.detail_failures.pop(sid,None); self.accept_detail_data(sid,aliases,data)
                stored=self.store.db.execute('SELECT aliases FROM shows WHERE id=?',(sid,)).fetchone()
                if stored and str(json.loads(stored[0]).get('_bgm_id'))==str(data['id']): self.detail_checked[sid]=stored[0]
                self.note(f'资料 {position}/{len(pending)}：{name} 已更新')
        def work():
            ok=failed=0
            for position,(sid,name,raw,aliases,subject) in enumerate(pending,1):
                if self.cancelled.is_set(): break
                if sid in self.removed_shows: continue
                data=error=None
                try:
                    bound=subject or resolve_subject(aliases.get('中文名',''),aliases.get('日文名',''))
                    if not bound: raise ValueError('无法唯一匹配Bangumi条目')
                    if self.cancelled.is_set(): break
                    data=self.detail_cache.get(bound,reason='daily'); ok+=1
                except Exception as e: error=str(e); failed+=1
                self.messages.put(('detail:batch-progress',progress,(sid,name,raw,aliases,data,error,position),None,None))
            return ok,failed
        def done(counts):
            self.next_detail_batch=time.monotonic()+2
            self.note(f'动画资料补充结束：成功 {counts[0]} 部，失败 {counts[1]} 部')
        self.background(work,done,key='detail:batch')

    def set_summary(self,text):
        self.detail_summary.configure(state='normal'); self.detail_summary.delete('1.0','end')
        self.detail_summary.insert('1.0',text); self.detail_summary.configure(state='disabled')

    def render_detail(self,data):
        from PIL import Image,ImageTk,ImageDraw
        self.detail_data=data
        self.detail_title.set(data.get('name_cn') or data.get('name') or '动画介绍')
        self.detail_native.set(data.get('name') or '')
        info=[str(x) for x in [data.get('platform'),data.get('date'),str(data['eps'])+' 集' if data.get('eps') else '',
                              f"{data.get('votes',0):,} 人评分",'排名 #'+str(data['rank']) if data.get('rank') else ''] if x]
        self.detail_meta.set('  ·  '.join(info))
        self.detail_score.set(f"{data['score']:.1f}" if isinstance(data.get('score'),(int,float)) and data['score'] else '—')
        self.set_summary(data.get('summary') or '该条目暂无简介。')
        self.rating_chart.delete('all')
        dist=data.get('distribution') or {}; max_count=max([int(x) for x in dist.values()]+[1])
        for i,score in enumerate(range(10,0,-1)):
            n=int(dist.get(str(score),0)); y=i*8
            self.rating_chart.create_text(16,y+4,text=str(score),fill=self.palette['muted'],font=('Segoe UI',7),anchor='e')
            self.rating_chart.create_rectangle(23,y+1,135,y+6,fill=self.palette['soft'],outline='')
            if n: self.rating_chart.create_rectangle(23,y+1,23+112*n/max_count,y+6,fill=self.palette['accent'],outline='')
        cover=data.get('cover_path')
        self.cover_photo=None
        if cover:
            try:
                with Image.open(cover) as original:
                    image=original.convert('RGBA'); image.thumbnail((104,148) if self.compact else (132,190),Image.Resampling.LANCZOS)
                    mask=Image.new('L',image.size)
                    ImageDraw.Draw(mask).rounded_rectangle((0,0,image.width-1,image.height-1),radius=12,fill=255)
                    image.putalpha(mask)
                self.cover_photo=ImageTk.PhotoImage(image,master=self.root)
            except (OSError,ValueError): pass
        if self.cover_photo: self.cover_label.configure(image=self.cover_photo,text='',width=104 if self.compact else 132,height=148 if self.compact else 190,bg=self.palette['card'])
        else: self.cover_label.configure(image='',text='ANIME\n\n暂无封面',width=14 if self.compact else 17,height=8 if self.compact else 12,bg=self.palette['soft'])
        updated=time.strftime('%m-%d %H:%M',time.localtime(data.get('cached_at',time.time())))
        self.detail_hint.set(f'Bangumi · {updated} 更新'+(' · 封面暂不可用' if data.get('cover_error') else ''))

    def open_bangumi(self):
        import webbrowser
        row=self.selected_show()
        if not row: return
        subject=(self.detail_data or {}).get('id') or json.loads(row[2]).get('_bgm_id')
        if str(subject).isdigit(): webbrowser.open('https://bgm.tv/subject/'+str(subject))

    def delete_selected(self):
        rows=[self.show_rows[i] for i in self.shows.curselection()]
        if not rows: return
        names='、'.join(r[1] for r in rows[:6])+(' 等' if len(rows)>6 else '')
        if not messagebox.askyesno('删除追番',f'删除所选 {len(rows)} 部动画的追踪和候选记录？\n{names}\n\n保留视频、种子和下载器任务；删除前自动备份。'): return
        ids={row[0] for row in rows}
        self.store.delete_shows(ids); self.removed_shows.update(ids)
        for win in list(self.editors):
            if getattr(win,'show_id',None) in ids: win.destroy()
        self.refresh_shows(); self.note(f'已删除 {len(rows)} 部动画的追踪记录，已下载文件和下载器任务保留')

    def refresh_shows(self):
        self.next_detail_batch=time.monotonic()+2
        old = self.shows.curselection()
        previous=getattr(self,'show_rows',[])
        selected_ids={previous[i][0] for i in old if i<len(previous)}
        self.show_rows = self.store.shows()
        self.library_count.set(f'{len(self.show_rows)} 部追踪中')
        self.shows.delete(0, 'end')
        for _, name, aliases in self.show_rows:
            self.shows.insert('end', name)
        if self.show_rows:
            for index,(sid,_,_) in enumerate(self.show_rows):
                if sid in selected_ids: self.shows.selection_set(index)
            if not self.shows.curselection(): self.shows.selection_set(min(old[0] if old else 0,len(self.show_rows)-1))
        self.refresh_candidates()

    def refresh_candidates(self):
        previous_selection=set(self.tree.selection());position=self.tree.yview()[0] if self.tree.get_children() else 0
        previous_sid=getattr(self,'candidate_sid',None)
        self.tree.delete(*self.tree.get_children()); self.items = {}
        row = self.selected_show()
        self.candidate_sid=row[0] if row else None
        if not row or self.detail_sid!=row[0]:
            self.detail_sid=None; self.detail_data=None; self.cover_photo=None
            self.intro.pack_forget()
            if not self.welcome.winfo_manager(): self.welcome.pack(fill='x',before=self.resource_section,pady=(0,14))
        if row:
            first,last,completed=self.store.episode_rule(row[0])
            view=self.episode_view(row[0]);shown_first=view.display(first);shown_last=view.display(last)
            self.first_ep.set(str(shown_first)); self.last_ep.set(str(shown_last) if last else '')
            self.progress_label.set(f'已完成 {view.label(completed) if completed else "暂无"} · '+
                                    ('本季 ' if view.enabled and view.range_basis=='local' else '全篇 ' if view.enabled else '追踪 ')+f'{shown_first}～{shown_last or "不限"}')
            self.tree.heading('ep',text='本季 / 全篇' if view.enabled else '集数')
            self.tree.column('ep',width=125 if view.enabled else 55,minwidth=125 if view.enabled else 55)
            for item in self.store.candidates(row[0]):
                if item['episode'].startswith('待核对') and not self.show_unknown.get(): continue
                hash_ = item['hash']; self.items[hash_] = item
                ep = view.label(item['episode'])
                self.tree.insert('', 'end', iid=hash_,tags=('even' if len(self.items)%2==0 else 'odd',), values=(ep, 'ANI' if item['ani'] else item.get('source','Nyaa'), item['subtitle'], item['seeds'] if item.get('seed_known',True) else '未知', item['size'], item['title']))
        else: self.progress_label.set('')
        self.resource_title.set(f'待下载资源  ·  {len(self.items)} 个候选')
        if previous_sid==self.candidate_sid:
            self.tree.selection_set([hash_ for hash_ in previous_selection if hash_ in self.items]);self.tree.yview_moveto(position)
        self.library_tools.refresh_candidates()

    def apply_range(self, clear=False):
        row=self.selected_show()
        if not row: return
        try:
            view=self.episode_view(row[0])
            self.store.set_episode_range(row[0],1 if clear else view.storage(int(self.first_ep.get())),0 if clear or not self.last_ep.get().strip() else view.storage(int(self.last_ep.get())))
            self.refresh_candidates(); self.next_scan=time.monotonic()+2
            self.note('集数范围已保存；下载完成进度形成的下限仍生效')
        except ValueError as e: messagebox.showerror('集数范围错误',str(e))

    def monitor_progress(self):
        if 'monitor' in self.jobs: return
        self.next_progress=time.monotonic()+15
        dbroot=self.store.root
        def work():
            store=Store(dbroot)
            try: return store.poll_downloaders()
            finally: store.db.close()
        def done(result):
            accepted,changed,errors=result
            if accepted: self.refresh_candidates(); self.note(f'下载器任务记录已确认接收 {accepted} 个任务')
            if changed:
                self.refresh_candidates(); self.note(f'确认 {changed} 个任务下载完成，已自动弃置旧集候选')
            error='；'.join(errors)
            if error and error!=self.progress_error: self.note('下载监测：'+error)
            self.progress_error=error
        def failed(error):
            if error!=self.progress_error:
                self.note('下载完成监测暂不可用：'+error); self.progress_error=error
        self.background(work,done,key='monitor',on_error=failed)

    def note(self, text, detail=False):
        if not detail:self.status.set(text if len(text)<=110 else text[:107]+'…')
        self.log.configure(state='normal'); self.log.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
        if int(self.log.index('end-1c').split('.')[0])>1500:self.log.delete('1.0','end-1000l')
        self.log.see('end'); self.log.configure(state='disabled')

    @property
    def busy(self):
        return 'scan' in self.jobs or 'transfer' in self.jobs

    def background(self, work, done, key='scan', on_error=None):
        if key in self.jobs:
            self.note('该任务正在处理'); return False
        self.jobs.add(key)
        def run():
            try: self.messages.put((key, done, work(), None, on_error))
            except Exception as e: self.messages.put((key, done, None, str(e), on_error))
        lane = 'details' if key.startswith('detail:') else key if key in self.workers else 'metadata'
        self.workers[lane].submit(run)
        return True

    def poll(self):
        try:
            while True:
                key, done, value, error, on_error = self.messages.get_nowait(); self.jobs.discard(key)
                if error:
                    if on_error: on_error(error)
                    else: self.note('失败：' + error)
                else:
                    try: done(value)
                    except Exception as e: self.note('失败：' + str(e))
        except queue.Empty: pass
        if self.prefetch_details and time.monotonic()>=self.next_detail_batch: self.refresh_library_details()
        if time.monotonic() >= self.next_progress: self.monitor_progress()
        if self.auto.get() and 'scan' not in self.jobs and not self.editors and time.monotonic() >= self.next_scan:
            self.scan()
        self.root.after(200, self.poll)

    def scan_selected(self):
        ids={self.show_rows[i][0] for i in self.shows.curselection()}
        if not ids:self.note('请先选择动画');return
        self.scan(ids)

    def scan(self,ids=None):
        if 'scan' in self.jobs:self.note('正在检查更新');return
        self.next_scan = time.monotonic() + int(self.store.setting('interval', '30')) * 60
        rows = self.store.shows()
        if ids is not None:rows=[r for r in rows if r[0] in ids]
        rules={sid:self.store.episode_rule(sid) for sid,_,_ in rows}
        search_aliases={sid:self.store.search_aliases(sid) for sid,_,_ in rows}
        preferences={sid:self.store.show_options(sid) for sid,_,_ in rows}
        if not rows: return
        self.note('正在并行检查 Nyaa、蜜柑、動漫花園及作品ID补充源…')
        def progress(result):
            sid,name,items,errors,reports=result
            if sid in self.removed_shows: return
            self.store.ingest(sid,items); self.refresh_candidates()
            self.note(f'{name} · 已找到 {len(items)} 个资源')
        def work():
            results=[]
            def check(row):
                sid,name,aliases=row
                if sid in self.removed_shows or self.cancelled.is_set(): return None
                first,last,_=rules[sid]
                reports=[]
                def partial(items):
                    result=(sid,name,items,[],[])
                    self.messages.put(('scan-progress',progress,result,None,None))
                items,errors=search_show(search_aliases[sid], self.cancelled,first,last,diagnostics=reports,
                                         on_progress=partial,cache_dir=self.store.root/'search-cache',request_control=self.search_limiter,
                                         exclude_words=preferences[sid].get('exclude_words',[]))
                result=(sid,name,items,errors,reports)
                self.messages.put(('scan-progress',progress,result,None,None))
                return result
            # Cap total traffic at two shows x four sources. UI/SQLite writes stay on the UI thread.
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(check,row) for row in rows]
                for future in as_completed(futures):
                    result=future.result()
                    if result is not None: results.append(result)
            return results
        def done(results):
            count = 0
            failures = 0
            report_data=[]
            for sid, name, items, errors, reports in results:
                if sid in self.removed_shows: continue
                count += len(items)
                report_data.append({'show_id':sid,'name':name,'queries':reports,'count':len(items),'ani':sum(x['ani'] for x in items),'first':rules[sid][0],'last':rules[sid][1],'checked_at':time.time()})
                failures += len(errors)
                if errors: self.note(name + ' 部分搜索失败：' + '；'.join(errors),detail=True)
            save_source_health(self.store.root/'source-health.json',report_data)
            path=self.store.root/'last-search.json'
            try:previous=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):previous=[]
            scanned={r['show_id'] for r in report_data}
            current_ids={r[0] for r in self.store.shows()}
            report_data=[r for r in previous if r.get('show_id') not in scanned and r.get('show_id') in current_ids]+report_data
            path.write_text(json.dumps(report_data,ensure_ascii=False,indent=2),encoding='utf-8')
            self.refresh_candidates(); self.note(f'已检查 {len(results)} 部动画 · 找到 {count} 个资源'+(' · 部分来源暂不可用' if failures else ''))
        self.background(work, done)

    def search_diagnostics(self,sid=None):
        path=self.store.root/'last-search.json'
        if not path.exists(): self.note('请先执行一次检查更新，完成后查看诊断'); return
        win=tk.Toplevel(self.root); win.title('最近一次搜索诊断'); win.geometry('950x550')
        text=tk.Text(win,wrap='word'); text.pack(fill='both',expand=True,padx=10,pady=10)
        for report in json.loads(path.read_text(encoding='utf-8')):
            if sid is not None and report.get('show_id')!=sid:continue
            text.insert('end',f"\n{report['name']}：去重后 {report['count']} 个，范围内ANI {report['ani']} 个；范围 {report.get('first','?')}～{report.get('last') or '不限'}\n")
            for q in report['queries']:
                state='查询失败：'+q['error'] if q['error'] else ('分集范围已核对' if q.get('episode_span') else '成功但零结果' if not q['returned'] else '全部被作品/集数规则排除' if not q['kept'] else '查询成功')
                text.insert('end',f"  {q['source']} | {q['query']} | {state} | 返回 {q['returned']} / 片名不符 {q.get('identity_rejected',0)} / 集数排除 {q.get('range_rejected',0)} / 偏好排除 {q.get('preference_rejected',0)} / 范围内 {q['kept']} / ANI返回 {q.get('ani_returned',q['ani'])} / ANI范围内 {q['ani']} / 重试 {q.get('retries',0)} / 限速等待 {q.get('wait_seconds',0)}s / 退避 {q.get('cooldown_seconds',0)}s"+(' / '+q['warning'] if q.get('warning') else '')+'\n')
        text.configure(state='disabled')

    def add(self): self.editor()
    def edit(self):
        row = self.selected_show()
        if row: self.editor(row)

    def editor(self, row=None):
        win = tk.Toplevel(self.root); win.title('追番名称与四重搜索'); win.geometry('740x430')
        win.show_id=row[0] if row else None
        self.editors.add(win)
        generation = [0]
        saved_requests = {}
        def destroyed(event):
            if event.widget is win: self.editors.discard(win)
        win.bind('<Destroy>', destroyed)
        variables = {}
        values = json.loads(row[2]) if row else {}
        selected_subject=[values.get('_bgm_id','')]
        selected_native=[values.get('日文名','')]
        for i, label in enumerate(['显示名称','中文名','日文名','罗马音','英文名']):
            ttk.Label(win, text=label).grid(row=i,column=0,padx=12,pady=8,sticky='w')
            var = tk.StringVar(value=row[1] if label=='显示名称' and row else values.get(label,'')); variables[label] = var
            ttk.Entry(win,textvariable=var,width=60).grid(row=i,column=1,padx=12,sticky='ew')
        hint = tk.StringVar(value='保存并继续添加可连续录入，后台搜索不阻塞名称补全。')
        ttk.Label(win,textvariable=hint).grid(row=5,column=0,columnspan=2,pady=8)
        def resolve():
            name = variables['显示名称'].get() or variables['中文名'].get()
            if not name: return
            generation[0] += 1
            request = generation[0]
            snapshot = {k:v.get() for k,v in variables.items()}
            lookup_button.configure(state='disabled')
            hint.set('正在查找作品…')
            def current():
                return win.winfo_exists() and request == generation[0]
            def failed(error):
                if current():
                    lookup_button.configure(state='normal'); hint.set('补全失败，可重试或手动填写'); self.note(error)
            def choose(entries):
                if not current(): return
                lookup_button.configure(state='normal')
                if not entries: hint.set('未找到元数据，可修改名称再查询'); return
                picker = tk.Toplevel(win); picker.title('选择作品，核对标题')
                box = tk.Listbox(picker,width=75,height=10); box.pack()
                for e in entries: box.insert('end',f"{e['中文名']} / {e['日文名']} [Bangumi {e['id']}]")
                def accept():
                    if not box.curselection() or not current(): return
                    e = entries[box.curselection()[0]]
                    selected_subject[0]=str(e['id'])
                    selected_native[0]=e['日文名']
                    for k in ['中文名','日文名']:
                        if variables[k].get() == snapshot[k]: variables[k].set(e[k])
                    picker.destroy()
                    native = variables['日文名'].get()
                    before = {k:variables[k].get() for k in ['罗马音','英文名']}
                    hint.set('正在补全罗马音及英文名…'); lookup_button.configure(state='disabled')
                    def fill(v):
                        # Saving while completion is in flight binds its result to the saved show.
                        # It must never write into the next show's cleared editor.
                        if not current():
                            sid=saved_requests.get(request)
                            stored=self.store.db.execute('SELECT name,aliases FROM shows WHERE id=?',(sid,)).fetchone() if sid else None
                            if stored:
                                aliases=json.loads(stored[1])
                                if aliases.get('日文名')==native:
                                    for k,value in zip(['罗马音','英文名'],v):
                                        if value and not aliases.get(k): aliases[k]=value
                                    self.store.save_show(stored[0],aliases,sid)
                                    self.refresh_shows()
                                    self.note(stored[0]+' 的后台名称补全已保存')
                            return
                        lookup_button.configure(state='normal')
                        if variables['日文名'].get() != native: return
                        for k,value in zip(['罗马音','英文名'],v):
                            if value and variables[k].get() == before[k] and not before[k]: variables[k].set(value)
                        hint.set('名称补全完成' if all(v) else '已补全部分名称；来源未提供的名称保留空白')
                    self.background(lambda: complete_aliases(native), fill, key=f'metadata:{id(win)}:{request}:aliases', on_error=failed)
                SoftButton(picker,text='选用并补全罗马音/英文',command=accept).pack(pady=8)
            self.background(lambda: lookup(name), choose, key=f'metadata:{id(win)}:{request}:lookup', on_error=failed)
        def save(keep_open=False):
            try:
                name = variables['显示名称'].get().strip()
                aliases = {k: v.get().strip() for k,v in variables.items() if k!='显示名称'}
                if selected_subject[0] and normalized_title(aliases.get('日文名'))==normalized_title(selected_native[0]): aliases['_bgm_id']=selected_subject[0]
                if not any(aliases.values()): aliases['中文名'] = name
                sid=self.store.save_show(name,aliases,row[0] if row else None)
                saved_requests[generation[0]]=sid
                generation[0] += 1
                self.refresh_shows(); self.next_scan = time.monotonic()+20
                if keep_open:
                    selected_subject[0]=''
                    selected_native[0]=''
                    for var in variables.values(): var.set('')
                    lookup_button.configure(state='normal'); hint.set('已保存，可继续录入下一部')
                    for child in win.winfo_children():
                        if isinstance(child,tk.Toplevel): child.destroy()
                    name_entry.focus_set()
                else: win.destroy()
            except Exception as e: messagebox.showerror('无法保存',str(e),parent=win)
        name_entry = [w for w in win.winfo_children() if isinstance(w,ttk.Entry)][0]
        lookup_button = SoftButton(win,text='查找并补全名称',command=resolve)
        lookup_button.grid(row=6,column=0,padx=12,pady=12)
        actions=ttk.Frame(win); actions.grid(row=6,column=1,pady=12)
        SoftButton(actions,text='保存追踪',command=save).pack(side='left',padx=5)
        if not row: SoftButton(actions,text='保存并继续添加',command=lambda:save(True)).pack(side='left',padx=5)
        name_entry.focus_set()

    def complete_existing(self):
        rows = [(sid,name,json.loads(raw)) for sid,name,raw in self.store.shows()
                if not all(json.loads(raw).get(k) for k in ['中文名','日文名','罗马音','英文名'])]
        if not rows: self.note('已有名称完整'); return
        self.note('正在后台补全已有名称，可继续添加追番…')
        def work():
            results=[]
            for sid,name,aliases in rows:
                if self.cancelled.is_set(): break
                try: results.append((sid,aliases,fill_missing_aliases(name,aliases),None))
                except Exception as e: results.append((sid,aliases,None,str(e)))
            return results
        def done(results):
            changed=0
            for sid,before,after,error in results:
                if error: self.note(f'名称补全失败（记录 {sid}）：{error}'); continue
                row=self.store.db.execute('SELECT name,aliases FROM shows WHERE id=?',(sid,)).fetchone()
                if not row: continue
                aliases=json.loads(row[1])
                for key,value in after.items():
                    if value and not aliases.get(key) and aliases.get(key)==before.get(key): aliases[key]=value
                if aliases != json.loads(row[1]): self.store.save_show(row[0],aliases,sid); changed+=1
            self.refresh_shows(); self.next_scan=time.monotonic()+20
            self.note(f'已有名称补全结束：更新 {changed} 部。来源缺失或未匹配的名称保留空白。')
        self.background(work,done,key='metadata:existing')

    def choose_downloader(self):
        backend=next(k for k,v in LABELS.items() if v==self.downloader.get())
        self.store.set_setting('downloader',backend)
        self.transfer_label.set('转交 '+self.downloader.get())
        self.note('已选择 '+self.downloader.get()+'；连接信息可在设置中填写，历史任务继续由原下载器监测')

    def settings(self):
        win = tk.Toplevel(self.root); win.title('设置 · 下载器与追踪'); win.geometry('780x560'); win.minsize(700,530)
        tabs=ttk.Notebook(win); tabs.pack(fill='both',expand=True,padx=16,pady=16)
        general=ttk.Frame(tabs,padding=16); tabs.add(general,text='常规')
        fields={}; profiles={}
        for i,(key,label,default) in enumerate([('output','下载目录',DEFAULT_OUTPUT),('interval','检查间隔（分钟，至少5）','30')]):
            ttk.Label(general,text=label).grid(row=i,column=0,padx=8,pady=12,sticky='w')
            fields[key]=tk.StringVar(value=self.store.setting(key,default))
            ttk.Entry(general,textvariable=fields[key],width=54).grid(row=i,column=1,sticky='ew')
        SoftButton(general,text='选择目录',command=lambda:fields['output'].set(filedialog.askdirectory(parent=win) or fields['output'].get())).grid(row=0,column=2,padx=8)
        selected=tk.StringVar(value=self.downloader.get())
        ttk.Label(general,text='默认下载器').grid(row=2,column=0,padx=8,pady=12,sticky='w')
        ttk.Combobox(general,textvariable=selected,values=list(LABELS.values()),state='readonly').grid(row=2,column=1,sticky='w')
        ttk.Label(general,text='每部动画页面也可随时切换下载器。\n历史任务保留原来的连接，切换默认项不会迁移正在下载的任务。\n密码与密钥使用当前 Windows 用户加密保存。',wraplength=620,style='Muted.TLabel').grid(row=3,column=0,columnspan=3,sticky='w',pady=24)
        schemas={
            'bitcomet':[('exe','BitComet 程序'),('task_xml','Downloads.xml')],
            'qbittorrent':[('url','Web UI 地址'),('username','用户名'),('password','密码')],
            'aria2':[('url','RPC 地址'),('secret','RPC 密钥')],
            'motrix':[('url','RPC 地址'),('secret','RPC 密钥')],
            'thunder':[('exe','迅雷程序'),('task_db','TaskDb.dat 任务记录')]}
        hints={
            'bitcomet':'选择 BitComet.exe 和其使用的 Downloads.xml；接收与完成状态通过任务记录核对。',
            'qbittorrent':'先在 qBittorrent 设置 → Web UI 启用远程控制，填写地址、用户名和密码。默认地址 http://127.0.0.1:8080。',
            'aria2':'先运行已启用 RPC 的 aria2。默认 http://127.0.0.1:6800/jsonrpc；密钥与 rpc-secret 一致。建议启用会话保存，便于重启后继续追踪。',
            'motrix':'支持使用 aria2 核心的 Motrix 1.x。在 Motrix 进阶设置核对 RPC 端口和授权密钥。默认 http://127.0.0.1:16800/jsonrpc。',
            'thunder':'通过迅雷的种子打开方式新建任务。请在迅雷窗口将目录设为“常规”中的下载目录并确认；本应用核对种子哈希与目录后才标记已转交。任务记录一般为迅雷安装目录旁 Profiles/TaskDb.dat。'}
        result=tk.StringVar(value='选择下载器标签页填写连接，然后测试连接。')
        for backend,label in LABELS.items():
            page=ttk.Frame(tabs,padding=16); tabs.add(page,text=label); page.columnconfigure(1,weight=1)
            try: saved=self.store.downloader_profile(backend)
            except ValueError: saved={}
            profiles[backend]={}
            for i,(key,caption) in enumerate(schemas[backend]):
                ttk.Label(page,text=caption).grid(row=i,column=0,sticky='w',padx=6,pady=10)
                var=tk.StringVar(value=saved.get(key,'')); profiles[backend][key]=var
                ttk.Entry(page,textvariable=var,width=57,show='●' if key in ('password','secret') else '').grid(row=i,column=1,sticky='ew')
                if key in ('exe','task_xml','task_db'):
                    def browse(v=var,k=key,b=backend):
                        path=filedialog.askopenfilename(parent=win,filetypes=[('程序','*.exe')] if k=='exe' else [('任务记录','*.xml *.dat'),('所有文件','*.*')])
                        if not path: return
                        v.set(path)
                        if k=='exe':
                            taskkey='task_xml' if b=='bitcomet' else 'task_db'
                            profiles[b][taskkey].set(discover_task_file(path) if b=='bitcomet' else thunder_db(path))
                    SoftButton(page,text='选择',command=browse).grid(row=i,column=2,padx=6)
            ttk.Label(page,text=hints[backend],wraplength=650,style='Muted.TLabel').grid(row=4,column=0,columnspan=3,sticky='w',pady=20)
            def test(b=backend):
                profile={k:v.get().strip() if k not in ('password','secret') else v.get() for k,v in profiles[b].items()}
                result.set('正在测试 '+LABELS[b]+'…')
                def work():
                    if b=='bitcomet':
                        if not Path(profile.get('exe','')).is_file(): raise ValueError('请选择 BitComet.exe')
                        task_records(profile.get('task_xml') or discover_task_file(profile.get('exe','')))
                        return 'BitComet 程序与任务记录可读取'
                    return client(b,profile).test()
                self.background(work,lambda text:result.set('连接成功：'+text) if win.winfo_exists() else None,
                    key='connection:'+b,on_error=lambda error:result.set('连接失败：'+error) if win.winfo_exists() else None)
            SoftButton(page,text='测试连接（不创建下载任务）',command=test).grid(row=5,column=0,columnspan=3,sticky='w')
        ttk.Label(win,textvariable=result,wraplength=720,style='Muted.TLabel').pack(fill='x',padx=24)
        def save():
            try:
                if int(fields['interval'].get())<5: raise ValueError('间隔至少5分钟')
                if not Path(fields['output'].get()).is_absolute(): raise ValueError('下载目录必须是绝对路径')
                encoded={b:encode_profile({k:v.get().strip() if k not in ('password','secret') else v.get() for k,v in p.items()}) for b,p in profiles.items()}
                for key,var in fields.items(): self.store.set_setting(key,var.get())
                for backend,raw in encoded.items(): self.store.set_setting('downloader_profile_'+backend,raw)
                self.store.set_setting('bitcomet',profiles['bitcomet']['exe'].get()); self.store.set_setting('task_xml',profiles['bitcomet']['task_xml'].get())
                self.downloader.set(selected.get()); self.choose_downloader()
                self.next_scan=time.monotonic()+int(fields['interval'].get())*60
                win.destroy()
            except Exception as e: messagebox.showerror('设置错误',str(e),parent=win)
        SoftButton(win,text='保存设置',command=save,style='Accent.TButton').pack(pady=16)

    def selected_item(self):
        sel=self.tree.selection()
        if len(sel)>1:
            self.note('批量选择可直接忽略；转交或打开页面时请只选一个资源')
            return None
        return self.items.get(sel[0]) if sel else None

    def transfer(self,sid=None,item=None):
        row=self.store.db.execute('SELECT id,name,aliases FROM shows WHERE id=?',(sid,)).fetchone() if sid is not None else self.selected_show()
        item=item if item is not None else self.selected_item()
        if not item or not row or 'transfer' in self.jobs: return
        # The explicit button is the user's confirmation. Network and process launch run off the UI thread.
        dbroot=self.store.root
        label=self.downloader.get()
        backend=next(k for k,v in LABELS.items() if v==label)
        self.note('正在下载种子并转交 '+label+'…')
        def work():
            store=Store(dbroot)
            try: return store.transfer(row[0],item,backend=backend)
            finally: store.db.close()
        def done(result):
            self.refresh_candidates(); self.next_progress=time.monotonic()
            state=result['state']
            if state=='already_completed': self.note(label+' 已有此资源的完成记录。位置：'+result['location'])
            elif state=='already_received': self.note(label+' 已有同一资源任务，请查看其运行/暂停状态。位置：'+result['location'])
            elif label=='迅雷': self.note('种子已交给迅雷，请在新建任务窗口确认目录为 '+result['location']+'；核对记录后标记接收。种子：'+result['torrent'])
            else: self.note('已向 '+label+' 提交种子，等待任务记录核对接收。种子：'+result['torrent'])
        self.background(work,done,key='transfer')

    def ignore(self):
        row=self.selected_show(); selected=self.tree.selection()
        if not row or not selected: return
        if 'transfer' in self.jobs: self.note('正在转交任务，请稍后再忽略候选'); return
        count=self.store.ignore_releases(row[0],selected)
        self.refresh_candidates(); self.note(f'已忽略 {count} 个候选资源')

    def clear_unknown(self):
        row=self.selected_show()
        if not row: return
        if 'transfer' in self.jobs: self.note('正在转交任务，请稍后再清理候选'); return
        count=self.store.clear_unknown(row[0])
        self.refresh_candidates(); self.note(f'已清除“{row[1]}”的 {count} 个待核对资源；以后搜索不再显示这些资源')

    def open_page(self,item=None):
        import webbrowser
        item=item if item is not None else self.selected_item()
        if item and any(item['page'].startswith(prefix) for prefix in ['https://nyaa.si/view/','https://animes.garden/detail/','https://mikanani.me/Home/Episode/','https://share.dmhy.org/topics/view/']): webbrowser.open(item['page'])

    def history(self):
        win=tk.Toplevel(self.root); win.title('转交历史与重置'); win.geometry('950x420')
        box=tk.Listbox(win,width=100); box.pack(fill='both',expand=True,padx=10,pady=10)
        rows=self.store.db.execute("SELECT show_id,hash,shows.name,title,output,status FROM releases JOIN shows ON shows.id=show_id WHERE status IN ('待接收','已转交','已完成') ORDER BY show_id").fetchall()
        for row in rows: box.insert('end',f'{row[5]} | {LABELS[self.store.handoff_backend(*row[:2])]} | {row[2]} | {row[3]} | {row[4]}')
        def reset():
            if not box.curselection() or self.busy: return
            row=rows[box.curselection()[0]]
            if row[5]=='已完成':
                self.note('已完成记录保留作为追踪进度，不可重置为未接收'); return
            if not messagebox.askyesno('重置记录','仅当下载器未接收此任务时重置。重置后可以再次转交，可能重复下载。',parent=win): return
            self.store.db.execute("UPDATE releases SET status='待确认',output='' WHERE show_id=? AND hash=?",row[:2]); self.store.db.commit()
            self.store.db.execute('DELETE FROM handoffs WHERE show_id=? AND hash=?',row[:2]); self.store.db.commit()
            self.store.export_queue(); self.refresh_candidates(); win.destroy()
        SoftButton(win,text='重置选中的转交记录',command=reset).pack(pady=8)
        def manual_complete():
            if not box.curselection(): return
            row=rows[box.curselection()[0]]
            if row[5]=='待接收':
                self.note('任务尚未确认接收，请先在对应下载器核对；未接收可重置后重试'); return
            self.store.mark_completed(row[0],row[1]); self.store.export_queue(); self.refresh_candidates(); win.destroy()
            self.note('已手动确认下载完成，旧集候选已弃置')
        SoftButton(win,text='手动确认选中任务下载完成',command=manual_complete).pack(pady=8)
        def open_seeds():
            folder=self.store.root/'torrents'
            if folder.exists(): os.startfile(str(folder))
        SoftButton(win,text='打开已下载的种子目录',command=open_seeds).pack(pady=8)

    def export(self):
        path=self.store.export_queue(); os.startfile(str(path))

    def open_output(self):
        path=Path(self.store.setting('output',DEFAULT_OUTPUT))
        if path.exists(): os.startfile(str(path))
        else: self.note('下载目录尚不存在，首次转交时创建')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--data-dir'); parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--tls-check',action='store_true');parser.add_argument('--network-check',metavar='BANGUMI_ID');args=parser.parse_args()
    if args.tls_check:
        import ssl,sys
        if not args.data_dir:parser.error('--tls-check requires --data-dir for its output')
        report={}
        try:
            context=ssl.create_default_context()
            report=dict(ok=context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED,
                        hostname_check=context.check_hostname,verify_mode=int(context.verify_mode),openssl=ssl.OPENSSL_VERSION)
        except Exception as e:report=dict(ok=False,error_type=type(e).__name__,error=str(e))
        destination=Path(args.data_dir);destination.mkdir(parents=True,exist_ok=True)
        (destination/'tls-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        sys.exit(0 if report['ok'] else 1)
    if args.network_check:
        import sys
        from core import search_fetch,parse_rss,parse_external_rss
        if not args.data_dir or not args.network_check.isdigit():parser.error('--network-check requires a numeric subject and --data-dir')
        destination=Path(args.data_dir);destination.mkdir(parents=True,exist_ok=True)
        def detail():
            data=DetailCache(destination).get(args.network_check,force=True)
            return dict(cover_loaded=bool(data.get('cover_path')),score=data.get('score'))
        checks=[('Bangumi',detail),
                ('Nyaa',lambda:dict(resources=len(parse_rss(search_fetch('https://nyaa.si/?page=rss&c=1_0'))))),
                ('Mikan',lambda:dict(resources=len(parse_external_rss(search_fetch('https://mikanani.me/RSS/Search?searchstr=ANi'),'Mikan')))),
                ('DMHY',lambda:dict(resources=len(parse_external_rss(search_fetch('https://share.dmhy.org/topics/rss/rss.xml?keyword=ANi'),'DMHY')))),
                ('AnimeGarden',lambda:dict(resources=len(json.loads(search_fetch('https://api.animes.garden/resources?pageSize=1'))['resources'])))]
        def check(pair):
            source,operation=pair
            try:return dict(source=source,ok=True,**operation())
            except Exception as e:return dict(source=source,ok=False,error_type=type(e).__name__,error=str(e))
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(check,checks))
        (destination/'network-check.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        sys.exit(0 if all(r['ok'] for r in results) else 1)
    root=tk.Tk(); app=App(root,args.data_dir,prefetch=not args.smoke_test)
    if args.smoke_test:
        app.auto.set(False); app.next_progress=time.monotonic()+3600
        # Exercise cached image rendering in frozen builds without network requests.
        for index,(_,_,raw) in enumerate(app.show_rows):
            subject=json.loads(raw).get('_bgm_id')
            cached=app.detail_cache.peek(subject) if str(subject).isdigit() else None
            if cached and time.time()-cached['cached_at']<app.detail_cache.TTL:
                app.shows.selection_clear(0,'end'); app.shows.selection_set(index); app.open_detail(); break
        def finish_smoke():
            (app.store.root/'smoke-result.json').write_text(json.dumps({'detail_loaded':bool(app.detail_data),'cover_loaded':bool(app.cover_photo),'jobs_remaining':list(app.jobs)}),encoding='utf-8')
            app.close()
        root.after(1000,finish_smoke)
    try: root.mainloop()
    finally:
        app.cancelled.set()
        for worker in app.workers.values(): worker.shutdown(wait=False,cancel_futures=True)
