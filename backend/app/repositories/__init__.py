"""Data access for core-domain tables.

Functions take a Session, flush but never commit (the caller owns the
transaction), and are idempotent on each table's natural key so workers can
retry safely. Each returns ``(row, created)``.
"""
