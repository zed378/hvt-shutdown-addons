import os

# Default VM control to the Python client in tests so a kubectl binary on the
# developer's PATH is never invoked. kubectl-specific tests opt in explicitly.
os.environ.setdefault("VM_CONTROL", "api")
