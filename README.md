# icinst

[![CI](https://github.com/stfngrt/icinst/actions/workflows/ci.yml/badge.svg)](https://github.com/stfngrt/icinst/actions/workflows/ci.yml)

Parse SystemVerilog files and extract the module/instance hierarchy as YAML or a dependency-ordered filelist, powered by the [slang](https://github.com/MikePopoloski/slang) elaborator.

- Cross-file instantiation and package imports resolve correctly
- Outputs YAML hierarchy and/or a `+incdir+`-annotated compilation filelist
- Packages and interface instances are filtered from the output

## Quick start

```bash
pip install icinst
icinst -r rtl/ --yaml hierarchy.yaml --filelist compile.f
```

```yaml
files:
- file_name: rtl/top.sv
  defs:
  - mod_name: Top
    insts:
    - mod_name: Mid
      inst_name: u_mid0
    - mod_name: Mid
      inst_name: u_mid1
```

## Documentation

Full usage, CLI reference, and architecture notes are in the [docs](docs/).
