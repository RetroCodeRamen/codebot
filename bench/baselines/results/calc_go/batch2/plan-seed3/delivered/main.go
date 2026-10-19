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
		if err := eval(os.Args[1:], false); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		os.Exit(0)
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
		if err := eval(tokens, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
	}
}

func eval(tokens []string, stdin bool) error {
	type token struct {
		num  float64
		op   string
	}

	var parsed []token
	for i, t := range tokens {
		if i == 0 {
			v, err := strconv.ParseFloat(t, 64)
			if err != nil || math.IsInf(v, 0) || math.IsNaN(v) {
				return fmt.Errorf("error: invalid number %q", t)
			}
			parsed = append(parsed, token{num: v})
			continue
		}

		op := normalizeOp(t)
		if op == "" {
			v, err := strconv.ParseFloat(t, 64)
			if err != nil || math.IsInf(v, 0) || math.IsNaN(v) {
				return fmt.Errorf("error: invalid token %q", t)
			}
			parsed = append(parsed, token{num: v})
			continue
		}

		if len(parsed) == 0 {
			return fmt.Errorf("error: expression starts with operator %q", t)
		}
		parsed = append(parsed, token{op: op})
	}

	if len(parsed) == 0 {
		return fmt.Errorf("error: empty expression")
	}

	// Check for two operators in a row.
	for i := 1; i < len(parsed); i++ {
		if parsed[i-1].op != "" && parsed[i].op != "" {
			return fmt.Errorf("error: two operators in a row")
		}
	}

	// Check for two numbers in a row.
	for i := 1; i < len(parsed); i++ {
		if parsed[i-1].num != 0 && parsed[i].num != 0 {
			return fmt.Errorf("error: two numbers in a row")
		}
	}

	// Evaluate using precedence: * and / first, then + and -.
	result := []token{parsed[0]}
	for i := 1; i < len(parsed); i++ {
		if parsed[i].op == "*" || parsed[i].op == "/" {
			last := result[len(result)-1]
			if parsed[i].op == "*" {
				result[len(result)-1] = token{num: last.num * parsed[i].num}
			} else {
				if parsed[i].num == 0 {
					return fmt.Errorf("error: division by zero")
				}
				result[len(result)-1] = token{num: last.num / parsed[i].num}
			}
		} else {
			result = append(result, parsed[i])
		}
	}

	// Pass 2: handle + and -
	for i := 1; i < len(result); i++ {
		if result[i].op == "+" {
			result[i-1] = token{num: result[i-1].num + result[i].num}
		} else if result[i].op == "-" {
			result[i-1] = token{num: result[i-1].num - result[i].num}
		}
	}

	// Format the result.
	v := result[0].num
	if v == 0 {
		v = 0
	}
	fmt.Println(formatFloat(v))
	return nil
}

func normalizeOp(s string) string {
	switch s {
	case "+", "-", "*", "x":
		return s
	default:
		return ""
	}
}

func formatFloat(v float64) string {
	s := strconv.FormatFloat(v, 'f', -1, 64)
	if s == "-0" {
		return "0"
	}
	return s
}
