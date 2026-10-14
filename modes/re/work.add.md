
REVERSE-ENGINEERING MODE (REA). First read {{MODE_DIR}}/memory.md (cheat sheet). Rules:
- Evidence only: every claim you write must cite an address, function, string or file you actually retrieved this session. If you did not see it, do not state it.
- Static analysis only. Never execute the target. Never run `rea capture-*`, `rea observe-*`, adb, gdb, lldb, strace. If the plan truly requires runtime execution, write BLOCKED asking the user.
- Tools: the rea_* MCP tools (start with rea_open_binary on the target path) and the `rea` CLI through bash. Outputs can be huge: with the CLI ALWAYS add `--token-limit 1500` and page with `--token-offset`; use `--filter-output` to select fields. For repeated CLI calls on the same target add `--snapshot {{STATE}}/rea.snap` (turns a 15 s analysis into 1 s).
- Never paste big dumps into files; record short findings with addresses.
- Append findings to the findings file named in PLAN.md; mark the task done only after the finding is written.
- ALWAYS add `--filter-output normalized_result` to rea CLI commands (otherwise a token-limit only shows a ~40 line header). See the worked example in rea.md.
- Write findings incrementally: after each useful tool result, append one bullet to the findings file immediately; do not wait until the end. Every bullet ends with `[src: tool or command]`. Write facts only: no sentences guessing what the program "likely" does, no sizes, counts or linking claims you did not see in a tool result.
- A decompiled variable such as `expected` or DAT_xxx is a pointer: list strings and use xrefs to find the string it points to.
- Before checking a task off, run `grep` on the findings file to confirm the required section really exists.
- Do not repeat a search or decompile you already did this iteration; move to the next step of the worked example.
