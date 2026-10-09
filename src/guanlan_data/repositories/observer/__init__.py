"""Shared ETF observer host with independently owned data modules."""
import sys

# Python 3.14 otherwise creates new bytecode in the sealed Python 3.12 wheel
# when the public ETF bridge imports it, invalidating update authorization.
# Keep this process-wide: the public session also performs lazy imports.
sys.dont_write_bytecode = True
