package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

func runExpr(tokens []string) (float64, error) {
	if len(tokens) == 0 {
		return 0, fmt.Errorf("error: empty expression")
	}

	// Parse first number
	var left float64
	var err error
	left, err = strconv.ParseFloat(tokens[0], 64)
	if err != nil {
		return 0, fmt.Errorf("error: bad number")
	}
	if math.IsInf(left, 0) || math.IsNaN(left) {
		return 0, fmt.Errorf("error: bad number")
	}

	i := 1
	for i < len(tokens) {
		op := tokens[i]
		if op != "+" && op != "-" && op != "*" && op != "/" && op != "x" {
			return 0, fmt.Errorf("error: bad operator")
		}
		if i+1 >= len(tokens) {
			return 0, fmt.Errorf("error: missing operand")
		}

		right, err := strconv.ParseFloat(tokens[i+1], 64)
		if err != nil {
			return 0, fmt.Errorf("error: bad number")
		}
		if math.IsInf(right, 0) || math.IsNaN(right) {
			return 0, fmt.Errorf("error: bad number")
		}

		switch op {
		case "+":
			left += right
		case "-":
			left -= right
		case "*", "x":
			left *= right
		case "/":
			if right == 0 {
				return 0, fmt.Errorf("error: division by zero")
			}
			left /= right
		}

		i += 2
	}

	return left, nil
}

func formatFloat(v float64) string {
	if v == 0 {
		return "0"
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}

func main() {
	if len(os.Args) > 1 {
		result, err := runExpr(os.Args[1:])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		fmt.Println(formatFloat(result))
		os.Exit(0)
		return
	}

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		tokens := strings.Fields(line)
		result, err := runExpr(tokens)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		fmt.Println(formatFloat(result))
	}
}
