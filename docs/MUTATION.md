# Mutation testing: what the score means here

**AUTHOR** Craig Hunt

## Measured, 2026-11-18

| | Count | Share |
| --- | --- | --- |
| Mutants | 894 | |
| Killed | 668 | 74.7% |
| Survived | 213 | 23.8% |
| Timed out | 0 | counted apart from kills, never as kills |
| Skipped | 13 | |

**The gate passes**: 74.7% against a 70% floor, over 208 tests.

### How it got there, recorded because the method matters

The first measurement was 65.4%, below this repository's own floor, which is
the kind of thing a standards repository cannot ship. It reached 74.7% through
36 further tests and exactly one new exclusion pattern, described below.

That history stays here on purpose. There are two ways to move this number,
and only one of them is honest: write tests until the suite would notice the
mutants, or widen the exclusions until they stop counting. A repository that
quietly did the second would teach the wrong lesson to everybody who copied
it. 36 tests and one pattern is the ratio that happened, and it is the ratio
worth defending.

### What still survives, honestly

Some of the remainder cannot be killed by any test on this machine:

- `"UTF-8"` substituted for `"utf-8"`, which behaves identically.
- `encoding=None` where the platform default is already UTF-8.
- `"CONFIG.YAML"` for `"config.yaml"`, which opens the same file because the
  checkout sits on a case-insensitive filesystem. On a case-sensitive one this
  mutant dies, so the score is a point or so higher there.

These are equivalent mutants rather than untested behavior. They are left in
the denominator rather than excluded, because a pattern broad enough to catch
them would also catch strings that matter, and a score that flatters itself is
worth less than one that does not.

## The gate

`scripts/mutation-gate.sh` fails the build below **70%**, reading killed apart
from timed out.

That distinction matters more than the number. mutmut's summary line counts a
timeout alongside a kill, and a mutant that timed out was never shown to be
caught by anything; it was shown to be slow. Counting it as a kill inflates
the score by exactly the number of mutants nobody tested, and the inflation
grows as a suite gets slower, which is when an honest score matters most.

## What sits outside the denominator

Prose, and nothing else.

A mutant that rewords a log line, a recommendation or an exception message is
unkillable without a test that pins the copy. Pinning copy makes an editorial
pass fail the suite while proving nothing about behavior, so those lines are
excluded in `setup.cfg` rather than tested.

The patterns stay narrow on purpose, and the last one is the one that needs
justifying:

```
^\s*f?["'][^"']*["'],?\s*$
```

It matches a line that is **only** a string literal, which is how a
continuation of a multi-line message looks and nothing else does. A behavioral
literal always carries a name, a key or a call on the same line: `detail=`, a
dictionary key, an argument. Matching every string instead would exclude a
finding's type, its detail key and every environment variable name, which are
the strings a test most needs to pin.

## Why coverage is not the bar

Coverage says a line ran. Mutation says a test would have noticed had the line
been wrong. Only the second is a statement about the suite rather than about
the code.

A suite can reach every line of this agent and still pass with the major
threshold inverted, the rate-limit statuses treated as answers, and the exit
code flipped. Mutation is what finds that, which is why the gate is here and
the coverage number is not.

## Reading a survivor

A surviving mutant is a question, not a defect. Three honest answers:

1. **The test was missing.** Write it. Most survivors are this.
2. **The line is prose.** Check whether the exclusion patterns should have
   caught it, and widen them only if the new pattern cannot also catch a
   behavioral string.
3. **The mutant is equivalent.** The change cannot alter behavior, so no test
   can kill it. Rare, and worth a comment in the code saying why rather than a
   silent exclusion.

What is never an honest answer is widening the exclusions until the number
rises. The score exists to be uncomfortable.
