package health

import (
	"encoding/json"
	"net/http"
	"testing"
)

func TestLive_ReturnsOK(t *testing.T) {
	h := &Health{}

	code, body := h.Live()

	if code != http.StatusOK {
		t.Errorf("Live() code = %d, want %d", code, http.StatusOK)
	}
	if body != "ok" {
		t.Errorf("Live() body = %v, want ok", body)
	}
}

func TestReady_ReturnsOKWithEmptyChecks(t *testing.T) {
	h := &Health{}

	code, report := h.Ready()

	if code != http.StatusOK {
		t.Errorf("Ready() code = %d, want %d", code, http.StatusOK)
	}
	if report.Status != string(StatusOK) {
		t.Errorf("Ready() report.Status = %q, want %q", report.Status, StatusOK)
	}
	if report.Checks != nil && len(report.Checks) != 0 {
		t.Errorf("Ready() report.Checks = %v, want []", report.Checks)
	}
}

func TestReady_ReportSerializesCorrectly(t *testing.T) {
	report := ReadinessReport{
		Status: string(StatusOK),
		Checks: []CheckResult{},
	}

	data, err := json.Marshal(report)
	if err != nil {
		t.Fatalf("Marshal(report): %v", err)
	}
	if got := string(data); got != `{"status":"ok","checks":[]}` {
		t.Errorf("Marshal(report) = %q, want {\"status\":\"ok\",\"checks\":[]}", got)
	}
}

func TestReady_ReportWithChecksSerializesCorrectly(t *testing.T) {
	report := ReadinessReport{
		Status: string(StatusDegraded),
		Checks: []CheckResult{
			{Name: "spool", Status: "fail", Message: "spool usage 95%"},
		},
	}

	data, err := json.Marshal(report)
	if err != nil {
		t.Fatalf("Marshal(report): %v", err)
	}
	want := `{"status":"degraded","checks":[{"name":"spool","status":"fail","message":"spool usage 95%"}]}`
	if got := string(data); got != want {
		t.Errorf("Marshal(report) = %q, want %q", got, want)
	}
}

func TestLiveHandler_DelegatesToLive(t *testing.T) {
	h := &Health{}

	code, body := LiveHandler(h)

	if code != http.StatusOK {
		t.Errorf("LiveHandler() code = %d, want %d", code, http.StatusOK)
	}
	if body != "ok" {
		t.Errorf("LiveHandler() body = %v, want ok", body)
	}
}

func TestReadyHandler_DelegatesToReady(t *testing.T) {
	h := &Health{}

	code, report := ReadyHandler(h)

	if code != http.StatusOK {
		t.Errorf("ReadyHandler() code = %d, want %d", code, http.StatusOK)
	}
	if report.Status != string(StatusOK) {
		t.Errorf("ReadyHandler() report.Status = %q, want %q", report.Status, StatusOK)
	}
}
