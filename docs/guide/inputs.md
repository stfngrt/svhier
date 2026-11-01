# Inputs

svhier takes sources in three ways, and you can mix them freely, in any
order on the command line.

::::{tab-set}
:::{tab-item} Directories
```bash
svhier -r rtl/ -x tb
```
Every `.sv`/`.v` file in the directory. `-r` searches it recursively.
:::
:::{tab-item} Project filelist
```bash
svhier -f core/Flist.cva6 --top cva6
```
The same command files your simulator uses.
:::
:::{tab-item} Library dirs
```bash
svhier -r rtl/ -y vendor/
```
Searched, not compiled. See [](libdirs.md).
:::
::::

## Files and directories

Paths can be files or directories. Directories contribute every `.sv` and
`.v` file in them, and every subdirectory too with `-r`. Every directory that
contains `.svh`/`.vh` headers is added to the include path automatically.

`-x NAME` skips any file or directory with a path component that contains
`NAME` (case-insensitive). It can be repeated, and applies to library
directories too:

```bash
svhier -r rtl/ -x test -x tb -x xilinx
```

(command-files)=
## Command files (`-f` / `-F`)

If your project already has a filelist, use it. svhier hands command files to
slang, so everything slang understands works:

- `+incdir+DIR` and `+define+NAME[=VALUE]`
- nested `-f` / `-F`
- `${VAR}` environment variables

```bash
export CVA6_REPO_DIR=$PWD/cva6 HPDCACHE_DIR=$PWD/cva6/core/cache_subsystem/hpdcache TARGET_CFG=cv64a6_imafdc_sv39
svhier -f $CVA6_REPO_DIR/core/Flist.cva6 --top cva6 --filelist cva6.f
```

With `-F FILE`, paths inside FILE are resolved relative to FILE. With `-f`,
they're resolved relative to the working directory.

:::{tip}
A project filelist usually picks one variant of each config package or
technology cell. That makes it the quickest way to a clean, duplicate-free
build. Use `-r` plus [`-y`](libdirs.md) when there is no filelist, or to find
out what one is missing.
:::

## Include dirs and macros

| Flag | Meaning |
|------|---------|
| `-I DIR`, `--include-dir DIR` | Add DIR to the `` `include `` search path |
| `-D NAME[=VALUE]`, `--define NAME[=VALUE]` | Define a preprocessor macro |

Both can be repeated and combined with paths and command files. Macros
passed with `-D` or `+define+` show up again as `+define+` lines in the
generated [filelist](outputs.md).

(top-modules)=
## Top modules (`--top`)

By default every module that nothing else instantiates is a top.
`--top MODULE` (repeatable) elaborates only the modules you name:

- the [tree](tree.md) shows only their hierarchy
- unused modules and testbenches aren't elaborated, so they add no errors
- [`-y`](libdirs.md) only pulls in what these tops need
- the [filelist](outputs.md) contains only the files they need
