# Plain Python Example

This example demonstrates how to use `sim-sdk` with plain Python functions (no web framework required).

## Overview

The demo shows all three sim-sdk primitives:

1. **`@sim_trace`** - Decorator for tracing function calls
2. **`sim_capture()`** - Context manager for capturing operations
3. **`sim_db()`** - Context manager for database operations

## Running the Demo

```bash
# Install sim-sdk
pip install sim-sdk

# Run the demo
python demo.py
```

## What the Demo Does

The demo simulates an order processing workflow:

1. **Calculate prices** using traced functions (`@sim_trace`)
2. **Process order** within a capture block (`sim_capture`)
3. **Save to database** using database capture (`sim_db`)

All operations are recorded and can be replayed in tests.

## Key Concepts

### Function Tracing

```python
@sim_trace
def calculate_discount(price, discount_percent):
    return price * (discount_percent / 100)
```

Automatically records:
- Function arguments
- Return values
- Exceptions (if any)

### Operation Capture

```python
with sim_capture("process_order", order_id=order_id):
    # All operations in this block are captured
    total = calculate_total(items)
    return total
```

Groups related operations together with metadata.

### Database Capture

```python
with sim_db(db_connection, name="orders_db") as db:
    cursor = db.cursor()
    cursor.execute("INSERT INTO orders (order_id, total) VALUES (%s, %s)", data)
```

Records all database queries and results.

## Output

The demo generates fixture files in `.sim/fixtures/` that can be used to replay these operations in tests.

## Next Steps

- Modify the demo to trace your own functions
- Add more complex workflows
- See the Flask example for web framework integration
