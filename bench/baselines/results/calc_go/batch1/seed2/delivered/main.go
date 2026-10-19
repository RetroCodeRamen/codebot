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
			os.Exit(1)
		}
		fmt.Println(formatFloat(result))
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
			fmt.Fprintln(os.Stderr, err)
		} else {
			fmt.Println(formatFloat(result))
		}
	}
}

var result float64

func runExpr(tokens ...string) error {
	type token struct {
		value float64
		isOp  bool
		opStr string
	}

	var parsed []token
	for _, t := range tokens {
		if t == "+" || t == "-" || t == "*" || t == "/" || t == "x" {
			parsed = append(parsed, token{isOp: true, opStr: t})
			continue
		}
		v, err := strconv.ParseFloat(t, 64)
		if err != nil {
			return fmt.Errorf("error: malformed expression")
		}
		if math.IsInf(v, 0) || math.IsNaN(v) {
			return fmt.Errorf("error: malformed expression")
		}
		parsed = append(parsed, token{value: v, isOp: false})
	}

	if len(parsed) == 0 {
		return fmt.Errorf("error: empty expression")
	}

	// Validate: no two numbers in a row, no two operators in a row
	for i := 0; i < len(parsed)-1; i++ {
		if parsed[i].isOp == parsed[i+1].isOp {
			return fmt.Errorf("error: malformed expression")
		}
	}

	// Validate: doesn't start or end with operator
	if parsed[0].isOp {
		return fmt.Errorf("error: malformed expression")
	}
	if parsed[len(parsed)-1].isOp {
		return fmt.Errorf("error: malformed expression")
	}

	// Convert to slices
	nums := make([]float64, len(parsed))
	ops := make([]string, len(parsed)-1)
	for i, p := range parsed {
		nums[i] = p.value
	}
	for i, p := range parsed {
		if p.isOp {
			ops[i] = p.opStr
		}
	}

	// First pass: handle * and / left to right
	for i := 0; i < len(ops); i++ {
		if ops[i] == "*" || ops[i] == "/" {
			if ops[i] == "/" && nums[i+1] == 0 {
				return fmt.Errorf("error: division by zero")
			}
			if ops[i] == "/" {
				nums[i] = nums[i] / nums[i+1]
			} else {
				nums[i] = nums[i] * nums[i+1]
			}
			ops = append(ops[:i], ops[i+1:]...)
			nums = append(nums[:i+1], nums[i+2:]...)
			i--
		}
	}

	// Second pass: handle + and - left to right
	for i := 0; i < len(ops); i++ {
		if ops[i] == "+" || ops[i] == "-" {
			if ops[i] == "+" {
				nums[i] = nums[i] + nums[i+1]
			} else {
				nums[i] = nums[i] - nums[i+1]
			}
			ops = append(ops[:i], ops[i+1:]...)
			nums = append(nums[:i+1], nums[i+2:]...)
			i--
		}
	}

	result = nums[0]
	if result == 0 {
		result = 0
	}
	return nil
}

func formatFloat(v float64) string {
	if v == 0 {
		return "0"
	}
	s := strconv.FormatFloat(v, 'f', -1, 64)
	return s
}
