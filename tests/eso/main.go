package main

import (
	"encoding/json"
	"fmt"
	"os"

	esapi "github.com/external-secrets/external-secrets/apis/externalsecrets/v1"
	template "github.com/external-secrets/external-secrets/runtime/template/v2"
	corev1 "k8s.io/api/core/v1"
)

func run() error {
	var input struct {
		Data       map[string]string `json:"data"`
		Parameters map[string]string `json:"parameters"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		return err
	}
	data, parameters := map[string][]byte{}, map[string][]byte{}
	for k, v := range input.Data {
		data[k] = []byte(v)
	}
	for k, v := range input.Parameters {
		parameters[k] = []byte(v)
	}
	secret := &corev1.Secret{}
	if err := template.Execute(data, parameters, esapi.TemplateScopeValues, "data", secret); err != nil {
		return err
	}
	output := map[string]string{}
	for k, v := range secret.Data {
		output[k] = string(v)
	}
	return json.NewEncoder(os.Stdout).Encode(output)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
