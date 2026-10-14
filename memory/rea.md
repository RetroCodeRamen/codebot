# REA cheat sheet (only read in reverse-engineering projects)
IMPORTANT: the rea_* names (rea_open_binary, rea_search_strings, ...) are TOOLS you call directly like read/edit, NOT shell commands. In bash use only the `rea <command>` forms listed below (there is no `rea open`). Never run `rea --help` (huge output); use `rea <command> --help --token-limit 600` if needed. Ghidra is installed and working; if a rea call fails, show the exact error in your notes before concluding anything, and never write "no provider installed" without running `rea providers`.
Run `rea <command> --help` for any command. Targets: ELF/PE/Mach-O native, APK/IPA/zip packages, ASAR/JS trees, .NET assemblies, firmware.
CLI OUTPUT TRICK (important): every rea CLI result begins with ~40 lines of provider/header metadata, so a small --token-limit shows ONLY that header. ALWAYS add `--filter-output normalized_result` to get just the answer (usually 1-30 short lines). Then add `--token-limit 700` and page with `--token-offset 700` if cut off.
Always add `--token-limit 1500` (page with `--token-offset 1500`, `--token-offset 3000`...). `--filter-output a,b.c` trims fields. `--token-count` prints size only.
Repeat calls on one target: add `--snapshot <state>/rea.snap`.

## Route by target
- Native executable/library: `rea analyze <path>` (overview) -> `rea search <path> <pattern>` (strings + function names) -> `rea decompile <path> <name|addr>` -> `rea xrefs <path> <addr>` / `rea trace <path> <literal>` -> `rea function <path> <name|addr>` (full dossier) -> `rea instructions <path> <addr>` (raw asm).
- ELF layout/symbols/mitigations: `rea inspect-binary-layout <path>`. Mach-O: `rea inspect-macho <path>`.
- Archive/package (zip, APK, IPA): `rea inspect-artifact <path>`; extract: `rea extract-artifact <path>`.
- APK code: `rea inspect-android-package <path>`, `rea search-android-classes <path> <query>`, `rea inspect-android-class <path> <Class>`, `rea inspect-android-method <path> <Class> <method>`, `rea trace-android-references <path> <Class>`.
- JavaScript/Electron app or ASAR: `rea analyze-javascript-application <path>`, `rea recover-javascript-sources <path> <outDir>`.
- .NET (PE/CLI): `rea inspect-managed-artifact <path>`, `rea inspect-managed-members <path>`.
- Firmware: `rea inspect-firmware-regions <path>`, `rea extract-firmware <path> <outDir>`.
- Compare versions: `rea compare-application-versions`, `rea compare-managed-members`, `rea compare <left> <right>` (Evidence bundles).

## Method
1. Identify format and size first (analyze / inspect-artifact). 2. Search strings for the feature, find the function that uses them, decompile it, follow callers/callees. 3. Write each finding right away with address + function name. 4. Unknown or unsupported? say so; do not guess.
## Forbidden unless the plan allows runtime work
`rea capture-*`, `rea observe-*`, `rea setup`, `rea update`, `rea uninstall`, adb, gdb, lldb, strace, running the target.
## MCP tool arguments: rea_search_strings and rea_search_procedures REQUIRE {"pattern": "text"} (add "mode":"regex" for regex). rea_procedure_pseudo_code takes {"procedure": "name or 0xaddress"}.
## MCP tools available (stateful session): rea_open_binary first, then rea_binary_overview, rea_search_procedures, rea_search_strings, rea_procedure_pseudo_code, rea_procedure_callers, rea_procedure_callees; rea_close_binary when finished.

## Worked example (verified): find what a license check compares against
T=<absolute target path>
1. rea search $T '.+' --mode regex --filter-output normalized_result --token-limit 900      # list ALL strings (about 800 tokens for a small binary). NOTE: the default match is LITERAL, so `rea search $T .` only finds strings containing a dot. Always pass --mode regex for patterns like '.+' or 'lic.*'. The program's own strings are near the top by address (e.g. 0x102004 ZEBRA-4417-QUILL, 0x102015 usage text, LICENSE-OK). Page with --token-offset 900.
2. rea search $T licen --kind procedures --filter-output normalized_result      # FUNCTION names containing "licen" (without --kind procedures it searches strings). Also try: check, verify, valid, key, auth. List all functions: rea search $T '.+' --kind procedures --mode regex --filter-output normalized_result
3. rea decompile $T validate_license --filter-output normalized_result            # shows e.g. strcmp(param_1, expected): `expected` is a global POINTER to a string, so the key is not inline.
4. rea xrefs $T 0x102004 --filter-output normalized_result                        # who references the string address: code addresses (users) and a data address (the `expected` pointer)
5. Write the finding at once: function name + address, what it compares, the string value and its address. Add `[src: rea decompile / rea search]` at the end of each bullet.
If a decompile shows a variable (DAT_xxx / name) instead of a literal: it is a pointer; list strings (step 1) and use xrefs on candidate strings to see which one is stored in that variable.
