package pythonclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type AlgorithmRef struct {
	Kind             string `json:"kind"`
	AlgorithmID      string `json:"algorithm_id"`
	AlgorithmVersion string `json:"algorithm_version"`
	SourceHash       string `json:"source_hash"`
}

type AlgorithmDefinition struct {
	AlgorithmRef
	Name            string           `json:"name"`
	InputSchema     string           `json:"input_schema"`
	ParameterSchema map[string]any   `json:"parameter_schema"`
	Outputs         []map[string]any `json:"outputs"`
	Warmup          map[string]any   `json:"warmup"`
	Causal          bool             `json:"causal"`
}

type JobStatus struct {
	RequestID string         `json:"request_id"`
	JobID     string         `json:"job_id"`
	Status    string         `json:"status"`
	Progress  float64        `json:"progress"`
	ResultRef string         `json:"result_ref,omitempty"`
	Error     map[string]any `json:"error,omitempty"`
}

type RemoteError struct {
	Status int
	Body   string
}

func (e *RemoteError) Error() string {
	return fmt.Sprintf("python returned HTTP %d: %s", e.Status, e.Body)
}

type Health struct {
	Status          string `json:"status"`
	ContractVersion string `json:"contract_version"`
	Services        map[string]struct {
		Status  string `json:"status"`
		Version string `json:"version"`
	} `json:"services"`
}

type Client struct {
	baseURL         string
	contractVersion string
	httpClient      *http.Client
}

func New(baseURL, contractVersion string, timeout time.Duration) *Client {
	return &Client{
		baseURL:         strings.TrimRight(baseURL, "/"),
		contractVersion: contractVersion,
		httpClient:      &http.Client{Timeout: timeout},
	}
}

func (c *Client) Health(ctx context.Context) Health {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/internal/v1/health", nil)
	if err != nil {
		return Health{Status: "unavailable", ContractVersion: c.contractVersion}
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return Health{Status: "unavailable", ContractVersion: c.contractVersion}
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Health{Status: "unavailable", ContractVersion: c.contractVersion}
	}
	var health Health
	if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
		return Health{Status: "unavailable", ContractVersion: c.contractVersion}
	}
	if health.ContractVersion != c.contractVersion {
		health.Status = "degraded"
	}
	if health.Status != "ok" && health.Status != "degraded" {
		health.Status = "unavailable"
	}
	return health
}

func (c *Client) String() string { return fmt.Sprintf("pythonclient(%s)", c.baseURL) }

func (c *Client) Algorithms(ctx context.Context, requestID, traceID string) ([]AlgorithmDefinition, error) {
	var response struct {
		Algorithms []AlgorithmDefinition `json:"algorithms"`
	}
	if err := c.doJSON(ctx, http.MethodGet, "/internal/v1/algorithms", requestID, traceID, nil, &response); err != nil {
		return nil, err
	}
	return response.Algorithms, nil
}

func (c *Client) Submit(ctx context.Context, kind, requestID, traceID string, payload any) (JobStatus, error) {
	var response JobStatus
	err := c.doJSON(ctx, http.MethodPost, "/internal/v1/job-submissions/"+kind, requestID, traceID, payload, &response)
	return response, err
}

func (c *Client) Job(ctx context.Context, jobID, requestID, traceID string) (JobStatus, error) {
	var response JobStatus
	err := c.doJSON(ctx, http.MethodGet, "/internal/v1/jobs/"+jobID, requestID, traceID, nil, &response)
	return response, err
}

func (c *Client) Cancel(ctx context.Context, jobID, requestID, traceID string) error {
	return c.doJSON(ctx, http.MethodPost, "/internal/v1/jobs/"+jobID+"/cancel", requestID, traceID, nil, &JobStatus{})
}

func (c *Client) doJSON(ctx context.Context, method, path, requestID, traceID string, payload, result any) error {
	var body io.Reader
	if payload != nil {
		data, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("X-Request-ID", requestID)
	req.Header.Set("X-Trace-ID", traceID)
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return &RemoteError{Status: resp.StatusCode, Body: string(data)}
	}
	return json.NewDecoder(resp.Body).Decode(result)
}

func (h Health) Version() string {
	if service, ok := h.Services["python-engine"]; ok && service.Version != "" {
		return service.Version
	}
	return h.ContractVersion
}
