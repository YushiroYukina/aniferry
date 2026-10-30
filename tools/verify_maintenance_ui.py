"""Render 2.1 secondary screens with fictional fixtures on an isolated desktop."""
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import json

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import App
from tools.render_native import capture_window


def settle(root):
    deadline=time.monotonic()+.25
    while time.monotonic()<deadline:root.update();time.sleep(.01)


def main():
    output=ROOT/'build'/'qa-2.1';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aniferry-21-ui-') as temp:
        root=tk.Tk();app=App(root,temp,prefetch=False)
        app.auto.set(False);app.next_progress=time.monotonic()+3600
        try:
            sid=app.store.save_show('星海来信 · 虚构演示',{'中文名':'星海来信'})
            items=[dict(hash=f'{n:040x}',title=f'[演示字幕组] 星海来信 - {n:02d} [1080p][CHS]',episode=str(n),ani=False,source='演示源',seeds=90,
                        subtitle='标题声明中文',size='320 MiB',url='https://example.invalid/demo.torrent',page='https://example.invalid/demo') for n in range(1,8)]
            app.store.ingest(sid,items)
            app.store.db.execute("UPDATE releases SET status='已转交',output=? WHERE episode='6'",(str(Path(temp)/'video'),));app.store.db.commit()
            app.store.mark_completed(sid,items[5]['hash']);app.store.request_repair(sid,{3})
            app.store.correct_releases(sid,[items[6]['hash']],'季度不符')
            app.refresh_shows();settle(root);capture_window(root,output/'main.png')
            for key,open_ in [('episodes',app.library_tools.episodes),('preferences',app.library_tools.preferences),('corrections',app.library_tools.corrections),('tasks',app.library_tools.tasks),('backups',app.library_tools.backups)]:
                open_();win=app.library_tools.windows[(key,None if key in ('tasks','backups') else sid)]
                settle(root);capture_window(win,output/(key+'.png'))
                if key=='episodes':
                    book=win.repair_tree.master.master.master;book.select(1);settle(root);capture_window(win,output/'repair.png')
                if key=='preferences':
                    book=win.subscription_text.master.master;book.select(2);settle(root);capture_window(win,output/'subscriptions.png')
                win.destroy()
            root.geometry('1120x740');settle(root);capture_window(root,output/'compact.png')
            (output/'ui-check.json').write_text(json.dumps(dict(version='2.1.0',screenshots=sorted(p.name for p in output.glob('*.png')),fictional_data=True,real_downloads=0,main_desktop_used=False),ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            app.close()
            for worker in app.workers.values():worker.shutdown(wait=True,cancel_futures=True)
            app.store.db.close()
    print(str(output))


if __name__=='__main__':main()
