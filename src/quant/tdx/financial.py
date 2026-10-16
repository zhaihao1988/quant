"""Read gpcw records without changing float32 payloads or guessing field meanings."""
from datetime import date
import math
import struct


def financial_header(data):
    if len(data)<20:raise ValueError('财务文件头不完整，可能正在下载')
    version,day,count,_,size,_=struct.unpack_from('<hIHIII',data)
    try:period=date(day//10000,day//100%100,day%100)
    except ValueError:raise ValueError('财务报告期无效')
    if count and (not size or size%4 or size>40000):raise ValueError('财务记录长度无效')
    if 20+count*11>len(data):raise ValueError('财务索引不完整')
    if 20+count*11+count*size>len(data):raise ValueError('财务文件被截断')
    return {'report_date':period,'record_count':count,'record_size':size,'field_count':size//4,'format_version':version}


def financial_records(data,header=None):
    header=header or financial_header(data)
    count=header['record_count'];size=header['record_size'];index_end=20+count*11
    ranges=[]
    for i in range(count):
        raw_code,tag,offset=struct.unpack_from('<6sBI',data,20+i*11)
        try:code=raw_code.decode('ascii')
        except UnicodeDecodeError:raise ValueError('财务证券代码无法解码')
        if len(code)!=6 or not code.isdigit():raise ValueError('财务证券代码无效')
        if offset<index_end or offset+size>len(data):raise ValueError('财务记录偏移越界')
        ranges.append((offset,offset+size,code,tag))
    ordered=sorted(ranges)
    if any(left[1]>right[0] for left,right in zip(ordered,ordered[1:])):raise ValueError('财务记录区间重叠')
    # Preserve original index order; validation above completes before any row is yielded.
    for start,end,code,tag in ranges:
        yield code,tag,data[start:end]


def decode_fields(payload):
    if len(payload)%4:raise ValueError('财务载荷不是float32序列')
    return {i:value if math.isfinite(value) and abs(value)<1e30 else None
            for i,(value,) in enumerate(struct.iter_unpack('<f',payload),1)}


def announcement_date(values,period):
    value=values.get(314)
    if value is None or value<=0 or value!=int(value):return None
    value=int(value)
    try:
        if value>=10000000:day=date(value//10000,value//100%100,value%100)
        else:
            # Choose the century nearest the report period, not a fixed 1969/2068 pivot.
            yy=value//10000;century=period.year//100*100
            candidates=[]
            for c in (century-100,century,century+100):
                try:candidates.append(date(c+yy,value//100%100,value%100))
                except ValueError:pass
            if not candidates:return None
            day=min(candidates,key=lambda d:abs((d-period).days))
    except ValueError:return None
    return day if period<=day<=date(period.year+2,12,31) else None
