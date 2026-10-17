"""Versioned tokenizer-aware SiliconFlow BGE embeddings; no usage collection."""
from functools import lru_cache
from hashlib import sha256
import json,math,os,time
from pathlib import Path
import httpx
from .config import ROOT,settings

MODEL='BAAI/bge-large-zh-v1.5'
DIM=1024
INSTRUCTION='为这个句子生成表示以用于检索相关文章：'
CHUNKER='tokens-420-overlap48-v1'
TOKENIZER=Path(__file__).parent/'data'/'bge-tokenizer.json'

@lru_cache(maxsize=1)
def tokenizer():
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(TOKENIZER))

def profile():
    return sha256((MODEL+'|1024|cosine|'+CHUNKER+'|'+INSTRUCTION+'|'+sha256(TOKENIZER.read_bytes()).hexdigest()).encode()).hexdigest()

def chunks(text):
    # Offsets preserve exact original evidence, rather than decoding normalized tokens.
    offsets=tokenizer().encode(text,add_special_tokens=False).offsets
    for start in range(0,len(offsets),372):
        subset=offsets[start:start+420]
        if not subset:break
        piece=text[subset[0][0]:subset[-1][1]].strip()
        if piece:yield piece
        if start+420>=len(offsets):break

def validate(data,n):
    if not isinstance(data,list) or len(data)!=n:raise ValueError('向量返回数量不匹配')
    result=[None]*n
    for item in data:
        i=item.get('index');v=item.get('embedding')
        if type(i)!=int or not 0<=i<n or result[i] is not None:raise ValueError('向量返回索引异常')
        if not isinstance(v,list) or len(v)!=DIM or any(type(x) not in (int,float) or not math.isfinite(x) for x in v):raise ValueError('向量维度或数值异常')
        norm=math.sqrt(sum(x*x for x in v))
        if norm<1e-12:raise ValueError('返回了零向量')
        result[i]=[x/norm for x in v]
    return result

def embed(texts,query=False):
    settings();key=os.getenv('QUANT_SILICONFLOW_API_KEY')
    if not key:raise ValueError('尚未配置硅基流动Key')
    inputs=[INSTRUCTION+t if query else t for t in texts]
    if not inputs or len(inputs)>16:raise ValueError('向量批次必须为1至16段')
    if any(not t.strip() or len(tokenizer().encode(t).ids)>512 for t in inputs):raise ValueError('向量文本超过512 token或为空，请缩短查询')
    with httpx.Client(timeout=httpx.Timeout(30,connect=10)) as c:
        for attempt in range(2):
            try:
                r=c.post('https://api.siliconflow.cn/v1/embeddings',headers={'Authorization':'Bearer '+key},json={'model':MODEL,'input':inputs,'encoding_format':'float'})
                if r.status_code in (429,500,502,503,504) and not attempt:time.sleep(2);continue
                if r.status_code!=200:raise ValueError(f'向量模型HTTP {r.status_code}')
                return validate(r.json().get('data'),len(inputs))
            except httpx.TransportError:
                if attempt:raise ValueError('向量服务连接失败或超时') from None
                time.sleep(2)

def literal(v):return '['+','.join(str(x) for x in v)+']'
