// Package uploader transfers committed spool fixtures to S3 via PutObject.
//
// The uploader runs as a background subsystem: a scan loop periodically lists
// committed fixtures from the spool, dispatches new ones to a pool of worker
// goroutines, and deletes them from disk after a confirmed upload. In-flight
// tracking prevents duplicate uploads when a fixture is still being processed
// by a worker.
package uploader

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"time"

	"github.com/dopl-dev/agent/internal/spool"
)

// S3Client is the minimal interface the uploader needs from an S3 client.
// The bucket is baked in at construction time (see s3ClientAdapter in main.go),
// so individual calls only specify the key and body.
type S3Client interface {
	PutObject(ctx context.Context, params *PutObjectInput) error
}

// PutObjectInput describes a single upload request. The target bucket is owned
// by the S3Client implementation, not passed per-call.
type PutObjectInput struct {
	Key         string
	Body        io.Reader
	ContentType string
}

// SpoolLister provides read access to committed fixtures on disk. Satisfied by
// *spool.Spool.
type SpoolLister interface {
	List() ([]spool.FixtureInfo, error)
}

// fixtureJob is the unit of work dispatched to upload workers.
type fixtureJob struct {
	info spool.FixtureInfo
}

// Uploader coordinates the transfer of committed spool fixtures to S3.
type Uploader struct {
	client S3Client
	lister SpoolLister
	cfg    UploaderConfig
	log    *slog.Logger

	// jobs is the buffered channel workers read from.
	jobs chan fixtureJob

	// wg tracks active worker goroutines.
	wg sync.WaitGroup

	// inFlight tracks fixture IDs currently being processed by workers.
	// Protected by inFlightMu.
	inFlightMu sync.Mutex
	inFlight   map[string]struct{}

	// Embedded atomic state (Running, counters, LastError).
	uploaderState
}

// New constructs an Uploader. Call Run(ctx) to start the scan loop and workers.
func New(client S3Client, lister SpoolLister, cfg UploaderConfig, log *slog.Logger) (*Uploader, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	return &Uploader{
		client:   client,
		lister:   lister,
		cfg:      cfg,
		log:      log,
		jobs:     make(chan fixtureJob, cfg.Workers*2),
		inFlight: make(map[string]struct{}),
	}, nil
}

// Run starts the worker pool and the scan loop. It blocks until ctx is
// cancelled, at which point it closes the jobs channel, waits for in-flight
// uploads to finish, and returns.
func (u *Uploader) Run(ctx context.Context) {
	u.running.Store(true)
	defer u.running.Store(false)

	u.log.Info("uploader started",
		"bucket", u.cfg.Bucket,
		"region", u.cfg.Region,
		"prefix", u.cfg.Prefix,
		"workers", u.cfg.Workers,
		"scan_interval", u.cfg.ScanInterval,
	)

	// Start worker pool.
	for i := 0; i < u.cfg.Workers; i++ {
		u.wg.Add(1)
		go u.worker(ctx, i)
	}

	// Immediate first scan, then tick at ScanInterval.
	u.scan(ctx)

	ticker := time.NewTicker(u.cfg.ScanInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			u.log.Info("uploader shutting down, draining in-flight uploads")
			close(u.jobs)
			u.wg.Wait()
			u.log.Info("uploader stopped",
				"uploads_completed", u.UploadsCompleted(),
				"uploads_failed", u.UploadsFailed(),
				"bytes_uploaded", u.BytesUploaded(),
			)
			return
		case <-ticker.C:
			u.scan(ctx)
		}
	}
}

// scan lists committed fixtures from the spool and dispatches new ones (not
// already in-flight) to the worker pool. Fixtures are sent to the jobs channel
// on a best-effort basis — if the channel is full, the fixture is skipped and
// will be picked up on the next scan.
func (u *Uploader) scan(ctx context.Context) {
	fixtures, err := u.lister.List()
	if err != nil {
		u.log.Error("uploader scan: failed to list spool", "error", err)
		u.setLastError("scan: " + err.Error())
		return
	}

	dispatched := 0
	for _, fi := range fixtures {
		if ctx.Err() != nil {
			return
		}
		if u.isInFlight(fi.FixtureID) {
			continue
		}
		u.markInFlight(fi.FixtureID)
		select {
		case u.jobs <- fixtureJob{info: fi}:
			dispatched++
		default:
			// Channel full — worker pool is saturated. Remove from in-flight
			// so it can be picked up next scan.
			u.clearInFlight(fi.FixtureID)
		}
	}

	if dispatched > 0 {
		u.log.Debug("uploader scan dispatched jobs",
			"dispatched", dispatched,
			"total_fixtures", len(fixtures),
		)
	}
}

// isInFlight checks whether a fixture ID is currently being processed.
func (u *Uploader) isInFlight(id string) bool {
	u.inFlightMu.Lock()
	defer u.inFlightMu.Unlock()
	_, ok := u.inFlight[id]
	return ok
}

// markInFlight adds a fixture ID to the in-flight set.
func (u *Uploader) markInFlight(id string) {
	u.inFlightMu.Lock()
	defer u.inFlightMu.Unlock()
	u.inFlight[id] = struct{}{}
}

// clearInFlight removes a fixture ID from the in-flight set.
func (u *Uploader) clearInFlight(id string) {
	u.inFlightMu.Lock()
	defer u.inFlightMu.Unlock()
	delete(u.inFlight, id)
}
