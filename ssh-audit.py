#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
   The MIT License (MIT)
   
   Copyright (C) 2016 Andris Raugulis (moo@arthepsy.eu)
   
   Permission is hereby granted, free of charge, to any person obtaining a copy
   of this software and associated documentation files (the "Software"), to deal
   in the Software without restriction, including without limitation the rights
   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   copies of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:
   
   The above copyright notice and this permission notice shall be included in
   all copies or substantial portions of the Software.
   
   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
   THE SOFTWARE.
"""
from __future__ import print_function
import os, io, sys, socket, struct, random, errno, getopt, re, hashlib, base64

VERSION = 'v1.5.0'


def usage(err=None):
	p = os.path.basename(sys.argv[0])
	out.batch = False
	out.minlevel = 'info'
	out.head('# {0} {1}, moo@arthepsy.eu'.format(p, VERSION))
	if err is not None:
		out.fail('\n' + err)
	out.info('\nusage: {0} [-12bnv] [-l <level>] <host[:port]>\n'.format(p))
	out.info('   -h,  --help             print this help')
	out.info('   -1,  --ssh1             force ssh version 1 only')
	out.info('   -2,  --ssh2             force ssh version 2 only')
	out.info('   -b,  --batch            batch output')
	out.info('   -n,  --no-colors        disable colors')
	out.info('   -v,  --verbose          verbose output')
	out.info('   -l,  --level=<level>    minimum output level (info|warn|fail)')
	out.sep()
	sys.exit(1)


class AuditConf(object):
	def __init__(self):
		self.__host = None
		self.__port = 22
		self.__ssh1 = False
		self.__ssh2 = False
	
	@property
	def host(self):
		return self.__host
	
	@host.setter
	def host(self, v):
		self.__host = v
	
	@property
	def port(self):
		return self.__port
	
	@port.setter
	def port(self, v):
		self.__port = v
	
	@property
	def ssh1(self):
		return self.__ssh1
	
	@ssh1.setter
	def ssh1(self, v):
		self.__ssh1 = v
	
	@property
	def ssh2(self):
		return self.__ssh2
	
	@ssh2.setter
	def ssh2(self, v):
		self.__ssh2 = v


class Output(object):
	LEVELS = ['info', 'warn', 'fail']
	COLORS = {'head': 36, 'good': 32, 'warn': 33, 'fail': 31}
	
	def __init__(self):
		self.batch = False
		self.colors = True
		self.verbose = False
		self.__minlevel = 0
	
	@property
	def minlevel(self):
		return self.__minlevel
	
	@minlevel.setter
	def minlevel(self, name):
		self.__minlevel = self.getlevel(name)
	
	def getlevel(self, name):
		cname = 'info' if name == 'good' else name
		if cname not in self.LEVELS:
			return sys.maxsize
		return self.LEVELS.index(cname)
	
	def sep(self):
		if not self.batch:
			print()
	
	def _colorized(self, color):
		return lambda x: print(u'{0}{1}\033[0m'.format(color, x))
	
	def __getattr__(self, name):
		if name == 'head' and self.batch:
			return lambda x: None
		if not self.getlevel(name) >= self.minlevel:
			return lambda x: None
		if self.colors and os.name == 'posix' and name in self.COLORS:
			color = u'\033[0;{0}m'.format(self.COLORS[name])
			return self._colorized(color)
		else:
			return lambda x: print(u'{0}'.format(x))


class OutputBuffer(list):
	def __enter__(self):
		self.__buf = io.StringIO()
		self.__stdout = sys.stdout
		sys.stdout = self.__buf
		return self
	
	def flush(self):
		for line in self:
			print(line)
	
	def __exit__(self, *args):
		self.extend(self.__buf.getvalue().splitlines())
		sys.stdout = self.__stdout


class KexParty(object):
	encryption = []
	mac = []
	compression = []
	languages = []


class Kex(object):
	cookie = None
	kex_algorithms = []
	key_algorithms = []
	server = KexParty()
	client = KexParty()
	follows = False
	unused = 0
	
	@classmethod
	def parse(cls, payload):
		kex = cls()
		buf = ReadBuf(payload)
		kex.cookie = buf.read(16)
		kex.kex_algorithms = buf.read_list()
		kex.key_algorithms = buf.read_list()
		kex.client.encryption = buf.read_list()
		kex.server.encryption = buf.read_list()
		kex.client.mac = buf.read_list()
		kex.server.mac = buf.read_list()
		kex.client.compression = buf.read_list()
		kex.server.compression = buf.read_list()
		kex.client.languages = buf.read_list()
		kex.server.languages = buf.read_list()
		kex.follows = buf.read_bool()
		kex.unused = buf.read_int()
		return kex


class SSH1(object):
	class CRC32(object):
		def __init__(self):
			self._table = [0] * 256
			for i in range(256):
				crc = 0
				n = i
				for j in range(8):
					x = (crc ^ n) & 1
					crc = (crc >> 1) ^ (x * 0xedb88320)
					n = n >> 1
				self._table[i] = crc
		
		def calc(self, v):
			crc, l = 0, len(v)
			for i in range(l):
				n = ord(v[i:i + 1])
				n = n ^ (crc & 0xff)
				crc = (crc >> 8) ^ self._table[n]
			return crc
	
	_crc32 = CRC32()
	CIPHERS = ['none', 'idea', 'des', '3des', 'tss', 'rc4', 'blowfish']
	AUTHS = [None, 'rhosts', 'rsa', 'password', 'rhosts_rsa', 'tis', 'kerberos']
	
	@classmethod
	def crc32(cls, v):
		return cls._crc32.calc(v)
	
	class KexDB(object):
		FAIL_PLAINTEXT        = 'no encryption/integrity'
		FAIL_OPENSSH37_REMOVE = 'removed since OpenSSH 3.7'
		FAIL_NA_BROKEN        = 'not implemented in OpenSSH, broken algorithm'
		FAIL_NA_UNSAFE        = 'not implemented in OpenSSH (server), unsafe algorithm'
		TEXT_CIPHER_IDEA      = 'cipher used by commercial SSH'
		
		ALGORITHMS = {
			'key': {
				'ssh-rsa1': [['1.2.2']],
			},
			'enc': {
				'none': [['1.2.2'], [FAIL_PLAINTEXT]],
				'idea': [[None], [], [], [TEXT_CIPHER_IDEA]],
				'des': [['2.3.0C'], [FAIL_NA_UNSAFE]],
				'3des': [['1.2.2']],
				'tss': [[''], [FAIL_NA_BROKEN]],
				'rc4': [[], [FAIL_NA_BROKEN]],
				'blowfish': [['1.2.2']],
			},
			'aut': {
				'rhosts': [['1.2.2', '3.6'], [FAIL_OPENSSH37_REMOVE]],
				'rsa': [['1.2.2']],
				'password': [['1.2.2']],
				'rhosts_rsa': [['1.2.2']],
				'tis': [['1.2.2']],
				'kerberos': [['1.2.2', '3.6'], [FAIL_OPENSSH37_REMOVE]],
			}
		}
	
	class PublicKeyMessage(object):
		def __init__(self, cookie, skey, hkey, pflags, cmask, amask):
			assert len(skey) == 3
			assert len(hkey) == 3
			self.__cookie = cookie
			self.__server_key = skey
			self.__host_key = hkey
			self.__protocol_flags = pflags
			self.__supported_ciphers_mask = cmask
			self.__supported_authentications_mask = amask
		
		@property
		def cookie(self):
			return self.__cookie
		
		@property
		def server_key_bits(self):
			return self.__server_key[0]
		
		@property
		def server_key_public_exponent(self):
			return self.__server_key[1]
		
		@property
		def server_key_public_modulus(self):
			return self.__server_key[2]
		
		@property
		def host_key_bits(self):
			return self.__host_key[0]
		
		@property
		def host_key_public_exponent(self):
			return self.__host_key[1]
		
		@property
		def host_key_public_modulus(self):
			return self.__host_key[2]
		
		@property
		def host_key_fingerprint_data(self):
			mod = WriteBuf._create_mpint(self.host_key_public_modulus, False)
			e = WriteBuf._create_mpint(self.host_key_public_exponent, False)
			return mod + e
		
		@property
		def protocol_flags(self):
			return self.__protocol_flags
		
		@property
		def supported_ciphers_mask(self):
			return self.__supported_ciphers_mask
		
		@property
		def supported_ciphers(self):
			ciphers = []
			for i in range(len(SSH1.CIPHERS)):
				if self.__supported_ciphers_mask & (1 << i) != 0:
					ciphers.append(SSH1.CIPHERS[i])
			return ciphers
		
		@property
		def supported_authentications_mask(self):
			return self.__supported_authentications_mask
		
		@property
		def supported_authentications(self):
			auths = []
			for i in range(1, len(SSH1.AUTHS)):
				if self.__supported_authentications_mask & (1 << i) != 0:
					auths.append(SSH1.AUTHS[i])
			return auths
		
		@classmethod
		def parse(cls, payload):
			buf = ReadBuf(payload)
			cookie = buf.read(8)
			server_key_bits = buf.read_int()
			server_key_exponent = buf.read_mpint1()
			server_key_modulus = buf.read_mpint1()
			skey = (server_key_bits, server_key_exponent, server_key_modulus)
			host_key_bits = buf.read_int()
			host_key_exponent = buf.read_mpint1()
			host_key_modulus = buf.read_mpint1()
			hkey = (host_key_bits, host_key_exponent, host_key_modulus)
			pflags = buf.read_int()
			cmask = buf.read_int()
			amask = buf.read_int()
			pkm = cls(cookie, skey, hkey, pflags, cmask, amask)
			return pkm


class ReadBuf(object):
	def __init__(self, data=None):
		super(ReadBuf, self).__init__()
		self._buf = io.BytesIO(data) if data else io.BytesIO()
		self._len = len(data) if data else 0
	
	@property
	def unread_len(self):
		return self._len - self._buf.tell()
	
	def read(self, size):
		return self._buf.read(size)
	
	def read_byte(self):
		return struct.unpack('B', self.read(1))[0]
	
	def read_bool(self):
		return self.read_byte() != 0
	
	def read_int(self):
		return struct.unpack('>I', self.read(4))[0]
	
	def read_list(self):
		list_size = self.read_int()
		return self.read(list_size).decode().split(',')
	
	def read_string(self):
		n = self.read_int()
		return self.read(n)
	
	@classmethod
	def _parse_mpint(cls, v, pad, sf):
		r = 0
		if len(v) % 4:
			v = pad * (4 - (len(v) % 4)) + v
		for i in range(0, len(v), 4):
			r = (r << 32) | struct.unpack(sf, v[i:i + 4])[0]
		return r
		
	def read_mpint1(self):
		# NOTE: Data Type Enc @ http://www.snailbook.com/docs/protocol-1.5.txt
		bits = struct.unpack('>H', self.read(2))[0]
		n = (bits + 7) // 8
		return self._parse_mpint(self.read(n), b'\x00', '>I')
	
	def read_mpint2(self):
		# NOTE: Section 5 @ https://www.ietf.org/rfc/rfc4251.txt
		v = self.read_string()
		if len(v) == 0:
			return 0
		pad, sf = (b'\xff', '>i') if ord(v[0:1]) & 0x80 else (b'\x00', '>I')
		return self._parse_mpint(v, pad, sf)
	
	def read_line(self):
		return self._buf.readline().rstrip().decode('utf-8')


class WriteBuf(object):
	def __init__(self, data=None):
		super(WriteBuf, self).__init__()
		self._wbuf = io.BytesIO(data) if data else io.BytesIO()
	
	def write(self, data):
		self._wbuf.write(data)
		return self
	
	def write_byte(self, v):
		return self.write(struct.pack('B', v))
	
	def write_bool(self, v):
		return self.write_byte(1 if v else 0)
	
	def write_int(self, v):
		return self.write(struct.pack('>I', v))
	
	def write_string(self, v):
		if not isinstance(v, bytes):
			v = bytes(bytearray(v, 'utf-8'))
		self.write_int(len(v))
		return self.write(v)
	
	def write_list(self, v):
		self.write_string(u','.join(v))
	
	@classmethod
	def _bitlength(cls, n):
		try:
			return n.bit_length()
		except AttributeError:
			return len(bin(n)) - (2 if n > 0 else 3)
		
	@classmethod
	def _create_mpint(cls, n, signed=True, bits=None):
		if bits is None:
			bits = cls._bitlength(n)
		length = bits // 8 + (1 if n != 0 else 0)
		ql = (length + 7) // 8
		fmt, v2 = '>{0}Q'.format(ql), [b'\x00'] * ql
		for i in range(ql):
			v2[ql - i - 1] = (n & 0xffffffffffffffff)
			n >>= 64
		data = bytes(struct.pack(fmt, *v2)[-length:])
		if not signed:
			data = data.lstrip(b'\x00')
		elif data.startswith(b'\xff\x80'):
			data = data[1:]
		return data
	
	def write_mpint1(self, n):
		# NOTE: Data Type Enc @ http://www.snailbook.com/docs/protocol-1.5.txt
		bits = self._bitlength(n)
		data = self._create_mpint(n, False, bits)
		self.write(struct.pack('>H', bits))
		return self.write(data)
	
	def write_mpint2(self, n):
		# NOTE: Section 5 @ https://www.ietf.org/rfc/rfc4251.txt
		data = self._create_mpint(n)
		return self.write_string(data)
	
	def write_flush(self):
		payload = self._wbuf.getvalue()
		self._wbuf.truncate(0)
		self._wbuf.seek(0)
		return payload


class SSH(object):
	class Protocol(object):
		SMSG_PUBLIC_KEY = 2
		MSG_KEXINIT     = 20
		MSG_NEWKEYS     = 21
		MSG_KEXDH_INIT  = 30
		MSG_KEXDH_REPLY = 32
	
	class Product(object):
		OpenSSH = 'OpenSSH'
		DropbearSSH = 'Dropbear SSH'
	
	class Software(object):
		def __init__(self, vendor, product, version, patch, os):
			self.__vendor = vendor
			self.__product = product
			self.__version = version
			self.__patch = patch
			self.__os = os
		
		@property
		def vendor(self):
			return self.__vendor
		
		@property
		def product(self):
			return self.__product
		
		@property
		def version(self):
			return self.__version
		
		@property
		def patch(self):
			return self.__patch
		
		@property
		def os(self):
			return self.__os
		
		def compare_version(self, other):
			if other is None:
				return 1
			if isinstance(other, self.__class__):
				other = '{0}{1}'.format(other.version, other.patch)
			else:
				other = str(other)
			mx = re.match(r'^([\d\.]+\d+)(.*)$', other)
			if mx:
				oversion, opatch = mx.group(1), mx.group(2).strip()
			else:
				oversion, opatch = other, ''
			if self.version < oversion:
				return -1
			elif self.version > oversion:
				return 1
			spatch = self.patch
			if self.product == SSH.Product.DropbearSSH:
				if not re.match(r'^test\d.*$', opatch):
					opatch = 'z{0}'.format(opatch)
				if not re.match(r'^test\d.*$', self.patch):
					spatch = 'z{0}'.format(self.patch)
			elif self.product == SSH.Product.OpenSSH:
				mx1 = re.match(r'^p\d(.*)', opatch)
				mx2 = re.match(r'^p\d(.*)', self.patch)
				if not (mx1 and mx2):
					if mx1:
						opatch = mx1.group(1)
					if mx2:
						spatch = mx2.group(1)
			if spatch < opatch:
				return -1
			elif spatch > opatch:
				return 1
			return 0
		
		def between_versions(self, vfrom, vtill):
			if vfrom and self.compare_version(vfrom) < 0:
				return False
			if vtill and self.compare_version(vtill) > 0:
				return False
			return True
		
		def __str__(self):
			out = '{0} '.format(self.vendor) if self.vendor else ''
			out += self.product
			if self.version:
				out += ' {0}'.format(self.version)
			patch = self.patch
			if self.product == SSH.Product.OpenSSH:
				mx = re.match('^(p\d)(.*)$', self.patch)
				if mx is not None:
					out += mx.group(1)
					patch = mx.group(2).strip()
			if patch:
				out += ' ({0})'.format(self.patch)
			if self.os:
				out += ' running on {0}'.format(self.os)
			return out
		
		def __repr__(self):
			out = 'vendor={0} '.format(self.vendor) if self.vendor else ''
			if self.product:
				if self.vendor:
					out += ', '
				out += 'product={0}'.format(self.product)
			if self.version:
				out += ', version={0}'.format(self.version)
			if self.patch:
				out += ', patch={0}'.format(self.patch)
			if self.os:
				out += ', os={0}'.format(self.os)
			return '<{0}({1})>'.format(self.__class__.__name__, out)
		
		@staticmethod
		def _fix_patch(patch):
			return re.sub(r'^[-_\.]+', '', patch)
		
		@staticmethod
		def _fix_date(d):
			if d is not None and len(d) == 8:
				return '{0}-{1}-{2}'.format(d[:4], d[4:6], d[6:8])
			else:
				return None
		
		@classmethod
		def _extract_os(cls, c):
			if c is None:
				return None
			mx = re.match(r'^NetBSD(?:_Secure_Shell)?(?:[\s-]+(\d{8})(.*))?$', c)
			if mx:
				d = cls._fix_date(mx.group(1))
				return 'NetBSD' if d is None else 'NetBSD ({0})'.format(d)
			mx = re.match(r'^FreeBSD(?:\slocalisations)?[\s-]+(\d{8})(.*)$', c)
			if not mx:
				mx = re.match(r'^[^@]+@FreeBSD\.org[\s-]+(\d{8})(.*)$', c)
			if mx:
				d = cls._fix_date(mx.group(1))
				return 'FreeBSD' if d is None else 'FreeBSD ({0})'.format(d)
			w = ['RemotelyAnywhere', 'DesktopAuthority', 'RemoteSupportManager']
			for win_soft in w:
				mx = re.match(r'^in ' + win_soft + ' ([\d\.]+\d)$', c)
				if mx:
					ver = mx.group(1)
					return 'Microsoft Windows ({0} {1})'.format(win_soft, ver)
			generic = ['NetBSD', 'FreeBSD']
			for g in generic:
				if c.startswith(g) or c.endswith(g):
					return g
			return None
		
		@classmethod
		def parse(cls, banner):
			software = str(banner.software)
			mx = re.match(r'^dropbear_([\d\.]+\d+)(.*)', software)
			if mx:
				patch = cls._fix_patch(mx.group(2))
				v, p = 'Matt Johnston', SSH.Product.DropbearSSH
				v = None
				return cls(v, p, mx.group(1), patch, None)
			mx = re.match(r'^OpenSSH[_\.-]+([\d\.]+\d+)(.*)', software)
			if mx:
				patch = cls._fix_patch(mx.group(2))
				v, p = 'OpenBSD', SSH.Product.OpenSSH
				v = None
				os = cls._extract_os(banner.comments)
				return cls(v, p, mx.group(1), patch, os)
			mx = re.match(r'^RomSShell_([\d\.]+\d+)(.*)', software)
			if mx:
				patch = cls._fix_patch(mx.group(2))
				v, p = 'Allegro Software', 'RomSShell'
				return cls(v, p, mx.group(1), patch, None)
			mx = re.match(r'^mpSSH_([\d\.]+\d+)', software)
			if mx:
				v, p = 'HP', 'iLO (Integrated Lights-Out) sshd'
				return cls(v, p, mx.group(1), None, None)
			mx = re.match(r'^Cisco-([\d\.]+\d+)', software)
			if mx:
				v, p = 'Cisco', 'IOS/PIX sshd'
				return cls(v, p, mx.group(1), None, None)
			return None
	
	class Banner(object):
		_RXP, _RXR = r'SSH-\d\.\s*?\d+', r'(-([^\s]*)(?:\s+(.*))?)?'
		RX_PROTOCOL = re.compile(_RXP.replace('\d', '(\d)'))
		RX_BANNER = re.compile(r'^({0}(?:(?:-{0})*)){1}$'.format(_RXP, _RXR))
		
		def __init__(self, protocol, software, comments):
			self.__protocol = protocol
			self.__software = software
			self.__comments = comments
		
		@property
		def protocol(self):
			return self.__protocol
		
		@property
		def software(self):
			return self.__software
		
		@property
		def comments(self):
			return self.__comments
		
		def __str__(self):
			out = 'SSH-{0}.{1}'.format(self.protocol[0], self.protocol[1])
			if self.software is not None:
				out += '-{0}'.format(self.software)
			if self.comments:
				out += ' {0}'.format(self.comments)
			return out
		
		def __repr__(self):
			p = '{0}.{1}'.format(self.protocol[0], self.protocol[1])
			out = 'protocol={0}'.format(p)
			if self.software:
				out += ', software={0}'.format(self.software)
			if self.comments:
				out += ', comments={0}'.format(self.comments)
			return '<{0}({1})>'.format(self.__class__.__name__, out)
		
		@classmethod
		def parse(cls, banner):
			mx = cls.RX_BANNER.match(banner)
			if mx is None:
				return None
			protocol = min(re.findall(cls.RX_PROTOCOL, mx.group(1)))
			protocol = (int(protocol[0]), int(protocol[1]))
			software = (mx.group(3) or '').strip() or None
			if software is None and (mx.group(2) or '').startswith('-'):
				software = ''
			comments = (mx.group(4) or '').strip() or None
			return cls(protocol, software, comments)
	
	class Fingerprint(object):
		def __init__(self, fpd):
			self.__fpd = fpd
		
		@property
		def md5(self):
			h = hashlib.md5(self.__fpd).hexdigest()
			h = u':'.join(h[i:i + 2] for i in range(0, len(h), 2))
			return u'MD5:{0}'.format(h)
		
		@property
		def sha256(self):
			h = base64.b64encode(hashlib.sha256(self.__fpd).digest())
			h = h.decode().rstrip('=')
			return u'SHA256:{0}'.format(h)
	
	class Security(object):
		CVE = {
			'Dropbear SSH': [
				['0.44', '2015.71', 1, 'CVE-2016-3116', 5.5, 'bypass command restrictions via xauth command injection.'],
				['0.28', '2013.58', 1, 'CVE-2013-4434', 5.0, 'discover valid usernames through different time delays.'],
				['0.28', '2013.58', 1, 'CVE-2013-4421', 5.0, 'cause DoS (memory consumption) via a compressed packet.'],
				['0.52', '2011.54', 1, 'CVE-2012-0920', 7.1, 'execute arbitrary code or bypass command restrictions.'],
				['0.40', '0.48.1',  1, 'CVE-2007-1099', 7.5, 'conduct a MitM attack (no warning for hostkey mismatch).'],
				['0.28', '0.47',    1, 'CVE-2006-1206', 7.5, 'cause DoS (slot exhaustion) via large number of connections.'],
				['0.39', '0.47',    1, 'CVE-2006-0225', 4.6, 'execute arbitrary commands via scp with crafted filenames.'],
				['0.28', '0.46',    1, 'CVE-2005-4178', 6.5, 'execute arbitrary code via buffer overflow vulnerability.'],
				['0.28', '0.42',    1, 'CVE-2004-2486', 7.5, 'execute arbitrary code via DSS verification code.'],
			]
		}
		TXT = {
			'Dropbear SSH': [
				['0.28', '0.34', 1, 'remote root exploit', 'remote format string buffer overflow exploit (exploit-db#387).'],
			]
		}
	
	class Socket(ReadBuf, WriteBuf):
		class InsufficientReadException(Exception):
			pass
		
		SM_BANNER_SENT = 1
		
		def __init__(self, host, port, cto=3.0, rto=5.0):
			self.__block_size = 8
			self.__state = 0
			self.__header = []
			self.__banner = None
			super(SSH.Socket, self).__init__()
			try:
				self.__sock = socket.create_connection((host, port), cto)
				self.__sock.settimeout(rto)
			except Exception as e:
				out.fail('[fail] {0}'.format(e))
				sys.exit(1)
		
		def __enter__(self):
			return self
		
		def get_banner(self, sshv=2):
			banner = 'SSH-{0}-OpenSSH_7.3'.format('1.5' if sshv == 1 else '2.0')
			rto = self.__sock.gettimeout()
			self.__sock.settimeout(0.7)
			s, e = self.recv()
			self.__sock.settimeout(rto)
			if s < 0:
				return self.__banner, self.__header
			if self.__state < self.SM_BANNER_SENT:
				self.send_banner(banner)
			while self.__banner is None:
				if not s > 0:
					s, e = self.recv()
					if s < 0:
						break
				while self.__banner is None and self.unread_len > 0:
					line = self.read_line()
					if len(line.strip()) == 0:
						continue
					if self.__banner is None:
						self.__banner = SSH.Banner.parse(line)
						if self.__banner is not None:
							continue
					self.__header.append(line)
				s = 0
			return self.__banner, self.__header
		
		def recv(self, size=2048):
			try:
				data = self.__sock.recv(size)
			except socket.timeout as e:
				r = 0 if e.strerror == 'timed out' else -1
				return (r, e)
			except socket.error as e:
				r = 0 if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK) else -1
				return (r, e)
			if len(data) == 0:
				return (-1, None)
			pos = self._buf.tell()
			self._buf.seek(0, 2)
			self._buf.write(data)
			self._len += len(data)
			self._buf.seek(pos, 0)
			return (len(data), None)
		
		def send(self, data):
			try:
				self.__sock.send(data)
				return (0, None)
			except socket.error as e:
				return (-1, e)
			self.__sock.send(data)
		
		def send_banner(self, banner):
			self.send(banner.encode() + b'\r\n')
			if self.__state < self.SM_BANNER_SENT:
				self.__state = self.SM_BANNER_SENT
		
		def ensure_read(self, size):
			while self.unread_len < size:
				s, e = self.recv()
				if s < 0:
					raise SSH.Socket.InsufficientReadException(e)
		
		def read_packet(self, sshv=2):
			try:
				header = WriteBuf()
				self.ensure_read(4)
				packet_length = self.read_int()
				header.write_int(packet_length)
				# XXX: validate length
				if sshv == 1:
					padding_length = (8 - packet_length % 8)
					self.ensure_read(padding_length)
					padding = self.read(padding_length)
					header.write(padding)
					payload_length = packet_length
					check_size = padding_length + payload_length
				else:
					self.ensure_read(1)
					padding_length = self.read_byte()
					header.write_byte(padding_length)
					payload_length = packet_length - padding_length - 1
					check_size = 4 + 1 + payload_length + padding_length
				if check_size % self.__block_size != 0:
					out.fail('[exception] invalid ssh packet (block size)')
					sys.exit(1)
				self.ensure_read(payload_length)
				if sshv == 1:
					payload = self.read(payload_length - 4)
					header.write(payload)
					crc = self.read_int()
					header.write_int(crc)
				else:
					payload = self.read(payload_length)
					header.write(payload)
				packet_type = ord(payload[0:1])
				if sshv == 1:
					rcrc = SSH1.crc32(padding + payload)
					if crc != rcrc:
						out.fail('[exception] packet checksum CRC32 mismatch.')
						sys.exit(1)
				else:
					self.ensure_read(padding_length)
					padding = self.read(padding_length)
				payload = payload[1:]
				return packet_type, payload
			except SSH.Socket.InsufficientReadException as ex:
				if ex.args[0] is None:
					header.write(self.read(self.unread_len))
					e = header.write_flush().strip()
				else:
					e = ex.args[0]
				return (-1, e)
		
		def send_packet(self):
			payload = self.write_flush()
			padding = -(len(payload) + 5) % 8
			if padding < 4:
				padding += 8
			plen = len(payload) + padding + 1
			pad_bytes = b'\x00' * padding
			data = struct.pack('>Ib', plen, padding) + payload + pad_bytes
			return self.send(data)
		
		def __del__(self):
			self.__cleanup()
		
		def __exit__(self, ex_type, ex_value, tb):
			self.__cleanup()
		
		def __cleanup(self):
			try:
				self.__sock.shutdown(socket.SHUT_RDWR)
				self.__sock.close()
			except:
				pass


class KexDH(object):
	def __init__(self, alg, g, p):
		self.__alg = alg
		self.__g = g
		self.__p = p
		self.__q = (self.__p - 1) // 2
		self.__x = None
	
	def send_init(self, s):
		r = random.SystemRandom()
		self.__x = r.randrange(2, self.__q)
		self.__e = pow(self.__g, self.__x, self.__p)
		s.write_byte(SSH.Protocol.MSG_KEXDH_INIT)
		s.write_mpint2(self.__e)
		s.send_packet()


class KexGroup1(KexDH):
	def __init__(self):
		# rfc2409: second oakley group
		p = int('ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd129024e088a67'
		        'cc74020bbea63b139b22514a08798e3404ddef9519b3cd3a431b302b0a6d'
		        'f25f14374fe1356d6d51c245e485b576625e7ec6f44c42e9a637ed6b0bff'
		        '5cb6f406b7edee386bfb5a899fa5ae9f24117c4b1fe649286651ece65381'
		        'ffffffffffffffff', 16)
		super(KexGroup1, self).__init__('sha1', 2, p)


class KexGroup14(KexDH):
	def __init__(self):
		# rfc3526: 2048-bit modp group
		p = int('ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd129024e088a67'
		        'cc74020bbea63b139b22514a08798e3404ddef9519b3cd3a431b302b0a6d'
		        'f25f14374fe1356d6d51c245e485b576625e7ec6f44c42e9a637ed6b0bff'
		        '5cb6f406b7edee386bfb5a899fa5ae9f24117c4b1fe649286651ece45b3d'
		        'c2007cb8a163bf0598da48361c55d39a69163fa8fd24cf5f83655d23dca3'
		        'ad961c62f356208552bb9ed529077096966d670c354e4abc9804f1746c08'
		        'ca18217c32905e462e36ce3be39e772c180e86039b2783a2ec07a28fb5c5'
		        '5df06f4c52c9de2bcbf6955817183995497cea956ae515d2261898fa0510'
		        '15728e5a8aacaa68ffffffffffffffff', 16)
		super(KexGroup14, self).__init__('sha1', 2, p)


class KexDB(object):
	WARN_OPENSSH72_LEGACY = 'disabled (in client) since OpenSSH 7.2, legacy algorithm'
	FAIL_OPENSSH70_LEGACY = 'removed since OpenSSH 7.0, legacy algorithm'
	FAIL_OPENSSH70_WEAK   = 'removed (in server) and disabled (in client) since OpenSSH 7.0, weak algorithm'
	FAIL_OPENSSH70_LOGJAM = 'disabled (in client) since OpenSSH 7.0, logjam attack'
	INFO_OPENSSH69_CHACHA = 'default cipher since OpenSSH 6.9.'
	FAIL_OPENSSH67_UNSAFE = 'removed (in server) since OpenSSH 6.7, unsafe algorithm'
	FAIL_OPENSSH61_REMOVE = 'removed since OpenSSH 6.1, removed from specification'
	FAIL_OPENSSH31_REMOVE = 'removed since OpenSSH 3.1'
	FAIL_DBEAR67_DISABLED = 'disabled since Dropbear SSH 2015.67'
	FAIL_DBEAR53_DISABLED = 'disabled since Dropbear SSH 0.53'
	FAIL_PLAINTEXT        = 'no encryption/integrity'
	WARN_CURVES_WEAK      = 'using weak elliptic curves'
	WARN_RNDSIG_KEY       = 'using weak random number generator could reveal the key'
	WARN_MODULUS_SIZE     = 'using small 1024-bit modulus'
	WARN_MODULUS_CUSTOM   = 'using custom size modulus (possibly weak)'
	WARN_HASH_WEAK        = 'using weak hashing algorithm'
	WARN_CIPHER_MODE      = 'using weak cipher mode'
	WARN_BLOCK_SIZE       = 'using small 64-bit block size'
	WARN_CIPHER_WEAK      = 'using weak cipher'
	WARN_ENCRYPT_AND_MAC  = 'using encrypt-and-MAC mode'
	WARN_TAG_SIZE         = 'using small 64-bit tag size'

	ALGORITHMS = {
		'kex': {
			'diffie-hellman-group1-sha1': [['2.3.0,d0.28', '6.6', '6.9'], [FAIL_OPENSSH67_UNSAFE, FAIL_OPENSSH70_LOGJAM], [WARN_MODULUS_SIZE, WARN_HASH_WEAK]],
			'diffie-hellman-group14-sha1': [['3.9,d0.53'], [], [WARN_HASH_WEAK]],
			'diffie-hellman-group14-sha256': [['7.3,d2016.73']],
			'diffie-hellman-group16-sha512': [['7.3,d2016.73']],
			'diffie-hellman-group18-sha512': [['7.3']],
			'diffie-hellman-group-exchange-sha1': [['2.3.0', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [WARN_HASH_WEAK]],
			'diffie-hellman-group-exchange-sha256': [['4.4'], [], [WARN_MODULUS_CUSTOM]],
			'ecdh-sha2-nistp256': [['5.7,d2013.62'], [WARN_CURVES_WEAK]],
			'ecdh-sha2-nistp384': [['5.7,d2013.62'], [WARN_CURVES_WEAK]],
			'ecdh-sha2-nistp521': [['5.7,d2013.62'], [WARN_CURVES_WEAK]],
			'curve25519-sha256@libssh.org': [['6.5,d2013.62']],
			'kexguess2@matt.ucc.asn.au': [['d2013.57']],
		},
		'key': {
			'rsa-sha2-256': [['7.2']],
			'rsa-sha2-512': [['7.2']],
			'ssh-ed25519': [['6.5']],
			'ssh-ed25519-cert-v01@openssh.com': [['6.5']],
			'ssh-rsa': [['2.5.0,d0.28']],
			'ssh-dss': [['2.1.0,d0.28', '6.9'], [FAIL_OPENSSH70_WEAK], [WARN_MODULUS_SIZE, WARN_RNDSIG_KEY]],
			'ecdsa-sha2-nistp256': [['5.7,d2013.62'], [WARN_CURVES_WEAK], [WARN_RNDSIG_KEY]],
			'ecdsa-sha2-nistp384': [['5.7,d2013.62'], [WARN_CURVES_WEAK], [WARN_RNDSIG_KEY]],
			'ecdsa-sha2-nistp521': [['5.7,d2013.62'], [WARN_CURVES_WEAK], [WARN_RNDSIG_KEY]],
			'ssh-rsa-cert-v00@openssh.com': [['5.4', '6.9'], [FAIL_OPENSSH70_LEGACY], []],
			'ssh-dss-cert-v00@openssh.com': [['5.4', '6.9'], [FAIL_OPENSSH70_LEGACY], [WARN_MODULUS_SIZE, WARN_RNDSIG_KEY]],
			'ssh-rsa-cert-v01@openssh.com': [['5.6']],
			'ssh-dss-cert-v01@openssh.com': [['5.6', '6.9'], [FAIL_OPENSSH70_WEAK], [WARN_MODULUS_SIZE, WARN_RNDSIG_KEY]],
			'ecdsa-sha2-nistp256-cert-v01@openssh.com': [['5.7'], [WARN_CURVES_WEAK], [WARN_RNDSIG_KEY]],
			'ecdsa-sha2-nistp384-cert-v01@openssh.com': [['5.7'], [WARN_CURVES_WEAK], [WARN_RNDSIG_KEY]],
			'ecdsa-sha2-nistp521-cert-v01@openssh.com': [['5.7'], [WARN_CURVES_WEAK], [WARN_RNDSIG_KEY]],
		},
		'enc': {
			'none': [['1.2.2,d2013.56'], [FAIL_PLAINTEXT]],
			'3des-cbc': [['1.2.2,d0.28', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [WARN_CIPHER_WEAK, WARN_CIPHER_MODE, WARN_BLOCK_SIZE]],
			'3des-ctr': [['d0.52']],
			'blowfish-cbc': [['1.2.2,d0.28', '6.6,d0.52', '7.1,d0.52'], [FAIL_OPENSSH67_UNSAFE, FAIL_DBEAR53_DISABLED], [WARN_OPENSSH72_LEGACY, WARN_CIPHER_MODE, WARN_BLOCK_SIZE]],
			'twofish-cbc': [['d0.28', 'd2014.66'], [FAIL_DBEAR67_DISABLED], [WARN_CIPHER_MODE]],
			'twofish128-cbc': [['d0.47', 'd2014.66'], [FAIL_DBEAR67_DISABLED], [WARN_CIPHER_MODE]],
			'twofish256-cbc': [['d0.47', 'd2014.66'], [FAIL_DBEAR67_DISABLED], [WARN_CIPHER_MODE]],
			'twofish128-ctr': [['d2015.68']],
			'twofish256-ctr': [['d2015.68']],
			'cast128-cbc': [['2.1.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_CIPHER_MODE, WARN_BLOCK_SIZE]],
			'arcfour': [['2.1.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_CIPHER_WEAK]],
			'arcfour128': [['4.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_CIPHER_WEAK]],
			'arcfour256': [['4.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_CIPHER_WEAK]],
			'aes128-cbc': [['2.3.0,d0.28', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [WARN_CIPHER_MODE]],
			'aes192-cbc': [['2.3.0', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [WARN_CIPHER_MODE]],
			'aes256-cbc': [['2.3.0,d0.47', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [WARN_CIPHER_MODE]],
			'rijndael128-cbc': [['2.3.0', '3.0.2'], [FAIL_OPENSSH31_REMOVE], [WARN_CIPHER_MODE]],
			'rijndael192-cbc': [['2.3.0', '3.0.2'], [FAIL_OPENSSH31_REMOVE], [WARN_CIPHER_MODE]],
			'rijndael256-cbc': [['2.3.0', '3.0.2'], [FAIL_OPENSSH31_REMOVE], [WARN_CIPHER_MODE]],
			'rijndael-cbc@lysator.liu.se': [['2.3.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_CIPHER_MODE]],
			'aes128-ctr': [['3.7,d0.52']],
			'aes192-ctr': [['3.7']],
			'aes256-ctr': [['3.7,d0.52']],
			'aes128-gcm@openssh.com': [['6.2']],
			'aes256-gcm@openssh.com': [['6.2']],
			'chacha20-poly1305@openssh.com': [['6.5'], [], [], [INFO_OPENSSH69_CHACHA]],
		},
		'mac': {
			'none': [['d2013.56'], [FAIL_PLAINTEXT]],
			'hmac-sha1': [['2.1.0,d0.28'], [], [WARN_ENCRYPT_AND_MAC, WARN_HASH_WEAK]],
			'hmac-sha1-96': [['2.5.0,d0.47', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_ENCRYPT_AND_MAC, WARN_HASH_WEAK]],
			'hmac-sha2-256': [['5.9,d2013.56'], [], [WARN_ENCRYPT_AND_MAC]],
			'hmac-sha2-256-96': [['5.9', '6.0'], [FAIL_OPENSSH61_REMOVE], [WARN_ENCRYPT_AND_MAC]],
			'hmac-sha2-512': [['5.9,d2013.56'], [], [WARN_ENCRYPT_AND_MAC]],
			'hmac-sha2-512-96': [['5.9', '6.0'], [FAIL_OPENSSH61_REMOVE], [WARN_ENCRYPT_AND_MAC]],
			'hmac-md5': [['2.1.0,d0.28', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_ENCRYPT_AND_MAC, WARN_HASH_WEAK]],
			'hmac-md5-96': [['2.5.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_ENCRYPT_AND_MAC, WARN_HASH_WEAK]],
			'hmac-ripemd160': [['2.5.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_ENCRYPT_AND_MAC]],
			'hmac-ripemd160@openssh.com': [['2.1.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_ENCRYPT_AND_MAC]],
			'umac-64@openssh.com': [['4.7'], [], [WARN_ENCRYPT_AND_MAC, WARN_TAG_SIZE]],
			'umac-128@openssh.com': [['6.2'], [], [WARN_ENCRYPT_AND_MAC]],
			'hmac-sha1-etm@openssh.com': [['6.2'], [], [WARN_HASH_WEAK]],
			'hmac-sha1-96-etm@openssh.com': [['6.2', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [WARN_HASH_WEAK]],
			'hmac-sha2-256-etm@openssh.com': [['6.2']],
			'hmac-sha2-512-etm@openssh.com': [['6.2']],
			'hmac-md5-etm@openssh.com': [['6.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_HASH_WEAK]],
			'hmac-md5-96-etm@openssh.com': [['6.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, WARN_HASH_WEAK]],
			'hmac-ripemd160-etm@openssh.com': [['6.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY]],
			'umac-64-etm@openssh.com': [['6.2'], [], [WARN_TAG_SIZE]],
			'umac-128-etm@openssh.com': [['6.2']],
		}
	}


def get_ssh_version(version_desc):
	if version_desc.startswith('d'):
		return (SSH.Product.DropbearSSH, version_desc[1:])
	else:
		return (SSH.Product.OpenSSH, version_desc)


def get_alg_timeframe(alg_desc, for_server=True, result={}):
	versions = alg_desc[0]
	vlen = len(versions)
	for i in range(3):
		if i > vlen - 1:
			if i == 2 and vlen > 1:
				cversions = versions[1]
			else:
				continue
		else:
			cversions = versions[i]
		if cversions is None:
			continue
		for v in cversions.split(','):
			ssh_prefix, ssh_version = get_ssh_version(v)
			if not ssh_version:
				continue
			if ssh_version.endswith('C'):
				if for_server:
					continue
				ssh_version = ssh_version[:-1]
			if ssh_prefix not in result:
				result[ssh_prefix] = [None, None, None]
			prev, push = result[ssh_prefix][i], False
			if prev is None:
				push = True
			elif i == 0 and prev < ssh_version:
				push = True
			elif i > 0 and prev > ssh_version:
				push = True
			if push:
				result[ssh_prefix][i] = ssh_version
	return result


def get_ssh_timeframe(alg_pairs, for_server=True):
	timeframe = {}
	for alg_pair in alg_pairs:
		alg_db, algs = alg_pair
		for alg_type, alg_list in algs.items():
			for alg_name in alg_list:
				alg_desc = alg_db[alg_type].get(alg_name)
				if alg_desc is None:
					continue
				timeframe = get_alg_timeframe(alg_desc, for_server, timeframe)
	return timeframe


def get_alg_since_text(alg_desc):
	tv = []
	versions = alg_desc[0]
	if len(versions) == 0 or versions[0] is None:
		return None
	for v in versions[0].split(','):
		ssh_prefix, ssh_version = get_ssh_version(v)
		if not ssh_version:
			continue
		if ssh_version.endswith('C'):
			ssh_version = '{0} (client only)'.format(ssh_version[:-1])
		tv.append('{0} {1}'.format(ssh_prefix, ssh_version))
	if len(tv) == 0:
		return None
	return 'available since ' + ', '.join(tv).rstrip(', ')


def output_algorithms(title, alg_db, alg_type, algorithms, maxlen=0):
	with OutputBuffer() as obuf:
		for algorithm in algorithms:
			output_algorithm(alg_db, alg_type, algorithm, maxlen)
	if len(obuf) > 0:
		out.head('# ' + title)
		obuf.flush()
		out.sep()


def output_algorithm(alg_db, alg_type, alg_name, alg_max_len=0):
	prefix = '(' + alg_type + ') '
	if alg_max_len == 0:
		alg_max_len = len(alg_name)
	padding = '' if out.batch else ' ' * (alg_max_len - len(alg_name))
	texts = []
	if alg_name in alg_db[alg_type]:
		alg_desc = alg_db[alg_type][alg_name]
		ldesc = len(alg_desc)
		for idx, level in enumerate(['fail', 'warn', 'info']):
			if level == 'info':
				since_text = get_alg_since_text(alg_desc)
				if since_text:
					texts.append((level, since_text))
			idx = idx + 1
			if ldesc > idx:
				for t in alg_desc[idx]:
					texts.append((level, t))
		if len(texts) == 0:
			texts.append(('info', ''))
	else:
		texts.append(('warn', 'unknown algorithm'))
	first = True
	for (level, text) in texts:
		f = getattr(out, level)
		text = '[' + level + '] ' + text
		if first:
			if first and level == 'info':
				f = out.good
			f(prefix + alg_name + padding + ' -- ' + text)
			first = False
		else:
			if out.verbose:
				f(prefix + alg_name + padding + ' -- ' + text)
			else:
				f(' ' * len(prefix + alg_name) + padding + ' `- ' + text)


def output_compatibility(kex, pkm, for_server=True):
	alg_pairs = []
	if pkm is not None:
		alg_pairs.append((SSH1.KexDB.ALGORITHMS,
		                  {'key': ['ssh-rsa1'],
		                   'enc': pkm.supported_ciphers,
		                   'aut': pkm.supported_authentications}))
	if kex is not None:
		alg_pairs.append((KexDB.ALGORITHMS,
		                  {'kex': kex.kex_algorithms,
		                   'key': kex.key_algorithms,
		                   'enc': kex.server.encryption,
		                   'mac': kex.server.mac}))
	ssh_timeframe = get_ssh_timeframe(alg_pairs, for_server)
	vp = 1 if for_server else 2
	comp_text = []
	for sshd_name in [SSH.Product.OpenSSH, SSH.Product.DropbearSSH]:
		if sshd_name not in ssh_timeframe:
			continue
		v = ssh_timeframe[sshd_name]
		if v[vp] is None:
			comp_text.append('{0} {1}+'.format(sshd_name, v[0]))
		elif v[0] == v[vp]:
			comp_text.append('{0} {1}'.format(sshd_name, v[0]))
		else:
			if v[vp] < v[0]:
				tfmt = '{0} {1}+ (some functionality from {2})'
			else:
				tfmt = '{0} {1}-{2}'
			comp_text.append(tfmt.format(sshd_name, v[0], v[vp]))
	if len(comp_text) > 0:
		out.good('(gen) compatibility: ' + ', '.join(comp_text))


def output_security_sub(sub, software, padlen):
	secdb = SSH.Security.CVE if sub == 'cve' else SSH.Security.TXT
	if software is None or software.product not in secdb:
		return
	for line in secdb[software.product]:
		vfrom, vtill = line[0:2]
		if not software.between_versions(vfrom, vtill):
			continue
		target, name = line[2:4]
		is_server, is_client = target & 1 == 1, target & 2 == 2
		if is_client:
			continue
		p = '' if out.batch else ' ' * (padlen - len(name))
		if sub == 'cve':
			cvss, descr = line[4:6]
			out.fail('(cve) {0}{1} -- ({2}) {3}'.format(name, p, cvss, descr))
		else:
			descr = line[4]
			out.fail('(sec) {0}{1} -- {2}'.format(name, p, descr))


def output_security(banner, padlen):
	with OutputBuffer() as obuf:
		if banner:
			software = SSH.Software.parse(banner)
			output_security_sub('cve', software, padlen)
			output_security_sub('txt', software, padlen)
	if len(obuf) > 0:
		out.head('# security')
		obuf.flush()
		out.sep()


def output_fingerprint(kex, pkm, sha256=True, padlen=0):
	with OutputBuffer() as obuf:
		fps = []
		if pkm is not None:
			name = 'ssh-rsa1'
			fp = SSH.Fingerprint(pkm.host_key_fingerprint_data)
			bits = pkm.host_key_bits
			fps.append((name, fp, bits))
		for fpp in fps:
			name, fp, bits = fpp
			fp = fp.sha256 if sha256 else fp.md5
			p = '' if out.batch else ' ' * (padlen - len(name))
			out.good('(fin) {0}{1} -- {2} {3}'.format(name, p, bits, fp))
	if len(obuf) > 0:
		out.head('# fingerprints')
		obuf.flush()
		out.sep()


def output(banner, header, kex=None, pkm=None):
	sshv = 1 if pkm else 2
	with OutputBuffer() as obuf:
		if len(header) > 0:
			out.info('(gen) header: ' + '\n'.join(header))
		if banner is not None:
			out.good('(gen) banner: {0}'.format(banner))
			if sshv == 1 or banner.protocol[0] == 1:
				out.fail('(gen) protocol SSH1 enabled')
			software = SSH.Software.parse(banner)
			if software is not None:
				out.good('(gen) software: {0}'.format(software))
		output_compatibility(kex, pkm)
		if kex is not None:
			compressions = [x for x in kex.server.compression if x != 'none']
			if len(compressions) > 0:
				cmptxt = 'enabled ({0})'.format(', '.join(compressions))
			else:
				cmptxt = 'disabled'
			out.good('(gen) compression: {0}'.format(cmptxt))
	if len(obuf) > 0:
		out.head('# general')
		obuf.flush()
		out.sep()
	ml, maxlen = lambda l: max(len(i) for i in l), 0
	if pkm is not None:
		maxlen = max(ml(pkm.supported_ciphers),
		             ml(pkm.supported_authentications),
		             maxlen)
	if kex is not None:
		maxlen = max(ml(kex.kex_algorithms),
		             ml(kex.key_algorithms),
		             ml(kex.server.encryption),
		             ml(kex.server.mac),
		             maxlen)
	output_security(banner, maxlen)
	if pkm is not None:
		adb = SSH1.KexDB.ALGORITHMS
		ciphers = pkm.supported_ciphers
		auths = pkm.supported_authentications
		title, atype = 'SSH1 host-key algorithms', 'key'
		output_algorithms(title, adb, atype, ['ssh-rsa1'], maxlen)
		title, atype = 'SSH1 encryption algorithms (ciphers)', 'enc'
		output_algorithms(title, adb, atype, ciphers, maxlen)
		title, atype = 'SSH1 authentication types', 'aut'
		output_algorithms(title, adb, atype, auths, maxlen)
	if kex is not None:
		adb = KexDB.ALGORITHMS
		title, atype = 'key exchange algorithms', 'kex'
		output_algorithms(title, adb, atype, kex.kex_algorithms, maxlen)
		title, atype = 'host-key algorithms', 'key'
		output_algorithms(title, adb, atype, kex.key_algorithms, maxlen)
		title, atype = 'encryption algorithms (ciphers)', 'enc'
		output_algorithms(title, adb, atype, kex.server.encryption, maxlen)
		title, atype = 'message authentication code algorithms', 'mac'
		output_algorithms(title, adb, atype, kex.server.mac, maxlen)
	output_fingerprint(kex, pkm, True, maxlen)


def parse_int(v):
	try:
		return int(v)
	except:
		return 0


def parse_args():
	conf = AuditConf()
	try:
		sopts = 'h12bnvl:'
		lopts = ['help', 'ssh1', 'ssh2', 'batch', 'no-colors', 'verbose', 'level=']
		opts, args = getopt.getopt(sys.argv[1:], sopts, lopts)
	except getopt.GetoptError as err:
		usage(str(err))
	for o, a in opts:
		if o in ('-h', '--help'):
			usage()
		elif o in ('-1', '--ssh1'):
			conf.ssh1 = True
		elif o in ('-2', '--ssh2'):
			conf.ssh2 = True
		elif o in ('-b', '--batch'):
			out.batch = True
			out.verbose = True
		elif o in ('-n', '--no-colors'):
			out.colors = False
		elif o in ('-v', '--verbose'):
			out.verbose = True
		elif o in ('-l', '--level'):
			if a not in ('info', 'warn', 'fail'):
				usage('level ' + a + ' is not valid')
			out.minlevel = a
	if len(args) == 0:
		usage()
	s = args[0].split(':')
	host, port = s[0].strip(), 22
	if len(s) > 1:
		port = parse_int(s[1])
	if not host or port <= 0:
		usage('port {0} is not valid'.format(port))
	conf.host = host
	conf.port = port
	if not (conf.ssh1 or conf.ssh2):
		conf.ssh1 = True
		conf.ssh2 = True
	return conf


def audit(conf, sshv=None):
	s = SSH.Socket(conf.host, conf.port)
	if sshv is None:
		sshv = 2 if conf.ssh2 else 1
	err = None
	banner, header = s.get_banner(sshv)
	if banner is None:
		err = '[exception] did not receive banner.'
	if err is None:
		packet_type, payload = s.read_packet(sshv)
		if packet_type < 0:
			if payload == b'Protocol major versions differ.':
				if sshv == 2 and conf.ssh1:
					audit(conf, 1)
					return
			err = '[exception] error reading packet ({0})'.format(payload)
		else:
			if sshv == 1 and packet_type != SSH.Protocol.SMSG_PUBLIC_KEY:
				err = ('SMSG_PUBLIC_KEY', SSH.Protocol.SMSG_PUBLIC_KEY)
			elif sshv == 2 and packet_type != SSH.Protocol.MSG_KEXINIT:
				err = ('MSG_KEXINIT', SSH.Protocol.MSG_KEXINIT)
			if err is not None:
				fmt = '[exception] did not receive {0} ({1}), ' + \
				      'instead received unknown message ({2})'
				err = fmt.format(err[0], err[1], packet_type)
	if err:
		output(banner, header)
		out.fail(err)
		sys.exit(1)
	if sshv == 1:
		pkm = SSH1.PublicKeyMessage.parse(payload)
		output(banner, header, pkm=pkm)
	elif sshv == 2:
		kex = Kex.parse(payload)
		output(banner, header, kex=kex)


if __name__ == '__main__':
	out = Output()
	conf = parse_args()
	audit(conf)
