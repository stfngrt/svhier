# Diagnostics and exit codes

## slang's errors and warnings

By default svhier prints a single count line instead of every compiler
message:

```text
slang reported 110 errors, 989 warnings (use -E to list errors, -W to also list warnings)
```

The reason: on a partial file set, almost all errors are follow-on errors
from something [missing](missing.md), and the list is only worth reading once
the design is complete. To see them anyway:

| Flag | Shows |
|------|-------|
| `-E`, `--errors` | every error |
| `-W`, `--warnings` | every error and warning |

All diagnostics come from the **final** parse, after [`-y`](libdirs.md) has
resolved what it can. Missing files that were found don't leave errors
behind.

To get the error list down to what matters:

1. **Fix [missing definitions](missing.md)** (or let `-y` find them).
2. **Fix duplicate definitions.** If several files define the same package or
   module, slang uses the first one, and the rest of the design may not match it.
3. **Pass `--top`.** Without it, every uninstantiated module is elaborated as
   a top with its default parameters, which generic library modules often
   don't support.

## Per-file summary

`--summary` adds a table to stderr with the number of modules, packages and
instances per file.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Elaboration completed without errors |
| `1` | slang reported at least one error, or no source files were found |
| `2` | Invalid command-line arguments |

All outputs are still written when the exit code is `1`.
