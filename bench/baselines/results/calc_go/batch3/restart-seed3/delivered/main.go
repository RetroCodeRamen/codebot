package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

func isOperator(s string) bool {
	switch s {
	case "+", "-", "*", "x", "/":
		return true
	default:
		return false
	}
}

func isNumber(s string) bool {
	if s == "" {
		return false
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return false
	}
	if math.IsInf(f, 0) || math.IsNaN(f) {
		return false
	}
	return true
}

func evaluate(expr string) (string, error) {
	tokens := strings.Fields(expr)
	if len(tokens) == 0 {
		return "", fmt.Errorf("error: malformed expression")
	}

	// Check for leading or trailing operator
	if isOperator(tokens[0]) || isOperator(tokens[len(tokens)-1]) {
		return "", fmt.Errorf("error: malformed expression")
	}

	// Parse first number
	val, err := strconv.ParseFloat(tokens[0], 64)
	if err != nil {
		return "", fmt.Errorf("error: malformed expression")
	}
	if math.IsInf(val, 0) || math.IsNaN(val) {
		return "", fmt.Errorf("error: malformed expression")
	}

	// Parse remaining tokens
	for i := 1; i < len(tokens); i++ {
		if isOperator(tokens[i]) {
			// Expect a number next
			if i+1 >= len(tokens) {
				return "", fmt.Errorf("error: malformed expression")
			}
			if !isNumber(tokens[i+1]) {
				return "", fmt.Errorf("error: malformed expression")
			}
			next, err := strconv.ParseFloat(tokens[i+1], 64)
			if err != nil {
				return "", fmt.Errorf("error: malformed expression")
			}
			if math.IsInf(next, 0) || math.IsNaN(next) {
				return "", fmt.Errorf("error: malformed expression")
			}

			switch tokens[i] {
			case "+":
				val += next
			case "-":
				val -= next
			case "*", "x":
				val *= next
			case "/":
				if next == 0 {
					return "", fmt.Errorf("error: division by zero")
				}
				val /= next
			}
			i++ // skip the number
		} else {
			// Two numbers in a row
			return "", fmt.Errorf("error: malformed expression")
		}
	}

	return strconv.FormatFloat(val, 'f', -1, 64), nil
}

func main() {
	if len(os.Args) > 1 {
		expr := strings.Join(os.Args[1:], " ")
		result, err := evaluate(expr)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Println(result)
		os.Exit(0)
	}

	scanner := bufio.NewReader(os.Stdin)
	for {
		line, err := scanner.ReadString('\n')
		if err != nil {
			break
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		expr := strings.Join(strings.Fields(line), " ")
		result, err := evaluate(expr)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			continue
		}
		fmt.Println(result)
	}
	os.Exit(0)
}
