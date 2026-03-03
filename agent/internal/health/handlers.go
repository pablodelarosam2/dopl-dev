package health

import (
	"encoding/json"
	"net/http"
)

// LiveHandler returns an http.HandlerFunc that always responds 200 OK.
func LiveHandler(h *Health) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		code, body := h.Live()
		writeJSON(w, code, body)
	}
}

// ReadyHandler returns an http.HandlerFunc that evaluates all readiness checks
// and responds 200 when healthy, 503 when any check is degraded.
func ReadyHandler(h *Health) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		code, report := h.Ready()
		writeJSON(w, code, report)
	}
}

// writeJSON serialises v as JSON and writes it to w with the given status code.
// On serialisation failure it falls back to a plain-text 500 so the handler
// never silently swallows an internal error.
func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	data, err := json.Marshal(v)
	if err != nil {
		http.Error(w, `{"error":"internal serialisation error"}`, http.StatusInternalServerError)
		return
	}
	w.WriteHeader(code)
	_, _ = w.Write(data)
}
