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
		if err := runExpr(os.Args[1:]...); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		return
	}

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		tokens := strings.Fields(line)
		if len(tokens) == 0 {
			continue
		}
		if err := runExpr(tokens...); err != nil {
			fmt.Fprintln(os.Stdout, err)
		}
	}
}

func runExpr(tokens ...string) error {
	type token struct {
		num float64
		op  string
	}

	var parsed []token
	for _, t := range tokens {
		if t == "+" || t == "-" || t == "*" || t == "/" || t == "x" {
			parsed = append(parsed, token{op: t})
		} else {
			v, err := strconv.ParseFloat(t, 64)
			if err != nil || math.IsInf(v, 0) || math.IsNaN(v) {
				return fmt.Errorf("error: invalid number %q", t)
			}
			parsed = append(parsed, token{num: v})
		}
	}

	if len(parsed) == 0 {
		return fmt.Errorf("error: empty expression")
	}

	if parsed[0].op != "" {
		return fmt.Errorf("error: expression must start with a number")
	}
	if parsed[len(parsed)-1].op != "" {
		return fmt.Errorf("error: expression must end with a number")
	}
	for i := 1; i < len(parsed); i++ {
		if parsed[i].op == "" {
			return fmt.Errorf("error: two numbers in a row")
		}
	}

	// Evaluate: first pass for * and /, then + and -.
	result := parsed[0].num
	i := 1
	var addOps []string
	var addNums []float64

	for i < len(parsed) {
		if parsed[i].op == "*" || parsed[i].op == "/" || parsed[i].op == "x" {
			next := parsed[i+1].num
			if parsed[i].op == "/" && next == 0 {
				return fmt.Errorf("error: division by zero")
			}
			if parsed[i].op == "*" {
				result *= next
			} else if parsed[i].op == "/" {
				result /= next
			} else {
				result *= next
			}
			i += 2
		} else {
			addOps = append(addOps, parsed[i].op)
			addNums = append(addNums, parsed[i+1].num)
			i += 2
		}
	}

	for j, op := range addOps {
		if op == "+" {
			result += addNums[j]
		} else {
			result -= addNums[j]
		}
	}

	// Handle negative zero
	if result == 0 {
		result = 0
	}

	return fmt.Errorf("result: %v", formatFloat(result))
}

func formatFloat(v float64) string {
	s := strconv.FormatFloat(v, 'f', -1, 64)
	if s == "-0" {
		return "0"
	}
	return s
}
