"""Secondary library screens. Keep the daily browsing surface small."""
import json
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from appearance import SoftButton
from core import continuation_eligible, cached_cour_span, cour_index
from tracking import EpisodeView, source_health


class LibraryTools:
    def __init__(self,app):
        self.app=app; self.windows={}; self.candidate_refresh={}

    def window(self,key,title,size='960x620'):
        previous=self.windows.get(key)
        if previous and previous.winfo_exists(): previous.deiconify();previous.lift();return None
        win=tk.Toplevel(self.app.root);win.title(title);win.geometry(size);win.minsize(760,540)
        win.configure(bg=self.app.palette['bg']);win.transient(self.app.root)
        self.windows[key]=win
        def destroyed(event):
            if event.widget is win:
                self.windows.pop(key,None);self.candidate_refresh.pop(key,None)
        win.bind('<Destroy>',destroyed,add='+')
        return win

    def row(self,sid=None):
        if sid is None:return self.app.selected_show()
        return self.app.store.db.execute('SELECT id,name,aliases FROM shows WHERE id=?',(sid,)).fetchone()

    def refresh_candidates(self):
        for refresh in list(self.candidate_refresh.values()): refresh()

    def candidates(self):
        row=self.row()
        if not row:return
        sid,name,_=row;key=('candidates',sid)
        win=self.window(key,name+' · 全部候选','1080x680')
        if win is None:return
        win.show_id=sid
        body=ttk.Frame(win,padding=18);body.pack(fill='both',expand=True)
        heading=ttk.Frame(body);heading.pack(fill='x',pady=(0,12))
        count=tk.StringVar();ttk.Label(heading,textvariable=count,font=('Microsoft YaHei UI',12,'bold')).pack(side='left')
        unknown=tk.BooleanVar(value=True)
        table=ttk.Frame(body);table.pack(fill='both',expand=True)
        cols=('ep','source','subtitle','seeds','title')
        tree=ttk.Treeview(table,columns=cols,show='headings',selectmode='extended')
        for col,label,width in zip(cols,['集数','来源','中文字幕','做种','资源标题'],[130,110,120,70,550]):
            tree.heading(col,text=label,anchor='w' if col=='title' else 'center')
            tree.column(col,width=width,minwidth=width if col!='title' else 300,stretch=col=='title',anchor='w' if col=='title' else 'center')
        tree.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(table,command=tree.yview);scroll.grid(row=0,column=1,sticky='ns');tree.configure(yscrollcommand=scroll.set)
        horizontal=ttk.Scrollbar(table,orient='horizontal',command=tree.xview);horizontal.grid(row=1,column=0,sticky='ew');tree.configure(xscrollcommand=horizontal.set)
        table.rowconfigure(0,weight=1);table.columnconfigure(0,weight=1)
        details=tk.Text(body,height=5,wrap='word',relief='flat',font=('Microsoft YaHei UI',10),state='disabled')
        details.pack(fill='x',pady=(12,6))
        items={}
        def explain(_=None):
            chosen=tree.selection();item=items.get(chosen[0]) if len(chosen)==1 else None
            if not item: value='选择一项查看匹配依据' if not chosen else f'已选 {len(chosen)} 项'
            else:
                view=self.app.episode_view(sid)
                identity=item.get('identity_basis') or ('Bangumi作品条目匹配' if item.get('subject_id') else '已填写的作品名称匹配')
                reason='ANI优先' if item['ani'] else ('按做种人数排序' if item.get('seed_known',True) else '来源未提供做种人数')
                value=f"{item['title']}\n集数：{view.label(item['episode'])}    来源：{' / '.join(item.get('sources',[item.get('source','Nyaa')]))}\n匹配：{identity}    排序：{reason}\n字幕：{item['subtitle']}（根据资源标题标记）"
                if item.get('source_episode'):value+=f"    发布标题集数：{item['source_episode']}"
            details.configure(state='normal');details.delete('1.0','end');details.insert('end',value);details.configure(state='disabled')
        def refresh():
            if not win.winfo_exists():return
            if not self.row(sid):win.destroy();return
            selected=set(tree.selection());tree.delete(*tree.get_children());items.clear()
            recommended={x['hash'] for x in self.app.store.candidates(sid)}
            view=self.app.episode_view(sid)
            for item in self.app.store.candidates(sid,expanded=True):
                if not unknown.get() and not item['episode'].isdigit():continue
                items[item['hash']]=item
                tree.insert('','end',iid=item['hash'],values=(view.label(item['episode']),
                    'ANI' if item['ani'] else item.get('source','Nyaa'),item['subtitle'],item['seeds'] if item.get('seed_known',True) else '未知',item['title']))
            tree.selection_set([h for h in selected if h in items])
            count.set(f'全部候选 · {len(items)} 项  /  推荐 {len(recommended.intersection(items))} 项');explain()
        self.candidate_refresh[key]=refresh
        ttk.Checkbutton(heading,text='显示待核对',variable=unknown,command=refresh).pack(side='right')
        tree.bind('<<TreeviewSelect>>',explain)
        tree.bind('<Control-a>',lambda _:(tree.selection_set(tree.get_children()),'break')[-1])
        def single():
            sel=tree.selection()
            if len(sel)!=1:return None
            return items.get(sel[0])
        def transfer():
            item=single()
            if item:self.app.transfer(sid,item)
        def ignore():
            if 'transfer' in self.app.jobs:return
            self.app.store.ignore_releases(sid,tree.selection());self.app.refresh_candidates()
        bar=ttk.Frame(body);bar.pack(fill='x',pady=(8,0))
        SoftButton(bar,text='转交所选',command=transfer,style='Accent.TButton').pack(side='left')
        SoftButton(bar,text='忽略所选',command=ignore).pack(side='left',padx=8)
        SoftButton(bar,text='打开资源页面',command=lambda:self.app.open_page(single()) if single() else None).pack(side='left')
        SoftButton(bar,text='追踪设置',command=lambda:self.preferences(sid)).pack(side='right')
        win.candidate_tree=tree;win.candidate_details=details;refresh()

    def preferences(self,sid=None):
        row=self.row(sid)
        if not row:return
        sid,name,raw=row;aliases=json.loads(raw);stored=self.app.store.show_options(sid)
        win=self.window(('preferences',sid),name+' · 追踪设置','820x650')
        if win is None:return
        win.show_id=sid;self.app.editors.add(win)
        win.bind('<Destroy>',lambda event:self.app.editors.discard(win) if event.widget is win else None,add='+')
        book=ttk.Notebook(win);book.pack(fill='both',expand=True,padx=18,pady=(18,8))
        episodes=ttk.Frame(book,padding=22);search=ttk.Frame(book,padding=22)
        book.add(episodes,text='集数与显示');book.add(search,text='搜索偏好')
        continuation=tk.BooleanVar(value=continuation_eligible(name,aliases,stored))
        dual=tk.BooleanVar(value=bool(stored.get('dual')))
        offset=tk.StringVar(value=str(stored.get('offset',0)))
        default_numbering='absolute' if any(cour_index(v) is not None for k,v in aliases.items() if not k.startswith('_') and isinstance(v,str)) else 'local'
        numbering=tk.StringVar(value='全篇编号' if stored.get('numbering',default_numbering)=='absolute' else '本季编号')
        basis=tk.StringVar(value='本季' if stored.get('range_basis','local')=='local' else '全篇')
        if not stored and str(aliases.get('_bgm_id','')).isdigit():
            try:
                p=self.app.store.root/'search-cache'/f"cour-{aliases['_bgm_id']}.json"
                cached=json.loads(p.read_text(encoding='utf-8'))
                if str(cached['subject'])==str(aliases['_bgm_id']) and 0<=time.time()-cached['checked_at']<30*86400:
                    start,end=cached['span']
                    if type(start) is int and type(end) is int and 1<start<=end<=10000:offset.set(str(start-1))
            except (OSError,ValueError,TypeError,KeyError):pass
            if not offset.get().isdigit() or int(offset.get())==0:numbering.set('本季编号')
        first,last,_=self.app.store.episode_rule(sid)
        initial=self.app.episode_view(sid)
        first_var=tk.StringVar(value=str(initial.display(first)));last_var=tk.StringVar(value=str(initial.display(last)) if last else '')
        previous=[initial]
        def options():
            return dict(stored,continuation=continuation.get(),dual=dual.get() and continuation.get(),offset=int(offset.get()),
                        numbering='absolute' if numbering.get()=='全篇编号' else 'local',range_basis='local' if basis.get()=='本季' else 'absolute')
        range_label=tk.StringVar();preview=tk.StringVar()
        ttk.Checkbutton(episodes,text='这是续作或分割放送',variable=continuation,command=lambda:update()).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,14))
        dual_check=ttk.Checkbutton(episodes,text='显示本季 / 全篇双集数',variable=dual,command=lambda:update())
        dual_check.grid(row=1,column=0,columnspan=3,sticky='w',pady=(0,14))
        mapping=ttk.Frame(episodes);mapping.grid(row=2,column=0,columnspan=3,sticky='ew',pady=(0,18))
        ttk.Label(mapping,text='前序集数').grid(row=0,column=0,sticky='w',pady=6)
        offset_entry=ttk.Entry(mapping,textvariable=offset,width=8);offset_entry.grid(row=0,column=1,sticky='w',padx=12)
        ttk.Label(mapping,text='资源使用').grid(row=1,column=0,sticky='w',pady=6)
        number_box=ttk.Combobox(mapping,textvariable=numbering,values=['全篇编号','本季编号'],state='readonly',width=12);number_box.grid(row=1,column=1,sticky='w',padx=12)
        SoftButton(mapping,text='读取分集编号',command=lambda:detect()).grid(row=0,column=2,rowspan=2,padx=12)
        ttk.Label(episodes,textvariable=range_label).grid(row=3,column=0,sticky='w')
        ranges=ttk.Frame(episodes);ranges.grid(row=4,column=0,columnspan=3,sticky='w',pady=(8,12))
        ttk.Entry(ranges,textvariable=first_var,width=7).pack(side='left');ttk.Label(ranges,text='至').pack(side='left',padx=10)
        ttk.Entry(ranges,textvariable=last_var,width=7).pack(side='left')
        basis_box=ttk.Combobox(ranges,textvariable=basis,values=['本季','全篇'],state='readonly',width=6);basis_box.pack(side='left',padx=12)
        ttk.Label(episodes,textvariable=preview,style='Muted.TLabel',wraplength=650).grid(row=5,column=0,columnspan=3,sticky='w',pady=8)
        def update(convert=True):
            try:view=EpisodeView.from_options(options(),continuation.get())
            except ValueError:return
            if continuation.get():dual_check.grid()
            else:dual.set(False);dual_check.grid_remove();view=EpisodeView.from_options(options(),False)
            if view.enabled:mapping.grid();basis_box.configure(state='readonly')
            else:mapping.grid_remove();basis_box.configure(state='disabled')
            if convert:
                try:
                    raw_first=previous[0].storage(int(first_var.get()));raw_last=previous[0].storage(int(last_var.get())) if last_var.get().strip() else 0
                    first_var.set(str(view.display(raw_first)));last_var.set(str(view.display(raw_last)) if raw_last else '')
                except ValueError:pass
            previous[0]=view
            range_label.set(('本季集数范围' if view.range_basis=='local' else '全篇集数范围') if view.enabled else '集数范围')
            preview.set(f'显示示例：本季第 3 集 / 全篇第 {view.offset+3} 集' if view.enabled else '')
        def detect():
            subject=aliases.get('_bgm_id','')
            if not str(subject).isdigit():preview.set('请先在编辑名称中关联作品');return
            preview.set('正在读取分集编号…')
            def done(span):
                if win.winfo_exists():offset.set(str(span[0]-1));numbering.set('全篇编号');update()
            def failed(error):
                if win.winfo_exists():preview.set('暂未取得连续编号，可手动设置前序集数')
                self.app.note('分集编号读取：'+error,detail=True)
            self.app.background(lambda:cached_cour_span(str(subject),self.app.store.root/'search-cache'),done,key=f'metadata:numbering:{sid}',on_error=failed)
        offset_entry.bind('<FocusOut>',lambda _:update());number_box.bind('<<ComboboxSelected>>',lambda _:update());basis_box.bind('<<ComboboxSelected>>',lambda _:update())
        ttk.Label(search,text='补充名称（一行一个）').pack(anchor='w')
        extra=tk.Text(search,height=4,wrap='word',font=('Microsoft YaHei UI',10));extra.pack(fill='x',pady=(8,18));extra.insert('1.0','\n'.join(stored.get('extra_aliases',[])))
        ttk.Label(search,text='排除词（一行一个）').pack(anchor='w')
        excludes=tk.Text(search,height=4,wrap='word',font=('Microsoft YaHei UI',10));excludes.pack(fill='x',pady=(8,14));excludes.insert('1.0','\n'.join(stored.get('exclude_words',[])))
        def lines(widget):return list(dict.fromkeys(v.strip() for v in widget.get('1.0','end').splitlines() if v.strip()))
        def preview_search():
            words=lines(excludes);items=self.app.store.candidates(sid,expanded=True)
            remaining=[i for i in items if not any(w.casefold() in i['title'].casefold() for w in words)]
            messagebox.showinfo('本地筛选预览',f'当前可见候选 {len(items)} 项，使用这些排除词后保留 {len(remaining)} 项。\n\n'+'\n'.join(i['title'] for i in remaining[:5]),parent=win)
        SoftButton(search,text='预览本地筛选',command=preview_search).pack(anchor='w')
        bar=ttk.Frame(win,padding=(18,10));bar.pack(fill='x')
        def save():
            try:
                opts=options();opts.update(extra_aliases=lines(extra),exclude_words=lines(excludes))
                view=EpisodeView.from_options(opts,continuation.get())
                first=view.storage(int(first_var.get()));last=view.storage(int(last_var.get())) if last_var.get().strip() else 0
                if not 1<=first<=999 or not 0<=last<=999 or last and last<first:raise ValueError('集数范围无效，请核对起止集及编号方式')
                self.app.store.set_show_options(sid,opts);self.app.store.set_episode_range(sid,first,last)
                self.app.refresh_candidates();self.app.note(name+' 的追踪设置已保存');win.destroy()
            except ValueError as error:messagebox.showerror('无法保存',str(error),parent=win)
        SoftButton(bar,text='保存',command=save,style='Accent.TButton').pack(side='right')
        SoftButton(bar,text='说明',command=lambda:self.help('编号与筛选')).pack(side='left')
        win.dual_var=dual;win.continuation_var=continuation;win.offset_var=offset;win.numbering_var=numbering;win.range_basis_var=basis
        win.first_var=first_var;win.last_var=last_var;win.extra_text=extra;win.exclude_text=excludes;win.update_mapping=update
        update(False)

    def health(self,sid=None):
        win=self.window(('health',sid),'来源状态','920x560')
        if win is None:return
        body=ttk.Frame(win,padding=20);body.pack(fill='both',expand=True)
        ttk.Label(body,text='搜索来源',font=('Microsoft YaHei UI',14,'bold')).pack(anchor='w',pady=(0,14))
        columns=('source','status','success','attempt','hits')
        tree=ttk.Treeview(body,columns=columns,show='headings',height=6)
        for col,label,width in zip(columns,['来源','连接状态','最近成功','最近检查','返回 / 范围内'],[130,120,150,150,140]):
            tree.heading(col,text=label);tree.column(col,width=width,anchor='center')
        tree.pack(fill='both',expand=True)
        summary=tk.StringVar();ttk.Label(body,textvariable=summary,style='Muted.TLabel').pack(anchor='w',pady=12)
        def refresh():
            try:reports=json.loads((self.app.store.root/'last-search.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):reports=[]
            if sid is not None:reports=[r for r in reports if r.get('show_id')==sid]
            try:history=json.loads((self.app.store.root/'source-health.json').read_text(encoding='utf-8')) if sid is None else []
            except (OSError,ValueError):history=[]
            tree.delete(*tree.get_children())
            for item in source_health(reports,history):
                def date(value):return time.strftime('%m-%d %H:%M',time.localtime(value)) if value else '—'
                tree.insert('','end',iid=item['source'],values=(item['source'],item['status'],date(item['last_success']),date(item['last_attempt']),f"{item['returned']} / {item['kept']}"))
            summary.set(f'最近记录覆盖 {len(reports)} 部动画')
        bar=ttk.Frame(body);bar.pack(fill='x')
        SoftButton(bar,text='刷新列表',command=refresh).pack(side='left')
        SoftButton(bar,text='详细诊断',command=lambda:self.app.search_diagnostics(sid)).pack(side='left',padx=8)
        SoftButton(bar,text='说明',command=lambda:self.help('来源与状态')).pack(side='right')
        win.health_tree=tree;win.refresh_health=refresh;refresh()

    def help(self,selected='日常使用'):
        win=self.window(('help',None),'使用说明','860x600')
        if win is None:
            win=self.windows[('help',None)]
            book=next(w for w in win.winfo_children() if isinstance(w,ttk.Notebook))
            for tab in book.tabs():
                if book.tab(tab,'text')==selected:book.select(tab)
            return
        book=ttk.Notebook(win);book.pack(fill='both',expand=True,padx=18,pady=18)
        pages={
            '日常使用':'选择动画，查看介绍和推荐资源。检查所选只刷新所选动画；工具菜单可检查全部。\n\n全部候选保留作品、集数、忽略和已转交规则，展开的是推荐之外的有效候选。选择一项后可查看匹配依据并转交。\n\nCtrl / Shift 多选，Ctrl+A 全选。批量删除只删除追踪记录，不删除视频或下载器任务。\n\n追踪设置可调整集数范围、补充别名及排除词。结束集留空表示不限。',
            '编号与筛选':'双集数默认关闭，只对续作或分割放送开启。可在追踪设置中确认作品类型。\n\n前序集数为此前部分累计集数，例如12；本季第3集对应全篇第15集。资源使用全篇编号时，原始集数15显示为“3 / 总15”；资源使用本季编号时，原始集数3也显示为“3 / 总15”。读取分集编号只在来源确认连续编号时填入。\n\n筛选可选择本季或全篇编号，程序换算到原有追踪编号。切换显示不会重写下载记录。最高已完成集数形成的下限继续生效。\n\n补充别名按完整作品名称匹配，请避免加入“魔法”“朋友”等过短泛词。排除词按标题包含关系过滤；预览不执行联网检索。',
            '来源与状态':'连接正常但零命中，表示查询成功且未返回资源；连接失败表示查询没有成功，不能据此判断停更。部分可用表示最近记录中既有成功也有失败。旧记录没有时间戳时显示“—”。\n\n每个来源串行并限速，连续失败后短暂退避；其他来源仍可检查。临时连接错误最多重试一次，来源分页存在上限。\n\n下载器需自行安装配置。资源仍由用户确认后转交；待接收不等于已接收，已转交不等于已完成。字幕根据标题标记，没有读取实际媒体轨道。',
            '资料与缓存':'首次为作品补充Bangumi资料；每天首次启动更新；同一天间隔至少5小时再次进入详情可刷新。封面未变时复用缓存。\n\n分割季度使用已核对的分集范围，24小时内复用；刷新失败时最多复用30天内范围。未确认范围时保持严格标题检索。\n\n程序运行期间才定时检查与监测，关闭窗口后停止。数据位于本机用户的AnimeTracker目录，升级前应关闭旧版。',
            '活动记录':self.app.log.get('1.0','end').strip() or '暂无记录'}
        for label,value in pages.items():
            frame=ttk.Frame(book,padding=16);book.add(frame,text=label)
            text=tk.Text(frame,wrap='word',font=('Microsoft YaHei UI',10),relief='flat',spacing3=8)
            text.pack(side='left',fill='both',expand=True);scroll=ttk.Scrollbar(frame,command=text.yview);scroll.pack(side='right',fill='y');text.configure(yscrollcommand=scroll.set)
            text.insert('1.0',value);text.configure(state='disabled')
            if label==selected:book.select(frame)
