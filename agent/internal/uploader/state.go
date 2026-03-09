package uploader

import "sync/atomic"

// UploaderStatus is the interface consumed by the health module to report
// uploader readiness. Implementations must be safe for concurrent reads.
type UploaderStatus interface {
	Running() bool
	UploadsCompleted() int64
	UploadsFailed() int64
	BytesUploaded() int64
	LastError() string
}

// Running returns true when the uploader scan loop is actively running.
func (u *Uploader) Running() bool {
	return u.running.Load()
}

// UploadsCompleted returns the total number of fixtures successfully uploaded
// and cleaned up since the uploader started.
func (u *Uploader) UploadsCompleted() int64 {
	return u.uploadsCompleted.Load()
}

// UploadsFailed returns the total number of fixture upload attempts that
// exhausted all retries since the uploader started.
func (u *Uploader) UploadsFailed() int64 {
	return u.uploadsFailed.Load()
}

// BytesUploaded returns the total bytes successfully sent to S3.
func (u *Uploader) BytesUploaded() int64 {
	return u.bytesUploaded.Load()
}

// LastError returns the most recent upload error message, or "" if no error
// has occurred since the uploader started.
func (u *Uploader) LastError() string {
	v := u.lastError.Load()
	if v == nil {
		return ""
	}
	return v.(string)
}

// setLastError stores the most recent error message atomically.
func (u *Uploader) setLastError(msg string) {
	u.lastError.Store(msg)
}

// clearLastError resets the last error to empty.
func (u *Uploader) clearLastError() {
	u.lastError.Store("")
}

// incrCompleted atomically increments the successful upload counter and adds
// bytes to the total.
func (u *Uploader) incrCompleted(bytes int64) {
	u.uploadsCompleted.Add(1)
	u.bytesUploaded.Add(bytes)
}

// incrFailed atomically increments the failed upload counter.
func (u *Uploader) incrFailed() {
	u.uploadsFailed.Add(1)
}

// Compile-time check: *Uploader satisfies UploaderStatus.
var _ UploaderStatus = (*Uploader)(nil)

// uploaderState groups the atomic fields used by the Uploader. Embedded in
// Uploader to keep the main struct declaration focused on dependencies.
type uploaderState struct {
	running          atomic.Bool
	uploadsCompleted atomic.Int64
	uploadsFailed    atomic.Int64
	bytesUploaded    atomic.Int64
	lastError        atomic.Value // stores string
}
