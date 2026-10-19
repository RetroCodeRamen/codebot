package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

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
		next, err := strconv.ParseFloat(tokens[i+1], 64)
		if err != nil {
			return "", 2
		}
		if math.IsInf(next, 0) || math.IsNaN(next) {
			return "", 2
		}
		i++
		switch op {
		case "+":
			val += next
		case "-":
			val -= next
		case "*":
			val *= next
		case "/":
			if next == 0 {
				return "error: division by zero", 1
			}
			val /= next
		case "x":
			val *= next
		default:
			return "", 2
		}
		i++
	}

	return strconv.FormatFloat(val, 'f', -1, 64), 0
}

func main() {
	if len(os.Args) > 1 {
		out, code := runExpr(os.Args[1:])
		if code == 1 {
			fmt.Fprintln(os.Stderr, out)
			os.Exit(1)
		}
		if code == 2 {
			fmt.Fprintln(os.Stderr, out)
			os.Exit(2)
		}
		fmt.Println(out)
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
		out, code := runExpr(tokens)
		if code == 1 {
			fmt.Fprintln(os.Stderr, out)
			os.Exit(1)
		}
		if code == 2 {
			fmt.Fprintln(os.Stderr, out)
			os.Exit(2)
		}
		fmt.Println(out)
	}
}
