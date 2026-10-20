Write the code this specification describes.

The code directory contains README.md and these files:

### go.mod
```
module calc

go 1.24
```

Specification (SPEC.md):

# Command-line calculator in Go

Build a command-line calculator in Go in the root of the code directory. The Go module `calc` already exists (`go.mod`). Use the Go standard
library only: no third-party modules, no network. All code is in package `main`; `go build -o calc .` must produce the program `calc`.
The calculator works with 64-bit floating-point numbers (`float64`).

## Requirements

1. The program `calc` builds with `go build -o calc .` and takes an arithmetic expression as its command-line arguments, one number or operator
   per argument: `calc 2 + 3` prints `5`. A single number is a valid expression: `calc 7` prints `7`.

2. It supports the four operators `+`, `-`, `*` and `/`. The letter `x` is accepted as another way to write `*` (so the shell does not have to
   quote it): `calc 6 x 7` prints `42`, `calc 10 - 4` prints `6`, `calc 9 / 3` prints `3`.

3. Multiplication and division are done before addition and subtraction; operators of the same kind are applied left to right:
   `calc 2 + 3 * 4` prints `14`, `calc 10 - 4 - 3` prints `3`, `calc 8 / 4 / 2` prints `1`, `calc 2 * 3 + 4 * 5` prints `26`.

4. Numbers may be integers or decimals and may be negative: `calc -2.5 + 1` prints `-1.5`, `calc 0.1 + 0.2` prints `0.30000000000000004`,
   `calc -3 * -3` prints `9`. A number is anything Go's `strconv.ParseFloat(s, 64)` accepts that is not infinite or NaN.

5. The result is printed on one line, followed by a newline, with no trailing zeros and no exponent: a whole number has no decimal point
   (`calc 2.5 * 2` prints `5`), other values use the shortest decimal form that reads back as the same float64 (`calc 7 / 2` prints `3.5`,
   `calc 1 / 3` prints `0.3333333333333333`). This is `strconv.FormatFloat(v, 'f', -1, 64)`. Negative zero is printed as `0`.

6. Errors. Division by zero prints `error: division by zero` to standard error, prints nothing to standard output, and exits with status 1.
   A malformed expression (a token that is neither a number nor an operator, two numbers in a row, two operators in a row, an expression that
   starts or ends with an operator) prints a message starting with `error:` to standard error, nothing to standard output, and exits with
   status 2. A successful calculation exits with status 0.

7. With no arguments, `calc` reads expressions from standard input, one per line, with numbers and operators separated by spaces (`2 + 3`).
   For each line it prints the result, or the error message (starting with `error:`) on standard output instead, and continues with the next
   line. Empty lines are skipped. At the end of the input it exits with status 0.

Reply with the complete contents of main.go and any other .go file the program needs, and no other file.