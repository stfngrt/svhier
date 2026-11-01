# svhier

[![CI](https://github.com/stfngrt/svhier/actions/workflows/ci.yml/badge.svg)](https://github.com/stfngrt/svhier/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-stfngrt.github.io%2Fsvhier-blue)](https://stfngrt.github.io/svhier/)

**See the module hierarchy of any SystemVerilog codebase, find out what's missing, and get a filelist that compiles.**

Point svhier at a directory of RTL. It elaborates the design with
[slang](https://github.com/MikePopoloski/slang) and shows you the instance tree:

```text
$ svhier -r core -y common -y vendor --top cva6
cva6
├── i_frontend (frontend)
│   ├── i_instr_realign (instr_realign)
│   ├── ras_gen.i_ras (ras)
│   ├── bht_gen.i_bht (bht)
│   └── i_instr_queue (instr_queue)
│       ├── gen_instr_fifo[0].i_fifo_instr_data (cva6_fifo_v3)
│       └── i_fifo_address (cva6_fifo_v3)
├── id_stage_i (id_stage)
├── issue_stage_i (issue_stage)
│   ├── i_scoreboard (scoreboard)
│   └── i_issue_read_operands (issue_read_operands)
├── ex_stage_i (ex_stage)
│   ├── alu_wrapper_i (alu_wrapper)
│   │   └── alu_i (alu)
…
```

## Why svhier

- 🌳 **Real elaboration, not regexes.** slang evaluates generate blocks,
  instance arrays and parameters, so `gen_sram[1].data_sram` shows up exactly
  where it sits in the elaborated design.
- 🕳️ **Tells you what's missing.** Undefined modules are marked `(blackbox)`
  in the tree. Instead of hundreds of compiler errors you get a short
  **Not found** table: modules, packages, interfaces, includes and macros,
  each with the file that needs it.
- 📚 **Finds it for you.** `-y DIR` searches library directories and pulls in
  only the files that define what's missing, plus whatever *those* files
  need in turn.
- 📄 **Hands you a filelist.** `--filelist` writes sources in dependency order
  with `+incdir+` lines, ready for VCS, Questa or Xcelium.
- 🧩 **Reads your filelists.** `-f`/`-F` command files with `+incdir+`,
  `+define+` and `${VAR}` work as-is, as do `-I`, `-D` and `--top`.
- 🔧 **Script-friendly.** Data goes to stdout and everything else to stderr.
  `--yaml` gives you the full hierarchy as data.

## Quick start

```bash
pip install svhier

svhier -r rtl/                                   # instance tree
svhier -r rtl/ --top my_soc                      # only the tree under my_soc
svhier -f project.f --top my_soc                 # use your existing filelist
svhier -r rtl/ -y vendor/ -y ip/                 # resolve blackboxes from library dirs
svhier -r rtl/ -y vendor/ --filelist compile.f   # dependency-ordered filelist
svhier -r rtl/ --yaml hierarchy.yaml             # hierarchy as YAML
```

New to it? The [getting started guide](https://stfngrt.github.io/svhier/getting-started.html)
takes the CVA6 RISC-V core from 300 loose files to a complete hierarchy in four commands.

## Documentation

**[stfngrt.github.io/svhier](https://stfngrt.github.io/svhier/)** has the
user guide, the CLI reference and architecture notes.

## Acknowledgements

Inspired by [svinst](https://github.com/sgherbst/svinst) by Steven Herbst, which provides similar module/instance extraction for SystemVerilog.
