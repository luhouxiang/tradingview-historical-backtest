package api

//go:generate node ../../web/scripts/bundle-openapi.mjs
//go:generate go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.8.0 --config ../../contracts/oapi-codegen.yaml ../../contracts/generated/openapi.dereferenced.yaml
