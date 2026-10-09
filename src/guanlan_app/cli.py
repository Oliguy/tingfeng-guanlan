"""Portable command line. Every path is user configuration or an installed resource."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import webbrowser
from . import __version__

def main(argv=None):
    parser=argparse.ArgumentParser(prog='guanlan',description='听风观澜：配置数据、启动服务或连接另一台电脑')
    parser.add_argument('--version',action='version',version=__version__)
    subs=parser.add_subparsers(dest='command',required=True)
    init=subs.add_parser('init',help='创建配置样例，不建立或修改数据库');init.add_argument('file')
    demo=subs.add_parser('demo',help='在新目录生成合成演示数据');demo.add_argument('directory')
    opened=subs.add_parser('open',help='打开或复用浏览器桌面窗口');opened.add_argument('--url',required=True)
    for name in ('doctor','serve'):
        sub=subs.add_parser(name);sub.add_argument('--config',required=True)
        if name=='serve':sub.add_argument('--open',action='store_true',help='打开桌面浏览器窗口')
    connection=subs.add_parser('connect',help='本机界面通过 API 连接远程服务')
    connection.add_argument('--url',required=True);connection.add_argument('--token-env',default='GUANLAN_ACCESS_TOKEN')
    connection.add_argument('--port',type=int,default=18739);connection.add_argument('--open',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.command=='open':
            from urllib.parse import urlsplit
            parsed=urlsplit(args.url)
            if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError('窗口地址须为 HTTP(S)，不能包含凭据')
            from guanlan_ui.desktop import open_url
            open_url(args.url);return 0
        if args.command=='init':
            from guanlan_data.config import example
            p=Path(args.file).resolve();p.parent.mkdir(parents=True,exist_ok=True)
            with p.open('x',encoding='utf-8') as f:json.dump(example(),f,ensure_ascii=False,indent=2)
            print(str(p));return 0
        if args.command=='demo':
            from guanlan_data.demo import create
            print(create(args.directory));return 0
        from .http import Server
        if args.command=='connect':
            from .client import Client
            from .service import RemoteService
            client=Client(args.url,os.environ.get(args.token_env));health=client.health()
            if health.get('application')!='tingfeng-guanlan' or health.get('api_version')!=1:
                raise ValueError('服务不支持听风观澜 API v1')
            server=Server(RemoteService(client),port=args.port)
        else:
            from guanlan_data.config import configure
            config=configure(args.config)
            if args.command=='doctor':
                from guanlan_data.doctor import inspect
                report=inspect();print(json.dumps(report,ensure_ascii=False,indent=2))
                return 0 if report['status']=='compatible' else 2
            from .service import Service
            settings=config.raw.get('server',{})
            server=Server(Service(),host=settings.get('host','127.0.0.1'),port=settings.get('port',18738),
                origin=settings.get('public_origin'),access_token=os.environ.get(settings.get('token_env','GUANLAN_ACCESS_TOKEN')))
        url=server.origin+'/home/'
        print('听风观澜 '+__version__+' · '+url,flush=True)
        if args.open:
            from guanlan_ui.desktop import open_url
            threading.Timer(.1,open_url,args=[url]).start()
        try:server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt:pass
        finally:server.close()
        return 0
    except (ValueError,OSError,KeyError) as exc:
        print('听风观澜：'+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
