package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

func formatFloat(v float64) string {
	if v == 0 {
		return "0"
	}
	s := strconv.FormatFloat(v, 'f', -1, 64)
	return s
}

func runExpr(tokens []string) (string, int) {
	if len(tokens) == 0 {
		return "", 2
	}

	// Parse first number
	val, err := strconv.ParseFloat(tokens[0], 64)
	if err != nil {
		return "", 2
	}
	if math.IsInf(val, 0) || math.IsNaN(val) {
		return "", 2
	}

	i := 1
	for i < len(tokens) {
		op := tokens[i]
		switch op {
		case "+":
			next, err := strconv.ParseFloat(tokens[i+1], 64)
			if err != nil {
				return "", 2
			}
			if math.IsInf(next, 0) || math.IsNaN(next) {
				return "", 2
			}
			val += next
			i += 2
		case "-":
			next, err := strconv.ParseFloat(tokens[i+1], 64)
			if err != nil {
				return "", 2
			}
			if math.IsInf(next, 0) || math.IsNaN(next) {
				return "", 2
			}
			val -= next
			i += 2
		case "*":
			next, err := strconv.ParseFloat(tokens[i+1], 64)
			if err != nil {
				return "", 2
			}
			if math.IsInf(next, 0) || math.IsNaN(next) {
				return "", 2
			}
			val *= next
			i += 2
		case "/":
			next, err := strconv.ParseFloat(tokens[i+1], 64)
			if err != nil {
				return "", 2
			}
			if math.IsInf(next, 0) || math.IsNaN(next) {
				return "", 2
			}
			if next == 0 {
				return "", 1
			}
			val /= next
			i += 2
		case "x":
			next, err := strconv.ParseFloat(tokens[i+1], 64)
			if err != nil {
				return "", 2
			}
			if math.IsInf(next, 0) || math.IsNaN(next) {
				return "", 2
			}
			val *= next
			i += 2
		default:
			return "", 2
		}
	}

	return formatFloat(val), 0
}

func main() {
	if len(os.Args) > 1 {
		out, code := runExpr(os.Args[1:])
		if code == 1 {
			fmt.Fprintln(os.Stderr, "error: division by zero")
			os.Exit(1)
		}
		if code == 2 {
			fmt.Fprintln(os.Stderr, "error: malformed expression")
			os.Exit(2)
		}
		fmt.Println(out)
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
		tokens := strings.Fields(line)
		if len(tokens) == 0 {
			continue
		}
		out, code := runExpr(tokens)
		if code == 1 {
			fmt.Fprintln(os.Stderr, "error: division by zero")
			continue
		}
		if code == 2 {
			fmt.Fprintln(os.Stderr, "error: malformed expression")
			continue
		}
		fmt.Println(out)
	}
}
