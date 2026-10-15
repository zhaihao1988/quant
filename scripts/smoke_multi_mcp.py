import asyncio,json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def unpack(result):
    assert not result.isError
    return result.structuredContent or json.loads(next(c.text for c in result.content if hasattr(c,'text')))


async def main():
    async with streamable_http_client('http://127.0.0.1:8766/mcp') as (reader,writer,_):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            catalog=unpack(await session.call_tool('multi_strategy_catalog',{}))
            assert len(catalog['strategies'])==7
            task=unpack(await session.call_tool('start_multi_screen',{'parameters':{
                'as_of':'2026-10-14','symbols':['300138.SZ'],'strategies':['RefactoredMultiLevelCrossStrategy']}}))
            for _ in range(60):
                result=unpack(await session.call_tool('multi_screen_status',{'run_id':task['id']}))
                if result['status'] not in ('queued','running'):break
                await asyncio.sleep(.5)
            assert result['status']=='completed',result['status']
            assert result['processed']==1 and not result['result']['errors']
            assert len(result['result']['coverage'][0]['evaluations'])==1
            print('MCP HTTP verified: catalog, background submission, persisted results.')


if __name__=='__main__':asyncio.run(main())
