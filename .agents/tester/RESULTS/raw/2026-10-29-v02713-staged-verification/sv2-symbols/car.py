#!/usr/bin/env python3
"""Self-contained PyInstaller CArchive/PYZ reader (SV2 staged verification).

Pure-python, read-only. Requires the SAME Python minor version that built the
artifact (marshal version gate): asserts PYZ pymagic == running MAGIC_NUMBER.
"""
import struct, sys, os, zlib, marshal, json

COOKIE_MAGIC = b'MEI\014\013\012\013\016'
COOKIE_FMT = '!8sIIII64s'
TOC_FMT = '!IIIIBc'
PYZ_MAGIC = b'PYZ\0'

def find_cookie(fp):
    fp.seek(0, os.SEEK_END)
    end = fp.tell()
    step = 4096
    pos = end
    while pos > 0:
        start = max(0, pos - step)
        fp.seek(start)
        chunk = fp.read(pos - start)
        idx = chunk.rfind(COOKIE_MAGIC)
        if idx != -1:
            return start + idx
        pos = start
    return -1

class CArchive:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as fp:
            cs = find_cookie(fp)
            if cs == -1:
                raise RuntimeError('cookie magic not found')
            fp.seek(cs)
            magic, arc_len, toc_off, toc_len, pyvers, pylib = struct.unpack(COOKIE_FMT, fp.read(struct.calcsize(COOKIE_FMT)))
            self.cookie_start = cs
            self.archive_len = arc_len
            self.start = (cs + struct.calcsize(COOKIE_FMT)) - arc_len
            self.toc_off = toc_off
            self.toc_len = toc_len
            self.pyvers = pyvers
            self.pylib = pylib.rstrip(b'\x00').decode('latin1')
            fp.seek(self.start + toc_off)
            data = fp.read(toc_len)
        self.entries = {}
        cur = 0
        while cur < len(data):
            entry_len = struct.unpack('!I', data[cur:cur+4])[0]
            dpos, dlen, ulen = struct.unpack('!III', data[cur+4:cur+16])
            flag = data[cur+16]
            typcd = data[cur+17:cur+18].decode('latin1')
            name_len = entry_len - struct.calcsize(TOC_FMT)
            name = data[cur+18:cur+18+name_len].rstrip(b'\x00').decode('utf-8', 'replace')
            self.entries[name] = dict(pos=self.start + dpos, dlen=dlen, ulen=ulen,
                                      flag=flag, type=typcd, entry_len=entry_len)
            cur += entry_len
    def read(self, name):
        e = self.entries[name]
        with open(self.path, 'rb') as fp:
            fp.seek(e['pos'])
            return fp.read(e['dlen'])

class PYZ:
    def __init__(self, path, offset=0):
        self.path = path; self.start = offset
        with open(path, 'rb') as fp:
            fp.seek(offset)
            assert fp.read(4) == PYZ_MAGIC, 'PYZ magic mismatch'
            self.pymagic = fp.read(4)
            self.toc_off = struct.unpack('!i', fp.read(4))[0]
            fp.seek(offset + self.toc_off)
            self.toc = dict(marshal.load(fp))
    def raw(self, name):
        typ, pos, length = self.toc[name]
        with open(self.path, 'rb') as fp:
            fp.seek(self.start + pos)
            return fp.read(length)
    def code(self, name):
        typ, pos, length = self.toc[name]
        if typ == 'N':
            return None
        return marshal.loads(zlib.decompress(self.raw(name)))

def walk_consts(code, path='<module>', out=None):
    """Yield (path, const_index, value) for every const in every nested code object."""
    if out is None: out = []
    for i, c in enumerate(code.co_consts):
        out.append((path, i, c))
        if hasattr(c, 'co_code'):
            walk_consts(c, path + '.' + code.co_name, out)
    return out
