"""
Tests for sim_db() context manager using a mock DB object.

Note: These tests use a mock database object, NOT psycopg2 or any real database.
"""

import pytest
from sim_sdk import sim_db
from sim_sdk.context import SimContext


class MockCursor:
    """Mock cursor for testing."""
    
    def __init__(self):
        self.queries = []
        self.results = []
    
    def execute(self, query, params=None):
        self.queries.append((query, params))
    
    def fetchall(self):
        return self.results
    
    def fetchone(self):
        return self.results[0] if self.results else None


class MockConnection:
    """Mock database connection for testing."""
    
    def __init__(self):
        self.cursors = []
    
    def cursor(self):
        cursor = MockCursor()
        self.cursors.append(cursor)
        return cursor
    
    def commit(self):
        pass
    
    def close(self):
        pass


def test_db_without_context():
    """Test that sim_db works without a recording context."""
    
    mock_conn = MockConnection()
    
    with sim_db(mock_conn) as db:
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users")
    
    assert len(mock_conn.cursors) == 1


def test_db_with_context():
    """Test that sim_db wraps connection when context is active."""
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    mock_conn = MockConnection()
    
    try:
        with sim_db(mock_conn, name="test_db") as db:
            cursor = db.cursor()
            cursor.execute("SELECT * FROM users WHERE id = %s", (1,))
            cursor.fetchall()
        
        # TODO: Assert that query was recorded in context
    finally:
        SimContext.reset_current(token)


def test_db_multiple_queries():
    """Test recording multiple queries in a single connection."""
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    mock_conn = MockConnection()
    
    try:
        with sim_db(mock_conn) as db:
            cursor = db.cursor()
            cursor.execute("SELECT * FROM users")
            cursor.execute("INSERT INTO users (name) VALUES (%s)", ("Alice",))
            cursor.execute("UPDATE users SET active = true")
        
        assert len(mock_conn.cursors[0].queries) == 3
        # TODO: Assert all queries were recorded
    finally:
        SimContext.reset_current(token)
