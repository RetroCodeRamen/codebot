// Reference solution for the calc_go benchmark: a command-line calculator (see SPEC.md).
package main

import (
	"bufio"
	"errors"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

var errDivZero = errors.New("division by zero")

func isOp(t string) bool { return t == "+" || t == "-" || t == "*" || t == "x" || t == "/" }

func parseNumber(t string) (float64, bool) {
	v, err := strconv.ParseFloat(t, 64)
	if err != nil || math.IsInf(v, 0) || math.IsNaN(v) {
		return 0, false
	}
	return v, true
}

// eval evaluates number (op number)* with * and / before + and -, left to right.
func eval(tokens []string) (float64, error) {
	if len(tokens) == 0 {
		return 0, errors.New("empty expression")
	}
	if len(tokens)%2 == 0 {
		return 0, fmt.Errorf("malformed expression: %q", strings.Join(tokens, " "))
	}
	nums := []float64{}
	ops := []string{}
	for i, t := range tokens {
		if i%2 == 0 {
			v, ok := parseNumber(t)
			if !ok {
				return 0, fmt.Errorf("not a number: %q", t)
			}
			nums = append(nums, v)
		} else {
			if !isOp(t) {
				return 0, fmt.Errorf("not an operator: %q", t)
			}
			if t == "x" {
				t = "*"
			}
			ops = append(ops, t)
		}
	}
	// first pass: * and /
	terms := []float64{nums[0]}
	addOps := []string{}
	for i, op := range ops {
		n := nums[i+1]
		switch op {
		case "*":
			terms[len(terms)-1] *= n
		case "/":
			if n == 0 {
				return 0, errDivZero
			}
			terms[len(terms)-1] /= n
		default:
			terms = append(terms, n)
			addOps = append(addOps, op)
		}
	}
	// second pass: + and -
	r := terms[0]
	for i, op := range addOps {
		if op == "+" {
			r += terms[i+1]
		} else {
			r -= terms[i+1]
		}
	}
	return r, nil
}

func format(v float64) string {
	if v == 0 {
		v = 0 // negative zero prints as 0
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}

func main() {
	if len(os.Args) > 1 {
		v, err := eval(os.Args[1:])
		if err != nil {
			fmt.Fprintln(os.Stderr, "error: "+err.Error())
			if errors.Is(err, errDivZero) {
				os.Exit(1)
			}
			os.Exit(2)
		}
		fmt.Println(format(v))
		return
	}
	sc := bufio.NewScanner(os.Stdin)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) == 0 {
			continue
		}
		v, err := eval(fields)
		if err != nil {
			fmt.Println("error: " + err.Error())
			continue
		}
		fmt.Println(format(v))
	}
}
