package uploader

import (
	"errors"
	"time"
)

// UploaderConfig holds all tunable parameters for the uploader subsystem.
type UploaderConfig struct {
	// Bucket is the target S3 bucket name.
	Bucket string

	// Region is the AWS region for the S3 client (e.g. "us-east-1").
	Region string

	// Prefix is prepended to S3 keys: {Prefix}/{fixtureID}/fixture.json.
	// Empty string means keys start at the root of the bucket.
	Prefix string

	// Workers is the number of concurrent upload goroutines.
	Workers int

	// ScanInterval controls how often the spool directory is scanned for
	// new committed fixtures to upload.
	ScanInterval time.Duration

	// MaxRetries is the maximum number of PutObject attempts per fixture
	// before the fixture is left on disk for the next scan cycle.
	MaxRetries int
}

// Validate returns an error if the config is unusable.
func (c UploaderConfig) Validate() error {
	if c.Bucket == "" {
		return errors.New("uploader: bucket must not be empty")
	}
	if c.Region == "" {
		return errors.New("uploader: region must not be empty")
	}
	if c.Workers <= 0 {
		return errors.New("uploader: workers must be > 0")
	}
	if c.ScanInterval <= 0 {
		return errors.New("uploader: scan interval must be > 0")
	}
	if c.MaxRetries <= 0 {
		return errors.New("uploader: max retries must be > 0")
	}
	return nil
}
