import hashlib
import math
import struct
from datetime import date
from pathlib import Path


def int_date(value):
    text = str(value)
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def dbf_records(path):
    yield from dbf_records_bytes(path.read_bytes())


def dbf_records_bytes(data):
    if len(data) < 32:
        raise ValueError('DBF文件头不完整')
    count = struct.unpack_from('<I', data, 4)[0]
    header_size, row_size = struct.unpack_from('<HH', data, 8)
    if header_size<33 or (header_size-33)%32 or row_size<1:
        raise ValueError('DBF记录布局无效')
    if header_size + count * row_size > len(data):
        raise ValueError('DBF文件不完整，可能正在更新')
    fields, offset = [], 1
    for i in range(32, header_size - 1, 32):
        name = data[i:i+11].split(b'\0')[0].decode('ascii')
        size = data[i+16]
        fields.append((name, offset, size))
        offset += size
    if offset!=row_size:raise ValueError('DBF字段长度与记录长度不一致')
    for i in range(count):
        row = data[header_size+i*row_size:header_size+(i+1)*row_size]
        if row[:1] != b'*':
            yield {name: row[o:o+s].decode('gbk', errors='replace').strip() for name,o,s in fields}


def day_records(data):
    if len(data) % 32:
        raise ValueError('日线文件大小不是32的整数倍，可能下载尚未完成')
    previous = None
    for dt, op, hi, lo, cl, amount, volume, _ in struct.iter_unpack('<IIIIIfII', data):
        day = int_date(dt)
        if previous and day <= previous:
            raise ValueError(f'日线日期重复或乱序：{day}')
        if not (0 <= lo <= op <= hi and lo <= cl <= hi and math.isfinite(amount) and amount >= 0):
            raise ValueError(f'无效OHLC或成交额：{day}')
        previous = day
        yield (day, op / 100, hi / 100, lo / 100, cl / 100, volume, amount)


def stable_bytes(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('源文件正在写入，请等待通达信下载完成后重试')
    return data, hashlib.sha256(data).hexdigest()


def gbbq_records(data):
    # Fixed Blowfish schedule used by TDX; data table provenance in THIRD_PARTY.md.
    key_data = bytes.fromhex(Path(__file__).with_name('gbbq_key.hex').read_text())
    keys = struct.unpack('<' + 'I' * (len(key_data)//4), key_data)
    def decrypt(block):
        a,b = struct.unpack('<II', block)
        a ^= keys[17]
        for j in range(16,0,-1):
            f = (keys[18+(a>>24)] + keys[274+((a>>16)&255)]) & 0xffffffff
            f ^= keys[530+((a>>8)&255)]
            f = (f + keys[786+(a&255)]) & 0xffffffff
            a,b = b ^ f ^ keys[j],a
        return struct.pack('<II', b ^ keys[0], a)
    if len(data) < 4 or len(data) != 4 + struct.unpack_from('<I',data)[0] * 29:
        raise ValueError('权息文件大小校验失败')
    markets = {0:'SZ',1:'SH',2:'BJ'}
    for off in range(4,len(data),29):
        clear = b''.join(decrypt(data[off+i:off+i+8]) for i in (0,8,16)) + data[off+24:off+29]
        market,code,dt,category,c1,c2,c3,c4 = struct.unpack('<B7sIBffff',clear)
        code = code.rstrip(b'\0').decode('ascii')
        if market not in markets:
            continue  # Other markets are outside the A-share adapter.
        if not code.isdigit() or not 1 <= category <= 255:
            raise ValueError(f'权息解码校验失败：market={market},code={code!r},category={category},date={dt}')
        if not all(math.isfinite(x) for x in (c1,c2,c3,c4)):
            raise ValueError('权息包含无效浮点数')
        yield (f'{code}.{markets[market]}',int_date(dt),category,c1,c2,c3,c4)


def is_a_stock(market, code):
    return ((market == 'SH' and code.startswith(('600','601','603','605','688','689')))
            or (market == 'SZ' and code.startswith(('000','001','002','003','300','301')))
            or (market == 'BJ' and code.startswith(('4','8','92'))))
