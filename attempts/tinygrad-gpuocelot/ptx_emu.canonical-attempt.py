"""A pure-Python PTX emulator, replacing the native gpuocelot dependency.

mockgpu's NV and CUDA paths execute kernels through libgpuocelot, a fork of a
Georgia Tech project that has been unmaintained since roughly 2011. It has to be
downloaded as a prebuilt binary in CI and built from source with cmake, ninja and
bison on macOS, and when it is absent the mock does not fail — it segfaults with
no message, because the ctypes binding is lazy.

This replaces it the way the AMD side already works: interpret in Python, with no
native dependency. The surface is bounded by what tinygrad's own PTX renderer
emits (asm_for_op in tinygrad/renderer/ptx.py, 233 lines), not by the PTX ISA.

Threads are generators so that bar.sync can suspend them: every thread in a block
runs to the barrier, then all resume. Straight-line kernels never yield.
"""

import bisect
import ctypes
import re
import struct
from collections.abc import Iterable
from typing import Any

# PTX type -> (ctypes type, byte width, is_signed_int, is_float)
_TYPES: dict[str, tuple[Any, int, bool, bool]] = {
    "s8":  (ctypes.c_int8,   1, True,  False), "u8":  (ctypes.c_uint8,  1, False, False),
    "s16": (ctypes.c_int16,  2, True,  False), "u16": (ctypes.c_uint16, 2, False, False),
    "s32": (ctypes.c_int32,  4, True,  False), "u32": (ctypes.c_uint32, 4, False, False),
    "s64": (ctypes.c_int64,  8, True,  False), "u64": (ctypes.c_uint64, 8, False, False),
    "b8":  (ctypes.c_uint8,  1, False, False), "b16": (ctypes.c_uint16, 2, False, False),
    "b32": (ctypes.c_uint32, 4, False, False), "b64": (ctypes.c_uint64, 8, False, False),
    "f16": (ctypes.c_uint16, 2, False, True),  "f32": (ctypes.c_float,  4, False, True),
    "f64": (ctypes.c_double, 8, False, True),
    "pred": (ctypes.c_bool,  1, False, False),
}

_COMMENT = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
# ".reg .u64 %dat_u64_<2>;" declares %dat_u64_0 and %dat_u64_1
_REG_DECL = re.compile(r"\.reg\s+\.(\w+)\s+%([\w$]+)<(\d+)>\s*;")
_REG_ONE = re.compile(r"\.reg\s+\.(\w+)\s+%([\w$]+)\s*;")
_PARAM = re.compile(r"\.param\s+\.(\w+)\s+([\w$]+)")
# "[%bidx_u64_0+0]" / "[data0+0]" / "[%r1]"
_ADDR = re.compile(r"\[\s*%?([\w$]+)\s*(?:\+\s*(-?\d+))?\s*\]")
# ".shared .align 16 .b8 local0[64];" declares a per-block shared array.
_SHARED = re.compile(r"\.shared\s+(?:\.align\s+(\d+)\s+)?\.(\w+)\s+([\w$]+)\[(\d+)\]\s*;")
# "local0[0]" as a source operand is the ADDRESS of that element, not a load.
_SYMIDX = re.compile(r"^([\w$]+)\[(\d+)\]$")


def _bits_of(v: Any, ty: str | None) -> int:
    """The raw bit pattern of a value held in a register of type `ty`."""
    if ty == "f32": return struct.unpack("<I", struct.pack("<f", float(v)))[0]
    if ty == "f64": return struct.unpack("<Q", struct.pack("<d", float(v)))[0]
    if ty == "f16": return struct.unpack("<H", struct.pack("<e", float(v)))[0]
    return int(v) & ((1 << (_TYPES[ty][1] * 8)) - 1) if ty in _TYPES else int(v)


def _from_bits(bits: int, ty: str | None) -> Any:
    """Interpret a raw bit pattern as a value of type `ty`."""
    if ty == "f32": return _f32_bits(bits)
    if ty == "f64": return _f64_bits(bits)
    if ty == "f16": return struct.unpack("<e", struct.pack("<H", bits & 0xFFFF))[0]
    return _truncate(bits, ty)


def _f32_bits(x: int) -> float:
    return struct.unpack("<f", struct.pack("<I", x & 0xFFFFFFFF))[0]


def _f64_bits(x: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", x & 0xFFFFFFFFFFFFFFFF))[0]


def parse_immediate(tok: str, ty: str) -> Any:
    """PTX immediates: 0f3F800000 is a float bit pattern, 0d... a double.

    Integer literals may carry a U/L suffix ("1U", "4096UL"), which int() rejects.
    """
    tok = tok.strip()
    if re.fullmatch(r"-?\d+[UuLl]+", tok): tok = tok.rstrip("UuLl")
    if tok.startswith("0f") or tok.startswith("0F"):
        return _f32_bits(int(tok[2:], 16))
    if tok.startswith("0d") or tok.startswith("0D"):
        return _f64_bits(int(tok[2:], 16))
    if tok.startswith("0x") or tok.startswith("0X"):
        v = int(tok, 16)
        return _f32_bits(v) if ty == "f32" else _f64_bits(v) if ty == "f64" else v
    if ty in ("f16", "f32", "f64") or "." in tok or "e" in tok.lower():
        try: return float(tok)
        except ValueError: pass
    return int(tok, 0)


def _split_operands(rest: str) -> list[str]:
    """Split on commas that are not inside a {...} vector group.

    PTX vector accesses put a register list in the destination:
        ld.global.v2.f32 {%val_f32_1, %val_f32_2}, [%bidx_u64_0+0];
    A naive comma split turns that into three operands and the address parse
    then fails on the fragment "%val_f32_2}".
    """
    out: list[str] = []
    depth = start = 0
    for i, ch in enumerate(rest):
        if ch in "{(": depth += 1
        elif ch in "})": depth -= 1
        elif ch == "," and depth == 0:
            out.append(rest[start:i].strip()); start = i + 1
    tail = rest[start:].strip()
    if tail: out.append(tail)
    return [a for a in out if a]


class Instruction:
    __slots__ = ("pred", "pred_neg", "op", "mods", "ty", "args", "raw")

    def __init__(self, raw: str):
        self.raw = raw
        self.pred: str | None = None
        self.pred_neg = False
        body = raw.strip().rstrip(";").strip()
        if body.startswith("@"):
            guard, body = body.split(None, 1)  # split(None) handles tabs
            guard = guard[1:]
            if guard.startswith("!"):
                self.pred_neg, guard = True, guard[1:]
            self.pred = guard.lstrip("%")
            body = body.strip()
        # tinygrad emits tab-separated PTX ("add.f32\t%d, %a, %b"), so the
        # opcode must be split on any whitespace, not on a literal space.
        head, _, rest = (body.split(None, 1) + [""])[:2] and \
            (body.split(None, 1)[0], None, body.split(None, 1)[1] if len(body.split(None, 1)) > 1 else "")
        parts = head.split(".")
        self.op = parts[0]
        self.mods = parts[1:]
        # The type is the last modifier that names one; cvt has two (dst.src).
        self.ty = next((m for m in reversed(self.mods) if m in _TYPES), None)
        self.args = _split_operands(rest)


class Kernel:
    """A parsed .entry: its parameter order, register types, code and labels."""

    def __init__(self, src: str):
        src = _COMMENT.sub("", src)
        m = re.search(r"\.entry\s+([\w$]+)", src)
        self.name: str = m.group(1) if m else "?"
        self.params: list[str] = [m.group(2) for m in _PARAM.finditer(src)]
        # Lay declared shared arrays out back to back, honouring each alignment.
        self.shared_syms: dict[str, int] = {}
        off = 0
        for align, _ty, name, size in _SHARED.findall(src):
            a = int(align or 1)
            off = (off + a - 1) // a * a
            self.shared_syms[name] = off
            off += int(size)
        self.shared_size = off
        self.reg_ty: dict[str, str] = {}
        for ty, base, n in _REG_DECL.findall(src):
            for i in range(int(n)):
                self.reg_ty[f"{base}{i}"] = ty
        for ty, name in _REG_ONE.findall(src):
            self.reg_ty[name] = ty

        body = src[src.index("{") + 1:src.rindex("}")]
        self.code: list[Instruction] = []
        self.labels: dict[str, int] = {}
        for line in body.split("\n"):
            line = line.strip()
            if not line or line.startswith("."):
                continue
            if line.endswith(":"):
                self.labels[line[:-1].strip()] = len(self.code)
                continue
            for stmt in (s for s in line.split(";") if s.strip()):
                self.code.append(Instruction(stmt))


class Thread:
    __slots__ = ("regs", "tid", "ctaid", "ntid", "nctaid")

    def __init__(self, tid, ctaid, ntid, nctaid):
        self.regs: dict[str, Any] = {}
        self.tid, self.ctaid, self.ntid, self.nctaid = tid, ctaid, ntid, nctaid

    def special(self, name: str) -> int:
        base, _, comp = name.partition(".")
        idx = {"x": 0, "y": 1, "z": 2}.get(comp, 0)
        return {"%tid": self.tid, "%ctaid": self.ctaid,
                "%ntid": self.ntid, "%nctaid": self.nctaid}[base][idx]


def _vector_regs(tok: str) -> list[str]:
    """"{%a, %b}" -> ["%a", "%b"];  "%a" -> ["%a"]."""
    tok = tok.strip()
    if tok.startswith("{") and tok.endswith("}"):
        return [x.strip() for x in tok[1:-1].split(",") if x.strip()]
    return [tok]


def _truncate(v: Any, ty: str | None) -> Any:
    """Wrap an integer result to its declared width, as the hardware would."""
    if ty is None or ty not in _TYPES:
        return v
    _, width, signed, is_float = _TYPES[ty]
    if is_float or ty == "pred" or isinstance(v, float):
        return v
    bits = width * 8
    v &= (1 << bits) - 1
    if signed and v >= (1 << (bits - 1)):
        v -= 1 << bits
    return v


import math

# setp comparison suffixes. The unordered float forms (.neu, .ltu, ...) differ
# from the plain ones only when an operand is NaN, which is why they are listed
# separately rather than aliased.
_CMP = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,  "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,  "ge": lambda a, b: a >= b,
    "lo": lambda a, b: a < b,  "ls": lambda a, b: a <= b,
    "hi": lambda a, b: a > b,  "hs": lambda a, b: a >= b,
    "equ": lambda a, b: a == b or _nan(a, b), "neu": lambda a, b: a != b or _nan(a, b),
    "ltu": lambda a, b: a < b or _nan(a, b),  "leu": lambda a, b: a <= b or _nan(a, b),
    "gtu": lambda a, b: a > b or _nan(a, b),  "geu": lambda a, b: a >= b or _nan(a, b),
    "num": lambda a, b: not _nan(a, b), "nan": lambda a, b: _nan(a, b),
}


def _nan(a, b) -> bool:
    return (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b))


def _idiv(a: int, b: int) -> int:
    if b == 0: return 0
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _irem(a: int, b: int) -> int:
    return 0 if b == 0 else a - _idiv(a, b) * b


class Executor:
    def __init__(self, kernel: Kernel, param_vals: list[int], smem_size: int,
                 valid_ranges: Iterable[tuple[int, int]] | None = None):
        self.k = kernel
        self.params = dict(zip(kernel.params, param_vals))
        self.smem_size = max(smem_size, kernel.shared_size, 1)
        # Empty means "unchecked": a caller that cannot enumerate its buffers
        # keeps the old behaviour rather than rejecting every address.
        self._mapped = sorted((a, a + s) for a, s in (valid_ranges or ()))
        self.new_block()

    def new_block(self) -> None:
        """Shared memory is per block, so it is reallocated and zeroed for each."""
        self.shared = (ctypes.c_uint8 * self.smem_size)()
        self.shared_addr = ctypes.addressof(self.shared)
        self.ranges = sorted(self._mapped +
                             [(self.shared_addr, self.shared_addr + self.smem_size)]) \
            if self._mapped else []

    # ---- operand access -------------------------------------------------
    def read(self, t: Thread, tok: str, ty: str | None) -> Any:
        tok = tok.strip()
        if tok.startswith("%"):
            name = tok[1:]
            if tok.split(".")[0] in ("%tid", "%ctaid", "%ntid", "%nctaid"):
                return t.special(tok)
            v = t.regs.get(name, 0)
            # Reading under an untyped .b* suffix asks for the register's BITS,
            # not its numeric value: `and.b32 %f, %f, 0x7fffffff` is fabs, and
            # int(-1.5) would mask the truncated 1 instead of the sign bit.
            # Symmetric with the reinterpretation write() does.
            src_ty = self.k.reg_ty.get(name)
            if ty is not None and ty.startswith("b") and src_ty is not None \
                    and src_ty != ty and src_ty in _TYPES \
                    and _TYPES[src_ty][1] == _TYPES[ty][1]:
                return _bits_of(v, src_ty)
            return v
        if tok in self.params:
            return self.params[tok]
        if (m := _SYMIDX.match(tok)) and m.group(1) in self.k.shared_syms:
            return self.shared_addr + self.k.shared_syms[m.group(1)] + int(m.group(2))
        if tok in self.k.shared_syms:
            return self.shared_addr + self.k.shared_syms[tok]
        return parse_immediate(tok, ty or "s64")

    def write(self, t: Thread, tok: str, val: Any, ty: str | None) -> None:
        name = tok.strip().lstrip("%")
        # A register holds one canonical value whichever type suffix produced it.
        # The untyped bitwise ops (.b8/.b16/.b32/.b64) carry no sign, so their
        # result must be reinterpreted as the register's declared type: `xor.b32`
        # into a .s32 register has to store what `max.s32` would, or a later
        # `setp.eq.s32` compares 4294967293 against -3 and reports not-equal.
        # Typed ops already mean the right thing and are left alone.
        dst_ty = self.k.reg_ty.get(name, ty)
        if ty is not None and ty.startswith("b") and dst_ty != ty and dst_ty in _TYPES \
                and _TYPES[ty][1] == _TYPES[dst_ty][1]:
            t.regs[name] = _from_bits(_bits_of(val, ty), dst_ty)
            return
        t.regs[name] = _truncate(val, ty)

    def addr_of(self, t: Thread, tok: str) -> int:
        m = _ADDR.search(tok)
        if m is None:
            raise RuntimeError(f"unparsed address operand: {tok!r}")
        base, off = m.group(1), int(m.group(2) or 0)
        if base in self.params:
            return self.params[base] + off
        if base in self.k.shared_syms:
            return self.shared_addr + self.k.shared_syms[base] + off
        return t.regs.get(base, 0) + off

    def _check(self, addr: int, ty: str, what: str) -> None:
        """Every access goes through a real pointer, so an out-of-range address
        silently corrupts the interpreter's own heap and aborts it later, in an
        unrelated test. Fail here instead, where the kernel is still known.
        Mirrors `valid_mem_ranges` in the AMD emulator (test/mockgpu/helpers.py).
        """
        if not self.ranges:
            return
        n = _TYPES[ty][1]
        i = bisect.bisect_right(self.ranges, (addr, 1 << 62)) - 1
        if i >= 0 and self.ranges[i][0] <= addr and addr + n <= self.ranges[i][1]:
            return
        raise RuntimeError(
            f"{what} of {n}B at 0x{addr:x} is outside every mapped buffer "
            f"(kernel {self.k.name!r}); ranges=" +
            ", ".join(f"0x{lo:x}-0x{hi:x}" for lo, hi in self.ranges))

    def load(self, addr: int, ty: str) -> Any:
        self._check(addr, ty, "load")
        cty, _, _, _ = _TYPES[ty]
        raw = ctypes.cast(addr, ctypes.POINTER(cty))[0]
        return _from_bits(raw, "f16") if ty == "f16" else raw

    def store(self, addr: int, ty: str, val: Any) -> None:
        self._check(addr, ty, "store")
        cty, _, _, is_float = _TYPES[ty]
        if ty == "f16":
            ctypes.cast(addr, ctypes.POINTER(cty))[0] = _bits_of(val, "f16"); return
        ctypes.cast(addr, ctypes.POINTER(cty))[0] = cty(float(val) if is_float else int(val)).value

    # ---- one thread, as a generator so bar.sync can suspend it ----------
    def run_thread(self, t: Thread):
        pc = 0
        code = self.k.code
        while pc < len(code):
            ins = code[pc]
            pc += 1
            if ins.pred is not None:
                p = bool(t.regs.get(ins.pred, False))
                if p == ins.pred_neg:
                    continue

            op, ty, a = ins.op, ins.ty, ins.args
            if op == "ret":
                return
            if op == "bra":
                pc = self.k.labels[a[0].strip().lstrip("@")]
                continue
            if op == "bar":
                yield            # resume once every thread reaches this point
                continue
            if op in ("ld", "ldu"):
                if "param" in ins.mods:
                    m = _ADDR.search(a[1])
                    name = m.group(1) if m else a[1].strip()
                    self.write(t, a[0], self.params.get(name, 0), ty)
                else:
                    addr = self.addr_of(t, a[1])
                    dsts = _vector_regs(a[0])
                    width = _TYPES[ty or "b32"][1]
                    for i, d in enumerate(dsts):
                        self.write(t, d, self.load(addr + i * width, ty or "b32"), ty)
                continue
            if op == "st":
                addr = self.addr_of(t, a[0])
                srcs = _vector_regs(a[1])
                width = _TYPES[ty or "b32"][1]
                for i, srg in enumerate(srcs):
                    self.store(addr + i * width, ty or "b32", self.read(t, srg, ty))
                continue

            self.alu(t, ins)
        return

    # ---- arithmetic / logic --------------------------------------------
    def alu(self, t: Thread, ins: Instruction) -> None:
        op, ty, a, mods = ins.op, ins.ty, ins.args, ins.mods
        rd = lambda i, tt=None: self.read(t, a[i], tt or ty)
        is_f = ty in ("f16", "f32", "f64")

        if op == "mov":
            src_tok = a[1].strip()
            dst_ty = self.k.reg_ty.get(a[0].strip().lstrip("%"), ty)
            if ty is not None and ty.startswith("b") and src_tok.startswith("%") \
                    and not src_tok.split(".")[0] in ("%tid", "%ctaid", "%ntid", "%nctaid"):
                src_ty = self.k.reg_ty.get(src_tok.lstrip("%"), ty)
                if src_ty != dst_ty:
                    self.write(t, a[0], _from_bits(_bits_of(rd(1, src_ty), src_ty), dst_ty), dst_ty)
                    return
            v = rd(1, dst_ty if ty is not None and ty.startswith("b") else ty)
            self.write(t, a[0], v, dst_ty if ty is not None and ty.startswith("b") else ty); return
        if op == "cvt":
            v = rd(1, ins.mods[-1] if ins.mods[-1] in _TYPES else ty)
            dst_ty = next((m for m in mods if m in _TYPES), ty)
            if "rzi" in mods or "rmi" in mods or "rpi" in mods or "rni" in mods:
                v = math.trunc(v) if "rzi" in mods else math.floor(v) if "rmi" in mods else \
                    math.ceil(v) if "rpi" in mods else round(v)
            v = float(v) if dst_ty in ("f16", "f32", "f64") else int(v)
            self.write(t, a[0], v, dst_ty); return
        if op == "selp":
            self.write(t, a[0], rd(1) if bool(t.regs.get(a[3].strip().lstrip("%"), False)) else rd(2), ty); return
        if op == "setp":
            cmp = next((m for m in mods if m in _CMP), None)
            r = _CMP[cmp](rd(1), rd(2))
            if len(a) > 3:   # setp.op.and.pred form
                r = r and bool(t.regs.get(a[3].strip().lstrip("%!"), False))
            self.write(t, a[0], bool(r), "pred"); return

        if op in ("add", "sub", "mul", "div", "rem", "max", "min", "and", "or", "xor", "shl", "shr"):
            x, y = rd(1), rd(2)
            if op == "add": v = x + y
            elif op == "sub": v = x - y
            elif op == "mul":
                v = x * y
                if "hi" in mods: v = (int(x) * int(y)) >> (_TYPES[ty][1] * 8)
            elif op == "div": v = (x / y if y else math.inf) if is_f else _idiv(int(x), int(y))
            elif op == "rem": v = _irem(int(x), int(y))
            elif op == "max": v = max(x, y)
            elif op == "min": v = min(x, y)
            elif op == "and": v = (x and y) if ty == "pred" else int(x) & int(y)
            elif op == "or":  v = (x or y) if ty == "pred" else int(x) | int(y)
            elif op == "xor": v = (bool(x) != bool(y)) if ty == "pred" else int(x) ^ int(y)
            elif op in ("shl", "shr"):
                # A shift wider than the register yields 0 on the hardware. Left
                # unguarded, Python instead allocates an integer of that many
                # bits: a shift of 2**31 asks for 256MB and aborts the process.
                nbits = _TYPES[ty][1] * 8 if ty in _TYPES else 64
                sh = int(y)
                if op == "shl": v = 0 if sh >= nbits else int(x) << sh
                else: v = (0 if int(x) >= 0 else -1) if sh >= nbits else int(x) >> sh
            self.write(t, a[0], v, ty); return

        if op in ("mad", "fma"):
            x, y, z = rd(1), rd(2), rd(3)
            self.write(t, a[0], x * y + z, ty); return
        if op == "neg":
            self.write(t, a[0], -rd(1), ty); return
        if op == "abs":
            self.write(t, a[0], abs(rd(1)), ty); return
        if op == "not":
            v = rd(1)
            self.write(t, a[0], (not bool(v)) if ty == "pred" else ~int(v), ty); return

        if op in ("rcp", "sqrt", "rsqrt", "ex2", "lg2", "sin", "cos", "tanh"):
            x = float(rd(1))
            try:
                v = {"rcp": lambda: 1.0 / x, "sqrt": lambda: math.sqrt(x),
                     "rsqrt": lambda: 1.0 / math.sqrt(x), "ex2": lambda: 2.0 ** x,
                     "lg2": lambda: math.log2(x), "sin": lambda: math.sin(x),
                     "cos": lambda: math.cos(x), "tanh": lambda: math.tanh(x)}[op]()
            except (ValueError, ZeroDivisionError, OverflowError):
                # The hardware returns an IEEE value where the math module raises.
                # log2(0) is -inf, not +inf: getting this backwards flips the sign
                # of logsumexp on an all--inf row.
                if op in ("sqrt", "rsqrt", "lg2") and x < 0: v = math.nan
                elif op == "lg2": v = -math.inf
                elif op == "rcp": v = math.copysign(math.inf, x)
                else: v = math.inf
            self.write(t, a[0], v, ty); return

        raise NotImplementedError(f"PTX op not implemented: {ins.raw.strip()!r}")


def run(source: bytes, args: list[int], block: tuple[int, int, int],
        grid: tuple[int, int, int], smem: int = 0,
        valid_ranges: Iterable[tuple[int, int]] | None = None) -> None:
    """Execute one kernel launch. Threads in a block advance together at bar.sync."""
    kern = Kernel(source.decode("utf-8", "replace"))
    ex = Executor(kern, args, smem, valid_ranges)
    bx, by, bz = block
    for gz in range(grid[2]):
        for gy in range(grid[1]):
            for gx in range(grid[0]):
                ex.new_block()
                live = []
                for tz in range(bz):
                    for tyi in range(by):
                        for tx in range(bx):
                            th = Thread((tx, tyi, tz), (gx, gy, gz), block, grid)
                            live.append(ex.run_thread(th))
                while live:
                    still = []
                    for g in live:
                        try:
                            next(g); still.append(g)
                        except StopIteration:
                            pass
                    live = still
