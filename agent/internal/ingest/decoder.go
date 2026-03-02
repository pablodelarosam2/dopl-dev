package ingest

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
)

// ErrInvalidJSON is returned when the request body cannot be decoded as valid JSON.
var ErrInvalidJSON = errors.New("invalid JSON")

// BodyTooLargeError is returned when the request body exceeds the configured limit.
type BodyTooLargeError struct {
	Max int64
}

func (e *BodyTooLargeError) Error() string {
	return fmt.Sprintf("request body exceeds limit of %d bytes", e.Max)
}

// DecodeRequest decodes an IngestRequest from r, rejecting unknown top-level
// fields and validating the result.
func DecodeRequest(r io.Reader) (IngestRequest, error) {
	var request IngestRequest
	dec := json.NewDecoder(r)
	dec.DisallowUnknownFields()
	if err := dec.Decode(&request); err != nil {
		return IngestRequest{}, fmt.Errorf("%w: %s", ErrInvalidJSON, err)
	}
	if err := request.Validate(); err != nil {
		return IngestRequest{}, err
	}
	return request, nil
}

// DecodeRequestFromHTTP reads and decodes an IngestRequest from an HTTP request,
// enforcing maxBodyBytes. Returns BodyTooLargeError if the body exceeds the limit.
func DecodeRequestFromHTTP(r *http.Request, w http.ResponseWriter, maxBodyBytes int64) (IngestRequest, error) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	req, err := DecodeRequest(r.Body)
	if err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			return IngestRequest{}, &BodyTooLargeError{Max: maxBodyBytes}
		}
		return IngestRequest{}, err
	}
	return req, nil
}
