# tinygrad GPUOcelot bounty — unfinished attempt (2026-08-30)

**Status: not submittable.** Preserved here because the working copy lived in
`/private/tmp`, which gets reaped. Do not open a PR against tinygrad from this.

## The bounty

Row 7 of the [tinygrad bounty sheet][sheet], unclaimed (empty `GitHub Owner`)
as of 2026-08-30:

> GPUOcelot or similar that works with CUDA 12 and replaces our CI NV tests
> with this — **$500**, Hardware Required: `None!`

"or similar" is what makes a pure-Python emulator eligible rather than
disqualified: the real problem is that gpuocelot is a dead Georgia Tech project
that CI downloads as a prebuilt `.so`, and that macOS contributors have to build
from source with cmake/ninja/bison via `extra/setup_mock_nv_osx.sh`.

The sheet also says, verbatim: `SLOP OR WIP = BAN FROM GITHUB, NO WARNING`.
That is why this sits here instead of upstream.

## What was built

`ptx_emu.py` (454 lines) — a pure-Python PTX interpreter replacing the ctypes
binding to `libgpuocelot` in `test/mockgpu/helpers.py` (`helpers.py.patch`).
Parses `.entry` kernels, expands `.reg` declarations, lays out `.shared` arrays
per block, and executes threads as generators so `bar.sync` can suspend them.

The instruction surface is bounded by `tinygrad/renderer/ptx.py` — its
`asm_for_op` table defines roughly 25 op families, so this does not need to be a
general PTX implementation.

It takes tinygrad from `exit=139` (segfault on the missing library) to a running
suite. That part works.

## What was measured

The question that decided whether this was worth finishing was runtime: a
pure-Python interpreter against a hard CI timeout.

| measurement | value |
|---|---|
| CI budget (`testnvidia`, `test.yml`) | `timeout-minutes: 20` |
| Full `test/backend` at `-n=4` | **10m55s** |
| Emulator throughput | ~50k element-ops/sec, linear in problem size |
| Peak RSS at 4.2M elements | 64 MB (flat — memory is not a constraint) |

`-n=4` is the honest simulation: an outside contributor's PR runs on
`ubuntu-24.04` (4 cores), not the `namespace-profile-tinygrad` runner, because
that is gated on `author_association == 'COLLABORATOR'`.

**Runtime is not the blocker.** That risk is closed.

## What blocks it

### 1. Hard aborts — unexplained

10 `Fatal Python error: Aborted` / worker deaths in the last full run. This is
the real blocker and it is **not diagnosed**.

Ruled out, each with a measurement:

- **Not memory exhaustion** — RSS stays flat (64 MB) while runtime scales
  perfectly linearly.
- **Not out-of-bounds `load`/`store`** — bounds checking was wired through from
  `cuda_state.memory` (mirroring `valid_mem_ranges` in the AMD emulator, see
  `test/mockgpu/helpers.py`). It produced no false positives on working kernels
  and never fired on a crashing one.
- **Not malformed operands** — instrumenting `parse_immediate` for long or empty
  tokens found zero.
- **Not the environment or pytest-xdist** — control run, same four test files,
  same `-n=4`, on METAL instead of the emulator: **155 passed, 0 aborts, 10s**.
  Under the emulator: 8 failed, 3 worker deaths, 6m11s. The crash is ours.

The crash lands in `re.fullmatch` inside `parse_immediate`, reached from an
ordinary ALU operand via `read()`. No explanation for that yet. Single tests
often pass in isolation and die inside a long worker session, which is the
signature of state corrupted earlier surfacing later.

### 2. Correctness gap

Roughly 24 failures against a target of zero.

**Failure counts from this session are not a reliable measurement.** A crashed
worker takes a variable slice of the suite down with it, so run-to-run counts
move independently of any code change. Use the abort count as the signal until
the crashes are fixed.

## What was fixed

`log2(0)` returned `+inf` instead of `-inf`. The fallback branch lumped "math
domain error" together with "overflow", which flipped the sign of `logsumexp`
on an all-`-inf` row. Fixes `test_logsumexp` and `test_logcumsumexp_numerical`.
Verified in isolation.

## What was tried and reverted

`ptx_emu.canonical-attempt.py` — the idea that a register must hold one
canonical value regardless of which type suffix wrote it, so that `xor.b32` into
a `.s32` register stores what `max.s32` would (otherwise a later `setp.eq.s32`
compares `4294967293` against `-3` and reports not-equal).

It fixed `test_argmax`, `test_argmin` and `test_sort`, and broke far more than
it fixed. A follow-up making reads symmetric — `and.b32 %f, %f, 0x7fffffff` is
floating-point `abs`, so reading an `.f32` register under a `b32` instruction
must take the bit pattern, not `int(-1.5)` — did not recover it either.

Kept because the underlying observation is real and reproducible: the diagnosis
was verified by tracing every register in the failing kernel. The fix was wrong,
or too broad, or fixed one bug while exposing another. If revisited, **gate on
the full suite, never on the targeted tests** — that mistake is what produced
two consecutive wrong conclusions.

## If picked back up

1. Diagnose the aborts first. Nothing else can be measured reliably until the
   crashes stop, because they corrupt the failure counts.
2. Only then re-attempt the type-canonicalization question, with the full suite
   as the gate.
3. Both CI matrix entries must pass — `DEV=MOCK+CUDA:PTX` (via
   `test/mockgpu/cuda/cuda.py`) and `DEV=MOCK+NV` (via
   `test/mockgpu/nv/nvgpu.py`). Only the CUDA path was exercised here.
4. Shipping also means removing the gpuocelot download from
   `.github/actions/setup-tinygrad/action.yml` and deleting
   `extra/setup_mock_nv_osx.sh`.

## Reproducing

```bash
git clone --depth 1 https://github.com/tinygrad/tinygrad
cp ptx_emu.py tinygrad/test/mockgpu/nv/ptx_emu.py
cd tinygrad && git apply ../helpers.py.patch
uv venv && uv pip install -e '.[testing]' pytest-xdist
DEV=MOCK+CUDA:PTX FORWARD_ONLY=1 PYTHONPATH=. \
  python -m pytest -n=4 test/backend --ignore test/backend/test_multitensor.py
```

[sheet]: https://docs.google.com/spreadsheets/d/1WKHbT-7KOgjEawq5h5Ic1qUWzpfAzuD_J06N1JwOCGs
