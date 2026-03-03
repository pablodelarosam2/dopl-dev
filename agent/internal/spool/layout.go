package spool

import (
	"path/filepath"
	"strings"
)

// fixtureFileName is the well-known name for the fixture payload file inside
// every committed (or in-flight) fixture directory.
const fixtureFileName = "fixture.json"

// tmpSuffix marks a fixture directory as an in-flight write that has not yet
// been atomically committed.
const tmpSuffix = ".tmp"

// FixtureDir returns the path to a committed fixture directory.
//
//	{spoolDir}/{fixtureID}
func FixtureDir(spoolDir, fixtureID string) string {
	return filepath.Join(spoolDir, fixtureID)
}

// TempFixtureDir returns the path to the temporary staging directory used
// during an atomic write. Once the fixture.json is fully written and closed
// inside this directory, it is renamed to FixtureDir to commit it.
//
//	{spoolDir}/{fixtureID}.tmp
func TempFixtureDir(spoolDir, fixtureID string) string {
	return filepath.Join(spoolDir, fixtureID+tmpSuffix)
}

// FixtureFilePath returns the full path to the fixture.json file inside a
// fixture directory (committed or temporary).
//
//	{dir}/fixture.json
func FixtureFilePath(dir string) string {
	return filepath.Join(dir, fixtureFileName)
}

// IsTempDirName reports whether name (a directory entry name, not a full path)
// looks like an in-flight staging directory created by the spool writer.
func IsTempDirName(name string) bool {
	return strings.HasSuffix(name, tmpSuffix)
}

// SanitizeFixtureID validates that id is safe for use as a single path
// component. It rejects empty strings, overly long values, and any character
// outside the [a-zA-Z0-9_-] allowlist. This prevents directory-traversal
// attacks and filesystem-unsafe names.
func SanitizeFixtureID(id string) error {
	return validateID(id, "fixture_id")
}
