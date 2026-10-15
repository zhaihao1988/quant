import argparse
import json
import logging
from .db import migrate


def main():
    parser=argparse.ArgumentParser(prog='quant')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init-db')
    imp=sub.add_parser('import-tdx');imp.add_argument('--symbols',nargs='*')
    imp.add_argument('--verify',action='store_true',help='校验所有源文件哈希，即使修改标记未变化')
    sub.add_parser('status')
    sub.add_parser('serve')
    mcp=sub.add_parser('mcp');mcp.add_argument('--transport',choices=['stdio','streamable-http'],default='stdio')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    if args.command=='init-db':
        migrate();print('数据库迁移完成')
    elif args.command=='import-tdx':
        from .ingest import reserve_run,run_import
        from .db import connect
        mode='verify' if args.verify else 'incremental'
        run_id,reused=reserve_run(args.symbols,mode)
        if not reused:run_import(run_id,args.symbols,mode)
        with connect() as conn:row=conn.execute('SELECT * FROM import_run WHERE id=%s',(run_id,)).fetchone()
        print(json.dumps(row,default=str,ensure_ascii=False))
        if row['status']!='completed':raise SystemExit(1)
    elif args.command=='status':
        from .services import status
        print(json.dumps(status(),ensure_ascii=False,indent=2))
    elif args.command=='serve':
        import uvicorn
        from .config import settings
        cfg=settings();uvicorn.run('quant.api:app',host=cfg.host,port=cfg.port)
    elif args.command=='mcp':
        from .mcp_server import create_server
        create_server().run(transport=args.transport)


if __name__=='__main__':main()
