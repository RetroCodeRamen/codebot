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
		runExpr(os.Args[1:])
		return
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
		runExpr(tokens)
	}
}

func runExpr(tokens []string) {
	if len(tokens) == 0 {
		fmt.Fprintln(os.Stderr, "error: empty expression")
		os.Exit(2)
	}

	// Parse all tokens into a list of (value, isOperator).
	type token struct {
		value float64
		isOp  bool
		op    string
	}

	var parsed []token
	for _, t := range tokens {
		if t == "+" || t == "-" || t == "*" || t == "/" || t == "x" {
			parsed = append(parsed, token{isOp: true, op: t})
			continue
		}
		f, err := strconv.ParseFloat(t, 64)
		if err != nil || math.IsInf(f, 0) || math.IsNaN(f) {
			fmt.Fprintln(os.Stderr, "error: invalid number")
			os.Exit(2)
		}
		parsed = append(parsed, token{value: f, isOp: false})
	}

	if len(parsed) == 0 {
		fmt.Fprintln(os.Stderr, "error: empty expression")
		os.Exit(2)
	}

	// Validate: no two numbers in a row, no two ops in a row, no leading/trailing op.
	for i := 0; i < len(parsed); i++ {
		if i == 0 {
			if parsed[i].isOp {
				fmt.Fprintln(os.Stderr, "error: expression starts with operator")
				os.Exit(2)
			}
			continue
		}
		if i == len(parsed)-1 {
			if parsed[i].isOp {
				fmt.Fprintln(os.Stderr, "error: expression ends with operator")
				os.Exit(2)
			}
			continue
		}
		if parsed[i].isOp == parsed[i-1].isOp {
			fmt.Fprintln(os.Stderr, "error: two operators in a row")
			os.Exit(2)
		}
	}

	// Evaluate with precedence: * and / first (left to right), then + and - (left to right).
	var nums []float64
	var ops []string

	for i := 0; i < len(parsed); i++ {
		if parsed[i].isOp {
			ops = append(ops, parsed[i].op)
		} else {
			nums = append(nums, parsed[i].value)
		}
	}

	// Pass 1: handle * and / (and x which is multiplication)
	for i := 0; i < len(ops); i++ {
		if ops[i] == "*" || ops[i] == "/" || ops[i] == "x" {
			a := nums[i]
			b := nums[i+1]
			if ops[i] == "/" && b == 0 {
				fmt.Fprintln(os.Stderr, "error: division by zero")
				os.Exit(1)
			}
			var res float64
			if ops[i] == "*" || ops[i] == "x" {
				res = a * b
			} else {
				res = a / b
			}
			nums[i] = res
			nums = nums[:i+1]
			ops = ops[:i]
			i--
		}
	}

	// Pass 2: handle + and -
	for i := 0; i < len(ops); i++ {
		a := nums[i]
		b := nums[i+1]
		var res float64
		if ops[i] == "+" {
			res = a + b
		} else {
			res = a - b
		}
		nums[i] = res
		nums = nums[:i+1]
		ops = ops[:i]
		i--
	}

	result := nums[0]

	// Format output
	s := strconv.FormatFloat(result, 'f', -1, 64)
	if s == "-0" {
		s = "0"
	}
	fmt.Println(s)
}
