package health

import (
	"net/http"
)

func LiveHandler(h *Health) (code int, body any) {
	return h.Live()
}

func ReadyHandler(h *Health) (code int, report ReadinessReport) {
	return h.Ready()
}