# Flask App Example

This example demonstrates how to use `sim-sdk` with a Flask web application.

## Overview

Shows how to integrate sim-sdk into a Flask app to record:
- HTTP requests/responses
- Database queries
- Function calls
- Business logic operations

## Installation

```bash
# Install dependencies (Flask is NOT part of sim-sdk)
pip install -r requirements.txt
```

## Running the App

```bash
python app.py
```

The app will start on `http://localhost:5000`

## Features

This example demonstrates:

1. **Request tracing** - Recording HTTP requests and responses
2. **Function tracing** - Using `@sim_trace` on business logic
3. **Database capture** - Recording database operations with `sim_db()`
4. **Context management** - Setting up sim context per request

## API Endpoints

Check `app.py` for available endpoints and how they use sim-sdk.

## Configuration

The app can be configured using `sim.yaml` in the project root:

```yaml
recording:
  enabled: true

sink:
  type: local
  path: .sim/fixtures

redaction:
  - "$.password"
  - "$.credit_card"
```

## Key Integration Points

### Per-Request Context

Flask apps should create a sim context per request:

```python
@app.before_request
def setup_sim_context():
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    g.sim_token = token

@app.after_request
def teardown_sim_context(response):
    if hasattr(g, 'sim_token'):
        SimContext.reset_current(g.sim_token)
    return response
```

### Tracing Business Logic

```python
@sim_trace
def process_data(input_data):
    # Business logic here
    return result
```

### Recording Database Queries

```python
with sim_db(get_db_connection()) as db:
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
```

## Testing with Fixtures

Once fixtures are recorded, you can replay them in tests without needing a real database or external services.

## Notes

- Flask is listed in this example's `requirements.txt`, NOT in sim-sdk's dependencies
- sim-sdk is framework-agnostic and can be used with any web framework
- The same principles apply to FastAPI, Django, etc.
