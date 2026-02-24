# syntax=docker/dockerfile:1

# ── Build stage ──────────────────────────────────────────────────────────────
FROM golang:1.22-alpine AS builder

WORKDIR /src

# Cache dependency downloads separately from source compilation.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/record-agent ./cmd/record-agent

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM scratch

COPY --from=builder /out/record-agent /record-agent

ENTRYPOINT ["/record-agent"]
