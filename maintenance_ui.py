"""Secondary screens for recovery; keep the main surface small."""
import json
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from appearance import SoftButton
from maintenance import FEEDBACK_REASONS
from downloaders import LABELS


class MaintenanceUI:
    def table(self,parent,columns,widths):
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,pady=8)
        tree=ttk.Treeview(frame,columns=tuple(range(len(columns))),show='headings',selectmode='extended')
        for i,(label,width) in enumerate(zip(columns,widths)):
            tree.heading(str(i),text=label,anchor='w' if i==len(columns)-1 else 'center')
            tree.column(str(i),width=width,minwidth=65,stretch=i==len(columns)-1,anchor='w' if i==len(columns)-1 else 'center')
        tree.grid(row=0,column=0,sticky='nsew')
        sy=ttk.Scrollbar(frame,command=tree.yview);sy.grid(row=0,column=1,sticky='ns');tree.configure(yscrollcommand=sy.set)
        sx=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview);sx.grid(row=1,column=0,sticky='ew');tree.configure(xscrollcommand=sx.set)
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        tree.bind('<Control-a>',lambda _:(tree.selection_set(tree.get_children()),'break')[-1])
        return tree

    def feedback(self,sid,hashes):
        hashes=list(hashes)
        if not hashes:return
        win=self.window(('feedback',sid),'忽略与纠正','760x540')
        if win is None:return
        body=ttk.Frame(win,padding=24);body.pack(fill='both',expand=True)
        ttk.Label(body,text=f'处理所选 {len(hashes)} 项资源',style='Section.TLabel').pack(anchor='w',pady=(0,20))
        reason=tk.StringVar(value=FEEDBACK_REASONS[0])
        for label in FEEDBACK_REASONS:ttk.Radiobutton(body,text=label,variable=reason,value=label).pack(anchor='w',pady=8)
        def save():
            if 'transfer' in self.app.jobs:self.app.note('正在转交，请稍后处理');return
            count=self.app.store.correct_releases(sid,hashes,reason.get())
            self.app.refresh_candidates();self.app.note(f'已处理 {count} 项，可在忽略与纠正记录中恢复');win.destroy()
        bar=ttk.Frame(body);bar.pack(side='bottom',fill='x')
        SoftButton(bar,text='确认',command=save,style='Accent.TButton').pack(side='right')
        SoftButton(bar,text='查看处理记录',command=lambda:self.corrections(sid)).pack(side='left')
        win.reason_var=reason

    def corrections(self,sid=None):
        row=self.row(sid)
        if not row:return
        sid,name,_=row;win=self.window(('corrections',sid),name+' · 忽略与纠正记录')
        if win is None:return
        body=ttk.Frame(win,padding=20);body.pack(fill='both',expand=True)
        tree=self.table(body,['原因','集数','资源标题'],[140,100,600])
        count=tk.StringVar();ttk.Label(body,textvariable=count,style='Muted.TLabel').pack(anchor='w')
        def refresh():
            tree.delete(*tree.get_children())
            rows=self.app.store.db.execute("SELECT r.hash,r.episode,r.title,COALESCE(c.reason,r.status) FROM releases r LEFT JOIN corrections c ON c.show_id=r.show_id AND c.hash=r.hash WHERE r.show_id=? AND r.status IN ('已纠正','忽略','已清理','误匹配')",(sid,)).fetchall()
            for h,ep,title,reason in rows:tree.insert('','end',iid=h,values=(reason,self.app.episode_view(sid).label(ep),title))
            count.set(f'{len(rows)} 项记录；恢复后仍按本作与集数规则筛选。')
        def restore():
            if 'transfer' in self.app.jobs:return
            n=self.app.store.restore_candidates(sid,tree.selection());self.app.refresh_candidates();refresh();self.app.note(f'已恢复 {n} 项候选')
        SoftButton(body,text='恢复所选',command=restore).pack(anchor='e',pady=10)
        win.correction_tree=tree;refresh()

    def episodes(self,sid=None):
        row=self.row(sid)
        if not row:return
        sid,name,_=row;key=('episodes',sid);win=self.window(key,name+' · 集数管理','1020x680')
        if win is None:return
        body=ttk.Frame(win,padding=18);body.pack(fill='both',expand=True)
        book=ttk.Notebook(body);book.pack(fill='both',expand=True)
        overview=ttk.Frame(book,padding=12);resources=ttk.Frame(book,padding=12)
        book.add(overview,text='集数记录');book.add(resources,text='补搜候选')
        top=ttk.Frame(overview);top.pack(fill='x')
        ttk.Label(top,text='检查截止集').pack(side='left')
        end=tk.StringVar();ttk.Entry(top,textvariable=end,width=8).pack(side='left',padx=8)
        inventory=self.table(overview,['集数','状态'],[150,650])
        resource_tree=self.table(resources,['集数','来源','中文字幕','资源标题'],[120,110,130,550])
        hint=tk.StringVar();ttk.Label(resources,textvariable=hint,style='Muted.TLabel').pack(anchor='w')
        items={}
        def refresh():
            if not win.winfo_exists():return
            if not self.row(sid):win.destroy();return
            selected=set(inventory.selection());position=inventory.yview()[0]
            try:
                limit=self.app.episode_view(sid).storage(int(end.get())) if end.get().strip() else None
                rows=self.app.store.episode_inventory(sid,limit)
            except ValueError:hint.set('请填写有效的截止集数');return
            inventory.delete(*inventory.get_children());view=self.app.episode_view(sid)
            for ep,state in rows:inventory.insert('','end',iid=str(ep),values=(view.label(str(ep)),state))
            inventory.selection_set([v for v in selected if inventory.exists(v)]);inventory.yview_moveto(position)
            selected=set(resource_tree.selection());resource_tree.delete(*resource_tree.get_children());items.clear()
            targets=self.app.store.repair_episodes(sid)
            for item in self.app.store.candidates(sid,expanded=True,episodes=targets):
                h=item['hash'];items[h]=item
                resource_tree.insert('','end',iid=h,values=(view.label(item['episode']),'ANI' if item['ani'] else item.get('source','Nyaa'),item['subtitle'],item['title']))
            resource_tree.selection_set([h for h in selected if h in items])
            hint.set(f'补搜目标 {len(targets)} 集 · 候选 {len(items)} 项')
        SoftButton(top,text='查看',command=refresh).pack(side='left')
        ttk.Label(overview,text='默认检查到本地记录中的最高集数；可指定截止集查看尚无记录的集数。',style='Muted.TLabel',wraplength=820).pack(anchor='w')
        def repair():
            if 'scan' in self.app.jobs:self.app.note('正在检查更新，请结束后再补搜');return
            chosen={int(v) for v in inventory.selection()}
            if not chosen:self.app.note('请先选择要补搜的集数');return
            self.app.store.request_repair(sid,chosen);self.app.scan({sid},repair={sid:chosen});book.select(resources);refresh()
        def stop():
            self.app.store.end_repair(sid,{int(v) for v in inventory.selection()});refresh()
        bar=ttk.Frame(overview);bar.pack(fill='x',pady=10)
        SoftButton(bar,text='补搜所选集数',command=repair,style='Accent.TButton').pack(side='left')
        SoftButton(bar,text='结束所选补搜',command=stop).pack(side='left',padx=8)
        def transfer():
            sel=resource_tree.selection()
            if len(sel)==1:self.app.transfer(sid,items[sel[0]])
        bar2=ttk.Frame(resources);bar2.pack(fill='x',pady=10)
        SoftButton(bar2,text='转交所选',command=transfer,style='Accent.TButton').pack(side='left')
        SoftButton(bar2,text='忽略与纠正',command=lambda:self.feedback(sid,resource_tree.selection())).pack(side='left',padx=8)
        self.candidate_refresh[key]=refresh
        win.episode_tree=inventory;win.repair_tree=resource_tree;win.end_var=end;refresh()

    def tasks(self):
        key=('tasks',None);win=self.window(key,'任务与转交记录','1100x680')
        if win is None:return
        body=ttk.Frame(win,padding=18);body.pack(fill='both',expand=True)
        tree=self.table(body,['状态','下载器','动画','资源标题'],[150,120,180,500])
        detail=tk.StringVar();ttk.Label(body,textvariable=detail,wraplength=1000,style='Muted.TLabel').pack(fill='x',pady=8)
        entries={}
        def selected():return entries.get(tree.selection()[0]) if len(tree.selection())==1 else None
        def explain(_=None):
            item=selected()
            if item:
                date=time.strftime('%m-%d %H:%M:%S',time.localtime(item['checked'])) if item['checked'] else '尚未检查'
                detail.set(f"记录：{item['status']} · 最近核对：{date}\n目录：{item['output']}\n{item['detail']}")
            else:detail.set('选择任务查看详情；状态依据对应下载器的最近一次检查。')
        def refresh():
            if not win.winfo_exists():return
            selected_ids=set(tree.selection());position=tree.yview()[0];tree.delete(*tree.get_children());entries.clear()
            for item in self.app.store.task_history():
                iid=f"{item['sid']}:{item['hash']}";entries[iid]=item
                backend=self.app.store.handoff_backend(item['sid'],item['hash'])
                tree.insert('','end',iid=iid,values=(item['state'],LABELS.get(backend,backend),item['name'],item['title']))
            tree.selection_set([k for k in selected_ids if k in entries]);tree.yview_moveto(position);explain()
        tree.bind('<<TreeviewSelect>>',explain)
        def check():
            if 'monitor' in self.app.jobs:self.app.note('正在检查下载器');return
            root=self.app.store.root
            def work():
                from core import Store
                store=Store(root)
                try:return store.poll_downloaders()
                finally:store.db.close()
            def done(_):self.app.refresh_candidates();refresh()
            self.app.background(work,done,key='monitor')
        def reset():
            item=selected()
            if not item or self.app.busy or 'monitor' in self.app.jobs:return
            try:
                self.app.store.reset_handoff_checked(item['sid'],item['hash']);self.app.refresh_candidates();refresh()
                self.app.note('已恢复待选；再次转交时仍会核对已有任务')
            except ValueError as error:messagebox.showinfo('暂不能恢复待选',str(error),parent=win)
        def complete():
            item=selected()
            if not item or self.app.busy or 'monitor' in self.app.jobs:return
            if item['status']!='已转交':messagebox.showinfo('请先核对接收','只能手动确认已转交的任务。',parent=win);return
            if not messagebox.askyesno('确认完成','请确认本集所需文件已全部下载完成。',parent=win):return
            self.app.store.mark_completed(item['sid'],item['hash']);self.app.store.export_queue();self.app.refresh_candidates();refresh()
        bar=ttk.Frame(body);bar.pack(fill='x')
        SoftButton(bar,text='检查状态',command=check,style='Accent.TButton').pack(side='left')
        SoftButton(bar,text='确认未接收后恢复待选',command=reset).pack(side='left',padx=8)
        SoftButton(bar,text='手动确认完成',command=complete).pack(side='right')
        self.candidate_refresh[key]=refresh;win.task_tree=tree;refresh()

    def backups(self):
        win=self.window(('backups',None),'备份与恢复','950x620')
        if win is None:return
        body=ttk.Frame(win,padding=20);body.pack(fill='both',expand=True)
        tree=self.table(body,['时间','大小','备份文件'],[170,100,560]);files={}
        hint=tk.StringVar();ttk.Label(body,textvariable=hint,wraplength=850,style='Muted.TLabel').pack(fill='x',pady=8)
        def refresh():
            tree.delete(*tree.get_children());files.clear()
            for i,p in enumerate(self.app.store.backup_files()):
                files[str(i)]=p;tree.insert('','end',iid=str(i),values=(time.strftime('%Y-%m-%d %H:%M',time.localtime(p.stat().st_mtime)),f'{p.stat().st_size/1024:.0f} KiB',p.name))
            hint.set('数据库备份含本机设置与加密连接凭据，请妥善保管。分享追番列表请使用“导出列表”。')
        def backup():
            if self.app.jobs:self.app.note('请等待后台任务结束后备份');return
            self.app.store.backup('manual');refresh()
        def restore(path=None):
            if self.app.jobs:self.app.note('请等待后台任务结束后恢复');return
            if path is None:
                sel=tree.selection()
                if len(sel)!=1:return
                path=files[sel[0]]
            try:
                n=self.app.store.validate_backup(path)
                if not messagebox.askyesno('恢复备份',f'将恢复 {n} 部动画及此备份中的本地记录。当前数据会先备份；恢复后请重新打开番舟。',parent=win):return
                self.app.store.restore_backup(path)
                messagebox.showinfo('恢复完成','已保留恢复前备份。请重新打开番舟。',parent=win);self.app.close()
            except Exception as error:messagebox.showerror('无法恢复',str(error),parent=win)
        def choose_restore():
            path=filedialog.askopenfilename(parent=win,title='选择番舟数据库备份',filetypes=[('番舟备份','*.sqlite3')])
            if path:restore(path)
        def export():
            path=filedialog.asksaveasfilename(parent=win,title='导出追番列表',defaultextension='.json',initialfile='AniFerry-追番列表.json',filetypes=[('追番列表','*.json')])
            if path:
                try:n=self.app.store.export_library(path);hint.set(f'已导出 {n} 部动画；不包含下载器凭据和任务历史。')
                except Exception as error:messagebox.showerror('导出失败',str(error),parent=win)
        def import_():
            if self.app.jobs:self.app.note('请等待后台任务结束后导入');return
            path=filedialog.askopenfilename(parent=win,title='导入番舟追番列表',filetypes=[('追番列表','*.json')])
            if path:
                try:n=self.app.store.import_library(path);self.app.refresh_shows();refresh();hint.set(f'已导入 {n} 部动画，重复作品已跳过。')
                except Exception as error:messagebox.showerror('导入失败',str(error),parent=win)
        bar=ttk.Frame(body);bar.pack(fill='x',pady=8)
        for label,command in [('立即备份',backup),('恢复所选',restore),('选择备份文件',choose_restore),('导出列表',export),('导入列表',import_)]:
            SoftButton(bar,text=label,command=command).pack(side='left',padx=3)
        self.app.editors.add(win)
        win.bind('<Destroy>',lambda e:self.app.editors.discard(win) if e.widget is win else None,add='+')
        win.backup_tree=tree;refresh()
