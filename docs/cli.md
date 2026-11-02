# Command-line reference

```{eval-rst}
.. argparse::
   :module: svhier.cli
   :func: _build_parser
   :prog: svhier
```

Options and paths can be given in any order. Data (tree, `--yaml -`,
`--filelist -`) goes to **stdout**; the spinner, tables and diagnostics go to
**stderr**.

Language plugins add their own options: `-f`, `-I` and `-D` come from the
bundled slang plugin, and [the VHDL plugin](guide/vhdl.md) adds `--vhdl-std` and
`--vhdl-work`. `svhier --help` lists what's installed.
