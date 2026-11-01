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
