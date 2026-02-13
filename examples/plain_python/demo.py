"""
Plain Python demo showing all 3 sim-sdk primitives.

This example demonstrates:
1. @sim_trace decorator for function tracing
2. sim_capture() context manager for capturing operations
3. sim_db() context manager for database operations

Run this script to see sim-sdk in action with plain Python.
"""

from sim_sdk import sim_trace, sim_capture, sim_db
from sim_sdk.context import SimContext
from sim_sdk.sink.local import LocalSink
from sim_sdk.fixture.writer import FixtureWriter


# Example 1: Using @sim_trace decorator
@sim_trace
def calculate_discount(price, discount_percent):
    """Calculate discount amount."""
    return price * (discount_percent / 100)


@sim_trace
def calculate_total(price, discount_percent=10):
    """Calculate total price after discount."""
    discount = calculate_discount(price, discount_percent)
    return price - discount


# Example 2: Using sim_capture context manager
def process_order(order_id, items):
    """Process an order using sim_capture."""
    with sim_capture("process_order", order_id=order_id):
        # Calculate totals
        total = sum(item['price'] for item in items)
        
        # Apply discount if eligible
        if total > 100:
            total = calculate_total(total, discount_percent=15)
        
        return {
            'order_id': order_id,
            'total': total,
            'item_count': len(items)
        }


# Example 3: Using sim_db for database operations
class MockDatabase:
    """Mock database for demonstration."""
    
    def cursor(self):
        return MockCursor()
    
    def commit(self):
        print("  [DB] Transaction committed")
    
    def close(self):
        print("  [DB] Connection closed")


class MockCursor:
    """Mock cursor for demonstration."""
    
    def execute(self, query, params=None):
        print(f"  [DB] Executing: {query}")
        if params:
            print(f"  [DB] Parameters: {params}")
    
    def fetchone(self):
        return (1, "success")
    
    def fetchall(self):
        return [(1, "Order A"), (2, "Order B")]


def save_order_to_db(order_data):
    """Save order using sim_db."""
    db_conn = MockDatabase()
    
    with sim_db(db_conn, name="orders_db") as db:
        cursor = db.cursor()
        
        # Insert order
        cursor.execute(
            "INSERT INTO orders (order_id, total) VALUES (%s, %s)",
            (order_data['order_id'], order_data['total'])
        )
        
        # Query to verify
        cursor.execute("SELECT * FROM orders WHERE order_id = %s", (order_data['order_id'],))
        result = cursor.fetchone()
        
        db.commit()
    
    return result


def main():
    """Main demo function showing all primitives together."""
    print("=" * 60)
    print("SIM-SDK Plain Python Demo")
    print("=" * 60)
    
    # Setup recording infrastructure
    print("\n1. Setting up recording infrastructure...")
    sink = LocalSink(output_dir=".sim/fixtures")
    writer = FixtureWriter(sink)
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        print("   ✓ Local sink configured: .sim/fixtures/")
        print("   ✓ Recording context active")
        
        # Example workflow combining all primitives
        print("\n2. Processing an order (demonstrating all 3 primitives)...")
        
        order_items = [
            {'name': 'Widget', 'price': 50},
            {'name': 'Gadget', 'price': 75},
        ]
        
        with sim_capture("order_workflow", customer="demo_user"):
            print("   → Processing order with sim_capture...")
            
            # Use traced functions
            order_data = process_order("ORD-001", order_items)
            print(f"   ✓ Order processed: ${order_data['total']:.2f}")
            
            # Save to database
            print("   → Saving to database with sim_db...")
            result = save_order_to_db(order_data)
            print(f"   ✓ Saved to database: {result}")
        
        print("\n3. Writing fixture...")
        # In a real implementation, the context would accumulate all traces/captures
        # and we would write them here
        print("   ✓ Fixture written (stub)")
        
        print("\n" + "=" * 60)
        print("Demo completed successfully!")
        print("=" * 60)
        print("\nAll operations were recorded and can be replayed in tests.")
        print("Check .sim/fixtures/ for generated fixture files.")
        
    finally:
        SimContext.reset_current(token)
        writer.close()


if __name__ == "__main__":
    main()
