package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWithWebUIServesAssetsAndKeepsAPIRoutes(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "index.html"), []byte("<main>TVBT release</main>"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "assets"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "assets", "app.js"), []byte("releaseAsset"), 0o600); err != nil {
		t.Fatal(err)
	}
	apiHandler := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("apiResponse")) })
	handler, err := withWebUI(apiHandler, root)
	if err != nil {
		t.Fatal(err)
	}

	for path, expected := range map[string]string{
		"/":              "TVBT release",
		"/unknown/route": "TVBT release",
		"/assets/app.js": "releaseAsset",
		"/api/v1/health": "apiResponse",
	} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), expected) {
			t.Fatalf("%s: status=%d body=%q", path, recorder.Code, recorder.Body.String())
		}
	}
}

func TestWithWebUIRequiresIndex(t *testing.T) {
	if _, err := withWebUI(http.NotFoundHandler(), t.TempDir()); err == nil {
		t.Fatal("expected a missing index error")
	}
}
