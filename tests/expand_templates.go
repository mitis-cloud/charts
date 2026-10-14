package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"text/template"
)

func expand(value any, params map[string]any) (any, error) {
	switch value := value.(type) {
	case string:
		t, err := template.New("resource").Funcs(template.FuncMap{
			"fromJson": func(text string) (any, error) {
				var value any
				err := json.Unmarshal([]byte(text), &value)
				return value, err
			},
		}).Option("missingkey=error").Parse(value)
		if err != nil {
			return nil, err
		}
		var b bytes.Buffer
		if err := t.Execute(&b, params); err != nil {
			return nil, err
		}
		return b.String(), nil
	case []any:
		result := make([]any, len(value))
		for i, item := range value {
			var err error
			result[i], err = expand(item, params)
			if err != nil {
				return nil, err
			}
		}
		return result, nil
	case map[string]any:
		result := make(map[string]any, len(value))
		for key, item := range value {
			var err error
			result[key], err = expand(item, params)
			if err != nil {
				return nil, err
			}
		}
		return result, nil
	default:
		return value, nil
	}
}

func run() error {
	var input struct {
		Template map[string]any   `json:"template"`
		Elements []map[string]any `json:"elements"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		return err
	}
	var applications []any
	for _, element := range input.Elements {
		app, err := expand(input.Template, element)
		if err != nil {
			return err
		}
		applications = append(applications, app)
	}
	return json.NewEncoder(os.Stdout).Encode(applications)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
