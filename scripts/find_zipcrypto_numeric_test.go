package main

import (
	"strings"
	"testing"
)

func TestPrintableASCIICharset(t *testing.T) {
	charset := printableASCIICharset()
	if len(charset) != 94 {
		t.Fatalf("charset length = %d, want 94", len(charset))
	}
	for value := byte(33); value <= 126; value++ {
		if strings.Count(charset, string(value)) != 1 {
			t.Fatalf("ASCII %q is not present exactly once", value)
		}
	}
}

func TestHabitPasswordOrders(t *testing.T) {
	tokens := []string{"42"}
	tests := map[string]string{
		"ADS": "aa42!",
		"ASD": "aa!42",
		"DAS": "42aa!",
		"DSA": "42!aa",
		"SAD": "!aa42",
		"SDA": "!42aa",
	}
	for order, want := range tests {
		got := habitPassword(
			0, order, 2, 1, "ab", "!?", tokens,
			make([]byte, 0, 16), make([]byte, 2), make([]byte, 1),
		)
		if string(got) != want {
			t.Errorf("order %s = %q, want %q", order, got, want)
		}
	}
}

func TestHabitPasswordCartesianEndpoints(t *testing.T) {
	tokens := []string{"1", "22"}
	got := habitPassword(
		15, "ADS", 2, 1, "ab", "!?", tokens,
		make([]byte, 0, 16), make([]byte, 2), make([]byte, 1),
	)
	if string(got) != "bb22?" {
		t.Fatalf("last Cartesian candidate = %q, want %q", got, "bb22?")
	}
}

func TestPowCount(t *testing.T) {
	got, err := powCount(52, 3)
	if err != nil || got != 140608 {
		t.Fatalf("powCount(52, 3) = %d, %v", got, err)
	}
}
