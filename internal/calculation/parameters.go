package calculation

import (
	"encoding/json"
	"fmt"
	"math"
)

func NormalizeParameters(schema, supplied map[string]any) (map[string]any, error) {
	properties, ok := schema["properties"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%w: parameter schema has no properties", ErrInvalidRequest)
	}
	for name := range supplied {
		if _, exists := properties[name]; !exists {
			return nil, fmt.Errorf("%w: unknown parameter %s", ErrInvalidRequest, name)
		}
	}
	result := make(map[string]any, len(properties))
	for name, rawRule := range properties {
		rule, ok := rawRule.(map[string]any)
		if !ok {
			return nil, ErrInvalidRequest
		}
		value, exists := supplied[name]
		if !exists {
			value, exists = rule["default"]
		}
		if !exists {
			return nil, fmt.Errorf("%w: missing parameter %s", ErrInvalidRequest, name)
		}
		normalized, err := normalizeValue(rule, value)
		if err != nil {
			return nil, fmt.Errorf("%w: parameter %s", ErrInvalidRequest, name)
		}
		result[name] = normalized
	}
	return result, nil
}

func normalizeValue(rule map[string]any, value any) (any, error) {
	switch rule["type"] {
	case "integer":
		integer, ok := integerValue(value)
		if !ok || below(integer, rule["minimum"]) || above(integer, rule["maximum"]) {
			return nil, ErrInvalidRequest
		}
		return integer, nil
	case "number":
		number, ok := numberValue(value)
		if !ok || below(number, rule["minimum"]) || above(number, rule["maximum"]) {
			return nil, ErrInvalidRequest
		}
		return number, nil
	case "string":
		text, ok := value.(string)
		if !ok || !inEnum(text, rule["enum"]) {
			return nil, ErrInvalidRequest
		}
		return text, nil
	case "boolean":
		boolean, ok := value.(bool)
		if !ok {
			return nil, ErrInvalidRequest
		}
		return boolean, nil
	default:
		return nil, ErrInvalidRequest
	}
}

func integerValue(value any) (int64, bool) {
	switch number := value.(type) {
	case json.Number:
		result, err := number.Int64()
		return result, err == nil
	case float64:
		return int64(number), number == math.Trunc(number)
	case int:
		return int64(number), true
	case int64:
		return number, true
	default:
		return 0, false
	}
}

func numberValue(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		result, err := number.Float64()
		return result, err == nil
	case float64:
		return number, true
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func below(value any, bound any) bool {
	left, okLeft := numberValue(value)
	right, okRight := numberValue(bound)
	return okLeft && okRight && left < right
}

func above(value any, bound any) bool {
	left, okLeft := numberValue(value)
	right, okRight := numberValue(bound)
	return okLeft && okRight && left > right
}

func inEnum(value string, raw any) bool {
	items, ok := raw.([]any)
	if !ok {
		return true
	}
	for _, item := range items {
		if item == value {
			return true
		}
	}
	return false
}
