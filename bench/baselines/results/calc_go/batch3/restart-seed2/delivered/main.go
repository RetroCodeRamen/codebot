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
		if err := runExpr(os.Args[1:], os.Stdout, os.Stderr, os.Exit); err != nil {
			os.Exit(1)
		}
		return
	}

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if err := runExpr(strings.Fields(line), os.Stdout, os.Stderr, os.Exit); err != nil {
			os.Exit(1)
		}
	}
}

func runExpr(tokens []string, stdout, stderr *os.File, exit func(int)) error {
	if len(tokens) == 0 {
		fmt.Fprintln(stderr, "error: empty expression")
		return fmt.Errorf("empty expression")
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
				fmt.Fprintln(stderr, "error: expression starts with operator")
				return fmt.Errorf("starts with operator")
			}
			if len(parsed) == 0 {
				fmt.Fprintln(stderr, "error: expression starts with operator")
				return fmt.Errorf("starts with operator")
			}
			parsed = append(parsed, token{op: t})
		} else {
			v, err := strconv.ParseFloat(t, 64)
			if err != nil || math.IsInf(v, 0) || math.IsNaN(v) {
				fmt.Fprintln(stderr, "error: malformed number")
				return fmt.Errorf("malformed number")
			}
			if len(parsed) == 0 {
				parsed = append(parsed, token{num: v})
			} else {
				last := parsed[len(parsed)-1]
				if last.num != 0 {
					fmt.Fprintln(stderr, "error: two numbers in a row")
					return fmt.Errorf("two numbers")
				}
				if last.op != "" {
					fmt.Fprintln(stderr, "error: two operators in a row")
					return fmt.Errorf("two operators")
				}
				parsed = append(parsed, token{num: v})
			}
		}
	}

	if len(parsed) == 0 {
		fmt.Fprintln(stderr, "error: empty expression")
		return fmt.Errorf("empty expression")
	}

	// Must end with a number.
	last := parsed[len(parsed)-1]
	if last.op != "" {
		fmt.Fprintln(stderr, "error: expression ends with operator")
		return fmt.Errorf("ends with operator")
	}

	// Evaluate: handle multiplication/division before addition/subtraction, left to right.
	type term struct {
		num  float64
		op   string
	}

	var terms []term
	for _, t := range parsed {
		if t.op == "" {
			terms = append(terms, term{num: t.num})
		} else {
			// Check if last term is a number (start of new term) or operator (continue).
			lastT := terms[len(terms)-1]
			if lastT.op != "" {
				terms = append(terms, term{op: t.op})
			} else {
				terms = append(terms, term{num: t.num, op: t.op})
			}
		}
	}

	// Now evaluate terms left to right.
	var result float64
	for i, t := range terms {
		if i == 0 {
			result = t.num
		} else {
			var op string
			var num float64
			if t.op != "" {
				op = t.op
				num = t.num
			} else {
				op = "+"
				num = t.num
			}
			switch op {
			case "+":
				result = result + num
			case "-":
				result = result - num
			case "*":
				result = result * num
			case "/", "x":
				if num == 0 {
					fmt.Fprintln(stderr, "error: division by zero")
					return fmt.Errorf("division by zero")
				}
				result = result / num
			}
		}
	}

	// Format result.
	s := strconv.FormatFloat(result, 'f', -1, 64)
	if s == "-0" {
		s = "0"
	}
	fmt.Fprintln(stdout, s)
	return nil
}
