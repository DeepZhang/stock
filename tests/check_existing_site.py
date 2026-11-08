#!/usr/bin/env python3
"""仅起本地隔离Flask服务；外部行情固定为空响应，不访问线上、不发送推送。
比较 git HEAD 原始 app.py 与当前 app.py 的所有现有路由状态和 JSON 顶层类型。
"""
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'modules')]
from werkzeug.serving import make_server


class Response:
    text=''
    content=b''
    status_code=200
    def json(self):
        return {'data':{'diff':[],'total':0,'pool':[],'klines':[]},'QuotationCodeTable':{'Data':[]}}
    def raise_for_status(self):
        pass


def load_app(source,name):
    module=types.ModuleType(name)
    module.__file__=str(ROOT/'app.py')
    sys.modules[name]=module
    # 原app导入即启动两个线程，在测试装载时禁用，实际WSGI线程稍后单独启动。
    with patch('threading.Thread.start'):
        exec(compile(source,str(ROOT/'app.py'),'exec'),module.__dict__)
    module.app.logger.disabled=True
    return module


def inspect(module,temp):
    module.BASE_DIR=str(temp)
    module.SCREEN_CACHE=str(temp/'data/stock_picks.json')
    (temp/'data').mkdir(parents=True,exist_ok=True)
    import daily_report
    daily_report.DATA_DIR=str(temp/'reports')
    Path(daily_report.DATA_DIR).mkdir(exist_ok=True)
    fixture=[{'code':'600000','name':'隔离契约样本','price':10,'vol_ratio':2}]
    statuses={}
    with ExitStack() as stack:
        stack.enter_context(patch('requests.get',return_value=Response()))
        stack.enter_context(patch('stock_screener.run_screen',return_value=fixture))
        stack.enter_context(patch.object(module,'_read_latest_data',return_value=({'times':[],'sectors':{}},'')))
        server=make_server('127.0.0.1',0,module.app)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        port=server.server_port
        def get(url):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
            conn.request('GET',url)
            response=conn.getresponse(); content=response.read(); conn.close()
            kind='html'
            try:
                body=json.loads(content); kind=type(body).__name__
            except (ValueError,UnicodeError):
                body=None
            return response.status,kind,body
        try:
            for rule in module.app.url_map.iter_rules():
                path=rule.rule
                if '<' in path:
                    continue
                status,kind,_=get(path)
                statuses[path]={'status':status,'type':kind}
            status,_,body=get('/api/screen')
            assert status==200 and body=={'list':fixture,'count':1}
            saved=json.loads(Path(module.SCREEN_CACHE).read_text())
            assert isinstance(saved['list'],list) and saved['list']==fixture and saved['time']
            assert get('/api/screen_cached')[2]==saved
            for path in ('/api/kline','/api/quote','/api/fundamental'):
                assert get(path)[0]==400
            for path in ('/api/prediction','/api/shortterm'):
                assert get(path)[0]==404
        finally:
            server.shutdown();thread.join();server.server_close()
    return statuses


def main():
    baseline=subprocess.check_output(['git','show','HEAD:app.py'],cwd=ROOT,text=True)
    modules=[load_app(baseline,'stock_site_baseline'),load_app((ROOT/'app.py').read_text(),'stock_site_current')]
    with tempfile.TemporaryDirectory() as temp:
        statuses=[inspect(m,Path(temp)/str(i)) for i,m in enumerate(modules)]
    assert statuses[0]==statuses[1], 'existing route regression changed'
    result={'route_count':len(statuses[1]),'baseline_equal':True,'routes':statuses[1],
            'mode':'local HTTP server; empty external feed fixtures; real API screen file persistence; not live upstream test'}
    path=ROOT/'data/history/site_regression.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'route_count':result['route_count'],'baseline_equal':True,
                      'existing_errors':{p:v for p,v in statuses[1].items() if v['status']>=500}},ensure_ascii=False))


if __name__=='__main__':
    main()
