package main

import (
	"fmt"
	"os"

	"offerpilot/backend/internal/knowledge"
)

func main() {
	root := os.Args[1]
	index, err := knowledge.Load(root)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("entries=%d\n", index.Len())
	for _, q := range os.Args[2:] {
		results := index.Search(q, 10)
		fmt.Printf("QUERY=%s\n", q)
		for _, e := range results {
			fmt.Printf("  %s %.6f %s\n", e.ID, e.Score, e.Question)
		}
	}
}
