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

The code directory contains README.md and these files:

### go.mod
```
module calc

go 1.24
```

### main.go
```go
package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

func main() {
	if len(os.Args) > 1 {
		run(os.Args[1:])
		return
	}
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		run(strings.Fields(line))
	}
}

func run(tokens []string) {
	if len(tokens) == 0 {
		fmt.Fprintln(os.Stderr, "error: empty expression")
		os.Exit(2)
	}

	// Parse tokens into numbers and operators.
	type token struct {
		num  float64
		op   string
	}

	var parsed []token
	for i, t := range tokens {
		if t == "+" || t == "-" || t == "*" || t == "/" || t == "x" {
			if i == 0 {
				fmt.Fprintln(os.Stderr, "error: expression starts with operator")
				os.Exit(2)
				return
			}
			if i == len(tokens)-1 {
				fmt.Fprintln(os.Stderr, "error: expression ends with operator")
				os.Exit(2)
				return
			}
			parsed = append(parsed, token{op: t})
			continue
		}

		v, err := strconv.ParseFloat(t, 64)
		if err != nil || math.IsInf(v, 0) || math.IsNaN(v) {
			fmt.Fprintln(os.Stderr, "error: malformed expression")
			os.Exit(2)
			return
		}
		if i == 0 {
			parsed = append(parsed, token{num: v})
			continue
		}
		// Check previous token
		if parsed[len(parsed)-1].op != "" {
			fmt.Fprintln(os.Stderr, "error: two operators in a row")
			os.Exit(2)
			return
		}
		parsed = append(parsed, token{num: v})
	}

	if len(parsed) == 0 {
		fmt.Fprintln(os.Stderr, "error: empty expression")
		os.Exit(2)
		return
	}

	// Evaluate: handle multiplication/division first, then addition/subtraction.
	// We'll use a simple recursive descent or two-pass approach.
	// Since we have a mix of numbers and operators, let's just evaluate left to right
	// respecting precedence.

	// Convert to a flat list of numbers and operators.
	nums := make([]float64, 0, len(parsed))
	ops := make([]string, 0, len(parsed))

	for _, p := range parsed {
		if p.num != 0 {
			nums = append(nums, p.num)
		}
		if p.op != "" {
			ops = append(ops, p.op)
		}
	}

	// Now evaluate with precedence.
	// First pass: handle * and / (including x)
	// Second pass: handle + and -

	// We'll use a stack-based approach.
	// Actually, let's just do two passes.

	// Pass 1: evaluate * and /
	result := nums[0]
	i := 0
	for i < len(ops) {
		op := ops[i]
		if op == "*" || op == "/" || op == "x" {
			next := nums[i+1]
			if op == "/" && next == 0 {
				fmt.Fprintln(os.Stderr, "error: division by zero")
				os.Exit(1)
			}
			if op == "*" {
				result *= next
			} else if op == "/" {
				result /= next
			} else {
				result *= next
			}
			i++
		} else {
			break
		}
	}

	// Pass 2: evaluate + and -
	for i < len(ops) {
		op := ops[i]
		next := nums[i+1]
		if op == "+" {
			result += next
		} else {
			result -= next
		}
		i++
	}

	// Format and print result
	fmt.Println(formatFloat(result))
}

func formatFloat(v float64) string {
	if v == 0 {
		return "0"
	}
	s := strconv.FormatFloat(v, 'f', -1, 64)
	return s
}
```

All files compile, but 14 of 15 examples from the specification fail:
FAIL: ./calc 2 + 3 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 6 x 7 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 10 - 4 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 9 / 3 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 2 + 3 * 4 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 10 - 4 - 3 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 8 / 4 / 2 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 2 * 3 + 4 * 5 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc -2.5 + 1 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 0.1 + 0.2 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc -3 * -3 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 2.5 * 2 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 7 / 2 exited 2, expected 0 (stderr: error: two operators in a row)
FAIL: ./calc 1 / 3 exited 2, expected 0 (stderr: error: two operators in a row)
failing assertions: 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
14 of 15 assertions failed

Fix the code so that it meets the whole specification. Reply with the complete corrected contents of every file.