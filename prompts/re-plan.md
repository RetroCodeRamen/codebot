
REVERSE-ENGINEERING PROJECT (REA). The goal is to analyze a shipped artifact (binary, package, app bundle, firmware, JavaScript bundle), not to write a normal application. Adjust the grilling: replace checklist areas 4-7 and 9-10 with the items below (areas 1, 2, 8, 11, 12, 13 still apply):
- Ask for: the exact absolute path(s) of the target(s); the question to answer (explain a feature, find where X happens, compare two versions, recover a file format or protocol, reconstruct source); the deliverable (default {{WORKDIR}}/findings.md, optionally reconstructed code); and confirm the user owns the target or is authorized to analyze it.
- Static analysis only. Do NOT plan to execute the target or run runtime capture unless the user explicitly asks for it.
- Tasks are small investigation steps (about 5-8 tool calls each). Each ends with an evidence-backed finding appended to the findings file: function names/addresses, strings, call relationships actually retrieved. Include early tasks to identify the format and get an overview, and a final task that summarizes the answer to the user's question.
- verify.sh should check the deliverable: findings file exists, has the required section headings, and (if code is reconstructed) that it compiles or passes tests.
- Read {{CODEBOT_HOME}}/memory/rea.md before planning.
- Make each task name the exact first tools/commands to use (for example "rea search <path> licen --filter-output normalized_result", then "rea decompile <path> <name>"), and the exact section heading to write. A 9B model follows concrete steps far better than goals.
- Tell the executor to follow pointers: decompiled variables are pointers to data; use string listing and xrefs.
